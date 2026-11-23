import ast
import os
import resource
import signal
import subprocess
import tempfile
import time
import uuid

from core.config import load_app_config

TIMEOUT_S = 5
MAX_OUTPUT_CHARS = 4000
MB = 1024 * 1024
MAX_MEMORY_BYTES = 128 * MB
MAX_PROCESSES = 64
# extra time for Docker to start and remove the container, on top of TIMEOUT_S
DOCKER_GRACE_S = 5
# exit codes of the `timeout` command that runs the code inside the container
TIMEOUT_EXIT = 124  # it stopped the code
KILLED_EXIT = 137  # SIGKILL: over the memory limit, or `timeout` had to force-stop the code

DENIED_IMPORTS = {"os", "subprocess", "socket", "shutil", "sys", "ctypes", "multiprocessing"}


def _denied_imports(code):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    denied = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module] if node.module else []
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top in DENIED_IMPORTS:
                denied.add(top)

    return denied


def _limit_resources():
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_S, TIMEOUT_S))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    except (ValueError, OSError):
        # macOS refuses an address-space limit ("current limit exceeds maximum limit"):
        # there the code runs without the memory cap instead of not running at all
        pass


def _text(output):
    # a timeout hands back what was printed so far as bytes, even with text=True
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _truncate(text):
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(text)} chars total)"
    return text


def _add_line(text, line):
    return f"{text.rstrip()}\n{line}" if text.strip() else line


def _result(stdout, stderr, returncode, sandbox, timed_out=False):
    return {
        "stdout": _truncate(_text(stdout)),
        "stderr": _truncate(_text(stderr)),
        "returncode": returncode,
        "timed_out": timed_out,
        "sandbox": sandbox,
    }


def _timed_out(stdout, stderr, sandbox):
    return _result(stdout, _add_line(_text(stderr), f"timed out after {TIMEOUT_S}s"), None, sandbox, timed_out=True)


def docker_problem(image: str) -> str | None:
    """Why the code cannot run in Docker, or None when it can."""
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return "Docker is not installed"
    except subprocess.TimeoutExpired:
        return "Docker did not answer"
    if proc.returncode == 0:
        return None
    if "No such image" in proc.stderr:
        return f"the image {image} is not downloaded (run: docker pull {image})"
    reason = proc.stderr.strip().splitlines()
    return f"Docker is not available: {reason[0] if reason else 'unknown error'}"


def run_in_docker(code: str, image: str) -> dict:
    """Run the code in a throwaway container: no network, no access to this machine's files,
    a read-only system, a normal user, and limits on time, memory, CPU and processes."""
    name = f"sensai-sandbox-{uuid.uuid4().hex[:12]}"
    command = [
        "docker", "run", "--rm", "--interactive", "--name", name, "--pull", "never",
        "--network", "none",
        # nothing from this machine is mounted; only /tmp is writable, in memory, and gone afterwards
        "--read-only", "--tmpfs", "/tmp:rw,size=16m", "--workdir", "/tmp", "--env", "HOME=/tmp",
        "--memory", f"{MAX_MEMORY_BYTES // MB}m", "--memory-swap", f"{MAX_MEMORY_BYTES // MB}m",
        "--cpus", "1", "--pids-limit", str(MAX_PROCESSES),
        "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        image,
        "timeout", "--kill-after", "1", str(TIMEOUT_S), "python", "-",
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            input=code,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S + DOCKER_GRACE_S,
        )
    except subprocess.TimeoutExpired as exc:
        # stopping the docker client leaves the container running: remove it by name
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
        return _timed_out(exc.stdout, exc.stderr, "docker")

    elapsed = time.monotonic() - started
    if proc.returncode in (TIMEOUT_EXIT, KILLED_EXIT) and elapsed >= TIMEOUT_S:
        return _timed_out(proc.stdout, proc.stderr, "docker")
    stderr = proc.stderr
    if proc.returncode == KILLED_EXIT:
        stderr = _add_line(stderr, f"killed: the code went over the {MAX_MEMORY_BYTES // MB} MB memory limit")
    return _result(proc.stdout, stderr, proc.returncode, "docker")


def run_in_subprocess(code: str) -> dict:
    """The fallback without Docker: a separate process with time and memory limits and an
    import filter. It can still read files and use the network: not a real isolation."""
    denied = _denied_imports(code)
    if denied:
        return _result(
            "",
            f"blocked: importing {', '.join(sorted(denied))} is not allowed in the sandbox",
            None,
            "subprocess",
        )

    with tempfile.TemporaryDirectory(prefix="sensai_sandbox_") as scratch:
        script_path = os.path.join(scratch, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        env = {"PATH": "/usr/bin:/bin", "HOME": scratch}

        try:
            proc = subprocess.run(
                ["python3", script_path],
                cwd=scratch,
                env=env,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                preexec_fn=_limit_resources,
            )
        except subprocess.TimeoutExpired as exc:
            return _timed_out(exc.stdout, exc.stderr, "subprocess")

        # the CPU limit stops a busy loop before the wall-clock timeout: SIGKILL on Linux, SIGXCPU on macOS
        if proc.returncode in (-signal.SIGKILL, -signal.SIGXCPU):
            return _timed_out(proc.stdout, proc.stderr, "subprocess")
        return _result(proc.stdout, proc.stderr, proc.returncode, "subprocess")


def run_python(code: str) -> dict:
    # read on each call, so a change in config.json applies to the next run
    config = load_app_config()
    if config.sandbox == "subprocess":
        return run_in_subprocess(code)
    problem = docker_problem(config.sandbox_image)
    if problem is None:
        return run_in_docker(code, config.sandbox_image)
    result = run_in_subprocess(code)
    result["note"] = f"{problem}: ran in a plain subprocess instead, which is not isolated from this machine"
    return result
