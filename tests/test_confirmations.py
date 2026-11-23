import asyncio
import contextlib
import io
import json
import unittest
from unittest import mock

import sensai
from core.agent import AgentRuntime
from core.contracts import ModelResponse
from hallucination.evidence import tool_output
from tools.executor import ToolExecutor


def run(answer, arguments=None):
    """Call a tool that needs confirmation; `answer` is what the user replies. Returns the result, the trace and what ran."""
    ran = []
    tool = {"type": "builtin", "requires_confirmation": True, "function": lambda **kw: ran.append(kw) or "written"}
    executor = ToolExecutor(mock.Mock(get=lambda name: tool), on_confirm=lambda name, args: answer)
    trace = {}
    result = asyncio.run(executor.execute("save_note", arguments or {"text": "original text"}, trace))
    return result, trace, ran


class ConfirmationTest(unittest.TestCase):
    def test_an_approved_call_runs_as_asked(self):
        result, trace, ran = run(("y", {"text": "original text"}))
        self.assertEqual(result, {"result": "written"})
        self.assertEqual(trace["decision"], "approved")
        self.assertEqual(ran, [{"text": "original text"}])

    def test_an_edited_call_tells_the_model_what_ran(self):
        result, trace, ran = run(("y", {"text": "changed by the user"}))
        self.assertEqual(ran, [{"text": "changed by the user"}])
        self.assertEqual(trace["decision"], "edited")
        self.assertEqual(trace["arguments"], {"text": "changed by the user"})
        self.assertIn('Asked for: {"text": "original text"}. Ran with: {"text": "changed by the user"}.', result["note"])

    def test_a_refused_call_runs_nothing(self):
        result, trace, ran = run(("n", {"text": "original text"}))
        self.assertEqual(ran, [])
        self.assertEqual(trace["decision"], "refused")
        self.assertEqual(result, {"error": "user refused to run this tool"})


class EditedCallDisplayTest(unittest.TestCase):
    def test_the_terminal_and_the_fact_check_see_the_edited_arguments(self):
        tool = {"type": "builtin", "requires_confirmation": True, "function": lambda **kw: "written"}
        executor = ToolExecutor(mock.Mock(get=lambda name: tool), on_confirm=lambda name, args: ("y", {"text": "changed"}))
        replies = iter([
            ModelResponse("", [{"function": {"name": "save_note", "arguments": {"text": "original text"}}}]),
            ModelResponse("done"),
        ])
        agent = AgentRuntime(mock.Mock(chat=lambda **kw: next(replies)), mock.Mock(to_ollama_tools=lambda: []), executor)
        display = sensai.ToolDisplay(lambda text: None)
        messages = []
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            asyncio.run(agent.run_turn(messages, "save it", on_tool_event=display.tool))

        last_line = printed.getvalue().strip().splitlines()[-1]
        self.assertIn("save_note(text='changed') · edited", last_line)
        evidence = tool_output(next(m for m in messages if m["role"] == "tool")["content"])
        self.assertEqual(evidence, 'written\nThe user edited the arguments before approving. '
                                   'Asked for: {"text": "original text"}. Ran with: {"text": "changed"}.')

    def test_a_result_without_a_note_is_unchanged(self):
        self.assertEqual(tool_output(json.dumps({"result": "written"})), "written")
        self.assertEqual(tool_output(json.dumps({"error": "boom"})), "error: boom")


if __name__ == "__main__":
    unittest.main()
