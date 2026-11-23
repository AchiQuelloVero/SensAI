import os
import resource
import subprocess
import sys
import unittest
from unittest import mock

from core.config import SANDBOX_IMAGE, AppConfig
from tools import sandbox

DOCKER_PROBLEM = sandbox.docker_problem(SANDBOX_IMAGE)


def assert_prints_two(test, result):
    test.assertEqual(result["stdout"].strip(), "2", result)
    test.assertEqual(result["returncode"], 0, result)


class SubprocessSandboxTest(unittest.TestCase):
    """The fallback used when Docker is missing."""

    def test_runs_code(self):
        result = sandbox.run_in_subprocess("print(1+1)")
        assert_prints_two(self, result)
        self.assertEqual(result["sandbox"], "subprocess")

    def test_runs_when_the_memory_limit_is_refused(self):
        real_setrlimit = resource.setrlimit

        def refuse_memory_limit(which, limits):
            if which == resource.RLIMIT_AS:
                raise ValueError("current limit exceeds maximum limit")
            return real_setrlimit(which, limits)

        with mock.patch.object(sandbox.resource, "setrlimit", refuse_memory_limit):
            assert_prints_two(self, sandbox.run_in_subprocess("print(1+1)"))

    @unittest.skipIf(sys.platform == "darwin", "macOS refuses the memory limit")
    def test_memory_cap_still_applies(self):
        result = sandbox.run_in_subprocess("data = bytearray(512 * 1024 * 1024)\nprint('allocated')")
        self.assertNotIn("allocated", result["stdout"])
        self.assertIn("MemoryError", result["stderr"])

    def test_blocks_denied_imports(self):
        result = sandbox.run_in_subprocess("import os")
        self.assertIn("blocked", result["stderr"])

    def test_output_printed_before_a_timeout_is_kept(self):
        result = sandbox.run_in_subprocess("print('started', flush=True)\nwhile True: pass")
        self.assertTrue(result["timed_out"])
        self.assertIn("started", result["stdout"])


@unittest.skipIf(DOCKER_PROBLEM, f"Docker sandbox not available: {DOCKER_PROBLEM}")
class DockerSandboxTest(unittest.TestCase):
    """Each escape attempt from issue #83 fails, with a clear message."""

    def run_code(self, code):
        result = sandbox.run_in_docker(code, SANDBOX_IMAGE)
        self.assertEqual(result["sandbox"], "docker")
        return result

    def test_runs_code(self):
        assert_prints_two(self, self.run_code("print(1+1)"))

    def test_os_is_allowed_but_sees_only_the_container(self):
        result = self.run_code('import os\nprint(os.listdir("/home"), __import__("os").getuid())')
        self.assertEqual(result["returncode"], 0, result)
        self.assertNotIn(os.path.basename(os.path.expanduser("~")), result["stdout"])
        self.assertNotEqual(result["stdout"].split()[-1], "0")  # not root

    def test_cannot_read_this_machines_files(self):
        result = self.run_code(f"print(open({os.path.abspath(sandbox.__file__)!r}).read())")
        self.assertIn("FileNotFoundError", result["stderr"])

    def test_cannot_write_outside_tmp(self):
        result = self.run_code('open("/escape.txt", "w").write("x")')
        self.assertIn("Read-only file system", result["stderr"])

    def test_has_no_network(self):
        result = self.run_code('import urllib.request\nurllib.request.urlopen("http://example.com", timeout=3)')
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("URLError", result["stderr"])

    def test_infinite_loop_is_stopped_and_its_container_removed(self):
        result = self.run_code("print('started', flush=True)\nwhile True: pass")
        self.assertTrue(result["timed_out"])
        self.assertIn("started", result["stdout"])
        left = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=sensai-sandbox-"], capture_output=True, text=True
        ).stdout
        self.assertEqual(left.strip(), "")

    def test_memory_bomb_is_killed(self):
        result = self.run_code('data = b"x" * (512 * 1024 * 1024)\nprint("allocated")')
        self.assertNotIn("allocated", result["stdout"])
        self.assertIn("memory limit", result["stderr"])

    def test_fork_bomb_hits_the_process_limit(self):
        result = self.run_code(
            "import os, time\n"
            "count = 0\n"
            "try:\n"
            "    while True:\n"
            "        if os.fork() == 0:\n"
            "            time.sleep(10)\n"
            "            os._exit(0)\n"
            "        count += 1\n"
            "except OSError as error:\n"
            "    print(type(error).__name__, count)\n"
        )
        self.assertIn("BlockingIOError", result["stdout"], result)
        self.assertLess(int(result["stdout"].split()[-1]), sandbox.MAX_PROCESSES)

    def test_a_missing_image_is_explained(self):
        self.assertIn("docker pull", sandbox.docker_problem("sensai-no-such-image:0"))


class SandboxChoiceTest(unittest.TestCase):
    def test_says_clearly_when_it_falls_back(self):
        with mock.patch.object(sandbox, "load_app_config", return_value=AppConfig()), \
             mock.patch.object(sandbox, "docker_problem", return_value="Docker is not installed"):
            result = sandbox.run_python("print(1+1)")
        assert_prints_two(self, result)
        self.assertEqual(result["sandbox"], "subprocess")
        self.assertIn("Docker is not installed", result["note"])

    def test_subprocess_setting_never_uses_docker(self):
        with mock.patch.object(sandbox, "load_app_config", return_value=AppConfig(sandbox="subprocess")), \
             mock.patch.object(sandbox, "docker_problem") as problem:
            result = sandbox.run_python("print(1+1)")
        problem.assert_not_called()
        self.assertEqual(result["sandbox"], "subprocess")
        self.assertNotIn("note", result)


if __name__ == "__main__":
    unittest.main()
