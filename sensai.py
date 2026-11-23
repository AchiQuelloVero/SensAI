import argparse
import asyncio
import signal
import sys
import time

import anyio

from export.publish import publish_session
from export.service import export_saved_session
from core.config import DEFAULT_MODEL, DEFAULT_PERSONA, HOST
from model.ollama import OllamaError
from app import create_agent
from cache.service import format_seconds
from call_tracking.call_log import log_call, log_tool_call
from core.commands import dispatch_command, run_edit_command
from core.config import load_app_config
from guardrails.config import GuardrailError, load_config
from guardrails.guard import guard_input, guard_output
from guardrails.output import StreamMasker, mask_messages, turn_answer, types_to_hide, withdraw_answer
from hallucination.check import check_answer, log_record, problem, regenerate
from hallucination.config import HallucinationError, judge_model
from hallucination.config import load_config as load_hallucination_config
from model.ollama import OllamaError
from prompting.persona import PersonaError, guard_config_for, session_persona
from prompting.prompt_builder import PromptError
from rag.errors import RagError
from storage.session_store import (
    SessionError,
    get_session_summaries,
    initialize_session,
    save_session,
)
from structured import StructuredOutputError


def parse_args():
    cfg = load_app_config()
    parser = argparse.ArgumentParser(
        description="Sensai: chat with a local Ollama model."
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help=f"Ollama model to use (default: {cfg.model}, or the saved model when resuming)",
    )
    parser.add_argument(
        "--persona",
        default=cfg.persona,
        help=f"persona from personas/ to start with (default: {cfg.persona}; ignored when resuming)",
    )
    parser.add_argument(
        "-s", "--system-prompt",
        default=cfg.system_prompt_path,
        help="path to the system prompt file",
    )
    parser.add_argument(
        "-d", "--sessions-dir",
        default=cfg.sessions_dir,
        help="directory where sessions are saved",
    )
    parser.add_argument(
        "-p", "--profile",
        default=cfg.profile_path,
        help="path to the user profile file",
    )
    parser.add_argument(
        "-r", "--resume",
        metavar="ID",
        help="resume a saved session",
    )
    parser.add_argument(
        "-l", "--log-file",
        default=cfg.log_file,
        help="path to the call log",
    )
    parser.add_argument(
        "-y", "--auto-approve",
        action="store_true",
        default=cfg.auto_approve,
        help="run tools that require confirmation without asking",
    )
    parser.add_argument(
        "--no-guardrails",
        action="store_true",
        default=cfg.no_guardrails,
        help="send messages to the model without the safety and privacy checks",
    )
    parser.add_argument(
        "--export",
        metavar="ID",
        help="export a saved conversation by session ID",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown", "pdf"),
        default="json",
        help="export format (default: json)",
    )
    parser.add_argument(
        "--publish",
        metavar="ID",
        help="publish a saved conversation as a read-only link (served by the web UI at /s/<token>)",
    )

    return parser.parse_args()

