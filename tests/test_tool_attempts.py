import asyncio
import unittest
from unittest import mock

from core.agent import AgentRuntime, describe_result
from core.contracts import ModelResponse


def run_call(code):
    return {"function": {"name": "run_python", "arguments": {"code": code}}}


def script(returncode, stderr=""):
    return {"result": {"stdout": "", "stderr": stderr, "returncode": returncode, "timed_out": False, "sandbox": "docker"}}


def run_turn(calls, results, max_tool_attempts=3):
    replies = iter([ModelResponse("", [call]) for call in calls] + [ModelResponse("done")])
    model = mock.Mock(chat=lambda **kw: next(replies))
    outcomes = iter(results)
    runs = []

    async def execute(name, args, trace):
        runs.append(args["code"])
        return next(outcomes)

    agent = AgentRuntime(
        model, mock.Mock(to_ollama_tools=lambda: []), mock.Mock(execute=execute),
        max_tool_rounds=10, max_tool_attempts=max_tool_attempts,
    )
    messages = []
    asyncio.run(agent.run_turn(messages, "run it"))
    tools = [m for m in messages if m["role"] == "tool"]
    return runs, tools


class ToolAttemptsTest(unittest.TestCase):
    def test_retries_are_numbered_and_stopped_at_the_maximum(self):
        failing = script(1, "NameError: name 'x' is not defined")
        runs, tools = run_turn([run_call(f"try {n}") for n in range(4)], [failing] * 3)
        self.assertEqual(len(runs), 3)
        self.assertEqual([t["call"].get("attempt") for t in tools], [None, 2, 3, None])
        self.assertEqual(tools[1]["call"]["max_attempts"], 3)
        self.assertEqual(tools[-1]["call"]["decision"], "skipped")
        self.assertIn("3 times in a row", tools[-1]["content"])

    def test_a_success_starts_the_count_again(self):
        runs, tools = run_turn(
            [run_call(f"try {n}") for n in range(4)],
            [script(1, "Error"), script(0), script(1, "Error"), script(0)],
        )
        self.assertEqual(len(runs), 4)
        self.assertEqual([t["call"].get("attempt") for t in tools], [None, 2, None, 2])

    def test_a_failing_script_counts_as_failed(self):
        ok, summary = describe_result(script(1, "Traceback (most recent call last):\nNameError: name 'x' is not defined"))
        self.assertFalse(ok)
        self.assertEqual(summary, "NameError: name 'x' is not defined · docker")
        self.assertEqual(describe_result(script(0)), (True, "exit 0 · docker"))


if __name__ == "__main__":
    unittest.main()