def handle_export(args):
    try:
        output_path = export_saved_session(
            session_id=args.export,
            sessions_dir=args.sessions_dir,
            output_format=args.format,
        )
    except (SessionError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Session exported to {output_path}")
    return 0

def handle_publish(args):
    try:
        token = publish_session(args.publish, args.sessions_dir)
    except (SessionError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Published: /s/{token}")
    return 0

def list_sessions(sessions_dir):
    try:
        sessions = get_session_summaries(sessions_dir)
    except SessionError as error:
        print(f"error: {error}", file=sys.stderr)
        return

    if not sessions:
        print("(no saved sessions)")
        return

    for session_id, updated, model, turns, first_message, *_ in sessions:
        print(
            f'{session_id}  {updated}  {model}  '
            f'{turns} turn(s)  "{first_message}"'
        )

    print("Resume one with: python3 sensai.py --resume <id>")


def display_text_chunk(text: str) -> None:
    print(text, end="", flush=True)


TTY = sys.stdout.isatty()
DIM, YELLOW, RESET = ("\033[2m", "\033[33m", "\033[0m") if TTY else ("", "", "")


def tool_label(name, arguments) -> str:
    args = ", ".join(f"{key}={value!r}" for key, value in arguments.items()) if isinstance(arguments, dict) else repr(arguments)
    label = f"{name}({args})"
    return label if len(label) <= 80 else label[:77] + "..."


class ToolDisplay:
    def __init__(self, write_text):
        self.write_text = write_text
        self.flush_text = lambda: None
        self.label = ""
        self.after_tool = False

    def text(self, chunk):
        if self.after_tool and chunk:
            self.after_tool = False
        self.write_text(chunk)

    def tool(self, event):
        self.flush_text()
        if event["phase"] == "start":
            self.label = tool_label(event["name"], event["arguments"])
            if event.get("attempt"):
                self.label += f" · attempt {event['attempt']} of {event['max_attempts']}"
            if not self.after_tool:
                print()
            if TTY:
                print(f"{DIM}  ⟳ {self.label}{RESET}", end="", flush=True)
        else:
            mark = "✓" if event["ok"] else "✗"
            if event.get("decision") == "edited":
                self.label = tool_label(event["name"], event["arguments"]) + " · edited"
            # \r overwrites the ⟳ line
            print(f"\r{DIM}  {mark} {self.label} · {event['summary']} ({event['duration_s']}s){RESET}")
            self.after_tool = True


def coerce_like(raw: str, original):
    if isinstance(original, bool):
        return raw.strip().lower() in ("true", "1", "yes", "y")
    if isinstance(original, int):
        try:
            return int(raw)
        except ValueError:
            return raw
    if isinstance(original, float):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def edit_arguments(arguments: dict) -> dict:
    edited = dict(arguments)
    print("Press enter to keep the current value.")
    for key, value in arguments.items():
        raw = input(f"  {key} [{value!r}]: ").strip()
        if raw:
            edited[key] = coerce_like(raw, value)
    return edited


def make_tool_confirmer(log_file: str, auto_approve: bool):
    def confirm_tool_call(name, arguments):
        if auto_approve:
            log_call(log_file, {"tool": name, "arguments": arguments, "decision": "auto-approved"})
            return "y", arguments

        print(f"\nTool call: {name}({arguments})")
        while True:
            choice = input("Run this? [y]es / [n]o / [e]dit > ").strip().lower()
            if choice in ("y", "yes", ""):
                log_call(log_file, {"tool": name, "arguments": arguments, "decision": "approved"})
                return "y", arguments
            if choice in ("n", "no"):
                log_call(log_file, {"tool": name, "arguments": arguments, "decision": "refused"})
                return "n", arguments
            if choice in ("e", "edit"):
                arguments = edit_arguments(arguments)
                log_call(log_file, {"tool": name, "arguments": arguments, "decision": "edited"})
                return "y", arguments
            print("please answer y, n or e")

    return confirm_tool_call


def print_welcome(model, persona):
    print(
        f"Sensai ready with {model}, persona: {persona or 'none'}. "
        "Type /sessions to list sessions, /stats for call stats, "
        "/json for JSON replies, /prompt for prompt versions, /persona to switch persona, "
        "/edit <n> <text> to fork a past message, /branches to list branches, "
        "/ingest, /docs and /rag [on|off] for your documents, "
        "/cache [on|off|clear] for saved answers, "
        "/permissions to see or change file access, "
        "/exit to quit."
    )


def print_resume_info(session, messages):
    previous_turns = sum(
        message["role"] == "user" for message in messages
    )
    print(
        f"Resumed session {session['id']} "
        f"({previous_turns} previous turn(s))."
    )
    _, problem = session_persona(session, DEFAULT_PERSONA)
    if problem:
        print(f"warning: this chat's persona can't be loaded: {problem}", file=sys.stderr)


def print_command_result(result: dict) -> None:
    output = result.get("output")
    if not output:
        return
    stream = sys.stderr if "error" in result else sys.stdout
    print(output, file=stream)
    for note in result.get("notes", []):
        print(f"{DIM}  {note}{RESET}")


def apply_edit(session, messages, text):
    result = run_edit_command(session, messages, text)
    print_command_result(result)
    if not result["ok"]:
        return None
    return result["new_content"]


def print_ingest_progress(status, source):
    # the summary counts unchanged files and gives the reason for each failure
    if status in ("added", "updated", "removed"):
        print(f"  {status:<9} {source}", flush=True)


def handle_command(user_input, config, model, messages, session, prompt_state, guard_config):
    if user_input == "/sessions":
        list_sessions(config.sessions_dir)
        return True

    try:
        result = dispatch_command(
            user_input,
            config=config,
            model=model,
            messages=messages,
            session=session,
            prompt_state=prompt_state,
            guard_config=guard_config,
            on_progress=print_ingest_progress,
        )
    except KeyboardInterrupt:
        print("\n(cancelled)")
        return True

    if result is None:
        return False

    if result.get("input_pii"):
        print(f"{DIM}  (masked before sending: {pii_summary(result['input_pii'])}, your rules mask these){RESET}")
    print_command_result(result)
    return True


def apply_input_guardrails(model, user_input, args, guard_config):
    decision = guard_input(model, user_input, guard_config)
    log_call(
        args.log_file,
        {"guardrail": "input", **{k: v for k, v in decision.items() if k != "text"}},
    )

    if decision["action"] == "block":
        print(f"{guard_config['blocked_message']}")
        print(f"{DIM}  ✗ blocked: {decision['category']}, {decision['reason']}{RESET}")
        return None

    if decision["pii"]:
        print(f"{DIM}  (masked before sending: {pii_summary(decision['pii'])}, your rules mask these){RESET}")
    return decision["text"]


def pii_summary(counts):
    return ", ".join(f"{name} ×{n}" if n > 1 else name for name, n in counts.items())


async def apply_output_guardrails(model, messages, turn_start, log_file, guard_config, answer_store=None):
    """Withdraw the answer if the output check refuses it. Returns True when it was withdrawn."""
    if TTY:
        print(f"{DIM}  ⟳ checking the answer is safe...{RESET}", end="", flush=True)
    start = time.perf_counter()
    try:
        decision = await asyncio.to_thread(
            guard_output, model, messages[turn_start]["content"], turn_answer(messages, turn_start), guard_config,
        )
    except KeyboardInterrupt:
        decision = {"action": "block", "category": "check_failed", "reason": "cancelled"}
    finally:
        if TTY:
            print("\r\033[K", end="", flush=True)
    if decision is None:
        return False
    log_call(log_file, {"guardrail": "output", **decision, "duration_s": round(time.perf_counter() - start, 2)})
    if decision["action"] == "allow":
        return False
    forgotten = await asyncio.to_thread(withdraw_answer, messages, turn_start, guard_config, decision, answer_store)
    print(f"{YELLOW}  ✗ answer withdrawn: {decision['category']}, {decision['reason']}{RESET}")
    if forgotten:
        print(f"{DIM}  (also removed from the saved answers){RESET}")
    print(f"{model}> {guard_config['blocked_message']}")
    return True


# contradicted, unsupported, and supported by the evidence but not by the samples
CLAIM_MARKS = {"contradicted": "✗", "unsupported": "?", "supported": "≠"}


def print_check(result):
    if not result.verdicts:
        print(f"{DIM}  ✓ no factual claims to check ({result.duration_s}s){RESET}")
        return
    if not result.flagged:
        print(f"{DIM}  ✓ {len(result.verdicts)} claim(s) checked, all supported ({result.duration_s}s){RESET}")
        return

    print(f"{YELLOW}  ⚠ {len(result.flagged)} of {len(result.verdicts)} claim(s) not supported ({result.duration_s}s):{RESET}")
    for verdict in result.flagged:
        score = verdict.get("consistency", verdict)["confidence"]
        print(f"{YELLOW}    {CLAIM_MARKS[verdict['verdict']]} {verdict['claim']}{RESET}")
        print(f"{DIM}      {problem(verdict)} (confidence {score:.2f}){RESET}")


async def run_check(agent, judge, config, messages, turn_start, response, log_file):
    """The check of the answer ending `messages`, or None when it could not run."""
    if TTY:
        print(f"{DIM}  ⟳ checking the answer...{RESET}", end="", flush=True)
    try:
        return await asyncio.to_thread(check_answer, agent.model, judge, config, messages, turn_start, response)
    except (StructuredOutputError, OllamaError, KeyboardInterrupt) as error:
        reason = str(error) if not isinstance(error, KeyboardInterrupt) else "cancelled"
        print(f"\r\033[K{DIM}  (answer not checked: {reason}){RESET}" if TTY else f"  (answer not checked: {reason})")
        log_call(log_file, {"hallucination": "check", "judge_model": judge, "error": reason})
        return None
    finally:
        if TTY:
            print("\r\033[K", end="", flush=True)


async def answer_again(agent, model, response, answer, flagged, hide_types):
    """The answer written again with the judge's feedback, or None when it failed."""
    print(f"{DIM}  ↻ answering again with the fact-checker's feedback{RESET}")
    print(f"{model}> ", end="", flush=True)
    masker = StreamMasker(hide_types, display_text_chunk) if hide_types else None
    try:
        try:
            new = await asyncio.to_thread(
                regenerate, agent.model, response, answer, flagged, masker or display_text_chunk,
            )
        finally:
            if masker is not None:
                masker.flush()
        print()
        return new.content
    except OllamaError as error:
        print(f"\nerror: {error}", file=sys.stderr)
    except KeyboardInterrupt:
        print()
    print(f"{DIM}  (keeping the first answer){RESET}")
    return None


async def check_turn(agent, messages, turn_start, response, model, log_file, config, hide_types):
    """Check the answer after it was streamed, print the verdict below it, act on
    unsupported claims (warn, regenerate once or refuse) and log the check."""
    judge = judge_model(config, model)
    first = await run_check(agent, judge, config, messages, turn_start, response, log_file)
    if first is None:
        return
    print_check(first)

    action = config["action"] if first.flagged else "none"
    final, regenerated = first, None
    if action == "refuse":
        messages[-1]["content"] = config["refusal_message"]
        print(f"{YELLOW}  ✗ answer withdrawn{RESET}")
        print(f"{model}> {config['refusal_message']}")
    elif action == "regenerate":
        new_answer = await answer_again(agent, model, response, messages[-1]["content"], first.flagged, hide_types)
        if new_answer is not None:
            messages[-1]["content"] = new_answer
            if hide_types:
                mask_messages(messages[-1:], hide_types)
            regenerated = await run_check(agent, judge, config, messages, turn_start, response, log_file)
            if regenerated is not None:
                print_check(regenerated)
                final = regenerated

    # kept in the session, so later checks do not take these claims as evidence
    if final.flagged and action != "refuse":
        messages[-1]["check"] = {"action": action, "flagged": [v["claim"] for v in final.flagged]}
    log_call(log_file, log_record(first, judge, action, regenerated))


async def run_chat_turn(agent, messages, user_input, model, args, session, session_path, guard_config, prompt_state,
                        hallucination_config=None):
    if guard_config is not None:
        try:
            user_input = apply_input_guardrails(model, user_input, args, guard_config_for(session, guard_config))
        except KeyboardInterrupt:
            print("\n(interrupted, message discarded)")
            return
        if user_input is None:
            return

    turn_start = len(messages)
    start = time.perf_counter()
    masker = None
    display = ToolDisplay(display_text_chunk)
    on_text_chunk = display.text
    if guard_config is not None:
        masker = StreamMasker(types_to_hide(guard_config), display.text)
        display.flush_text = masker.flush
        on_text_chunk = masker

    try:
        print(f"{model}> ", end="", flush=True)

        try:
            response = await agent.run_turn(
                messages=messages,
                user_input=user_input,
                on_text_chunk=on_text_chunk,
                on_tool_event=display.tool,
                on_tool_call=lambda name, trace, content: log_tool_call(
                    args.log_file, name, trace, content, session.get("id")),
            )
        finally:
            if masker is not None:
                masker.flush()
        print()
        if response.cached:
            took, saved = format_seconds(response.stats["total_duration_s"]), format_seconds(response.saved_s)
            print(f"{DIM}  (answered from cache in {took}, saved {saved}){RESET}")
        elif response.cache_note:
            print(f"{DIM}  (cache: {response.cache_note}){RESET}")

    except (OllamaError, RagError, RuntimeError) as error:
        del messages[turn_start:]
        print(f"\nerror: {error}", file=sys.stderr)

        log_call(
            args.log_file,
            {
                "model": model,
                "prompt_version": prompt_state["version"],
                "error": str(error),
                "total_duration_s": time.perf_counter() - start,
            },
        )
        return

    except KeyboardInterrupt:
        del messages[turn_start:]
        messages.append({"role": "user", "content": user_input, "interrupted": True})
        save_session(session_path, session, messages)
        print("\n(interrupted: your message was kept, the reply was cut off)")
        return

    if masker is not None:
        mask_messages(messages[turn_start:], masker.types)
        if masker.counts:
            log_call(args.log_file, {"guardrail": "output", "action": "mask", "pii": masker.counts})
            print(f"{DIM}  (hidden in the answer: {pii_summary(masker.counts)}, your rules mask these){RESET}")

    withdrawn = False
    if guard_config is not None:
        withdrawn = await apply_output_guardrails(
            model, messages, turn_start, args.log_file, guard_config, agent.answer_store,
        )

    if hallucination_config is not None and hallucination_config["enabled"] and not response.cached and not withdrawn:
        answer = messages[-1]["content"]
        await check_turn(
            agent, messages, turn_start, response, model, args.log_file, hallucination_config,
            masker.types if masker is not None else None,
        )
        if guard_config is not None and messages[-1]["content"] != answer:
            await apply_output_guardrails(model, messages, turn_start, args.log_file, guard_config, agent.answer_store)

    save_session(session_path, session, messages)

    if not response.cached:
        log_call(args.log_file, {"model": model, "prompt_version": prompt_state["version"], **response.stats})


async def main():
    # asyncio's runner turns Ctrl+C into a task cancellation, which leaves input()
    # blocked and later cancels the MCP cleanup; raise KeyboardInterrupt instead.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    args = parse_args()
    if args.export:
        sys.exit(handle_export(args))
    if args.publish:
        sys.exit(handle_publish(args))
    config = load_app_config(cli_args=args)
    try:
        session, messages, model, session_path, prompt_version = initialize_session(
            config,
            resume_id=args.resume,
            model_override=args.model,
        )
        config.model = model
        prompt_state = {"version": prompt_version}
        guard_config = None if config.no_guardrails else load_config(config.guardrails_config_path)
        hallucination_config = load_hallucination_config(config.hallucination_config_path)
        confirm_tool_call = make_tool_confirmer(config.log_file, config.auto_approve)
        agent, mcp_manager = await create_agent(
            model, on_confirm=confirm_tool_call, config=config, hallucination_config=hallucination_config,
        )
    except (PromptError, PersonaError, SessionError, OllamaError, GuardrailError, HallucinationError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    try:
        if args.resume:
            print_resume_info(session, messages)

        print_welcome(model, session.get("persona"))

        while True:
            try:
                user_input = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not user_input:
                print("(empty message, type something or /exit)")
                continue

            if user_input == "/exit":
                print("Bye.")
                break

            if user_input == "/edit" or user_input.startswith("/edit "):
                edited_text = apply_edit(session, messages, user_input[len("/edit"):].strip())
                if edited_text is None:
                    continue
                user_input = edited_text
            elif handle_command(user_input, config, model, messages, session, prompt_state, guard_config):
                if user_input.split()[:2] in (["/guardrails", "on"], ["/guardrails", "off"]):
                    guard_config = None if config.no_guardrails else load_config(config.guardrails_config_path)
                continue

            await run_chat_turn(
                agent=agent,
                messages=messages,
                user_input=user_input,
                model=model,
                args=args,
                session=session,
                session_path=session_path,
                guard_config=guard_config,
                prompt_state=prompt_state,
                hallucination_config=hallucination_config,
            )
    finally:
        await mcp_manager.disconnect_all()


if __name__ == "__main__":
    anyio.run(main)
