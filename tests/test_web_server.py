import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from core.config import AppConfig
from core.contracts import ModelResponse
from storage.session_store import save_session, session_path_for
from web import server

CHAT = "20261110_120000"


def fake_client(model, *args):
    return SimpleNamespace(model=model, context_window=0, check_model_available=lambda: None)


class WebStateTest(unittest.TestCase):
    """The server's state for one saved chat, with no model, Ollama or config.json involved."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = session_path_for(tmp.name, CHAT)
        session = {"id": CHAT, "model": "qwen3:4b", "persona": None, "prompt_version": None}
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        save_session(self.path, session, messages)

        state = server.app.state
        state.config = AppConfig(model="qwen3:4b", sessions_dir=tmp.name, log_file=os.path.join(tmp.name, "calls.jsonl"))
        state.session, state.messages, state.session_path = session, messages, self.path
        state.model, state.prompt_version = "qwen3:4b", None
        state.agent = SimpleNamespace(model=fake_client("qwen3:4b"))
        state.guard_config = None
        state.hallucination_config = {"enabled": False}
        state.lock = asyncio.Lock()
        state.emit = None
        for name, value in (("make_model_client", fake_client), ("save_app_config", lambda cfg: None)):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def saved_model(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["model"]


class ModelPerChatTest(WebStateTest):
    def test_a_model_chosen_in_settings_comes_back_with_the_chat(self):
        asyncio.run(server.patch_config(server.ConfigUpdate(model="openrouter/openai/gpt-oss-20b:free")))
        self.assertEqual(self.saved_model(), "openrouter/openai/gpt-oss-20b:free")

        server.app.state.model = "gemma3:4b"  # another chat was opened meanwhile
        view = asyncio.run(server.resume_session(CHAT))
        self.assertEqual(view["model"], "openrouter/openai/gpt-oss-20b:free")
        self.assertEqual(server.app.state.agent.model.model, "openrouter/openai/gpt-oss-20b:free")

    def test_an_unavailable_model_changes_nothing(self):
        def missing(model, *args):
            def check():
                raise server.OllamaError(f"Model '{model}' is not installed. Run: ollama pull {model}")
            return SimpleNamespace(model=model, check_model_available=check)

        with mock.patch.object(server, "make_model_client", missing):
            with self.assertRaises(server.HTTPException) as raised:
                asyncio.run(server.patch_config(server.ConfigUpdate(model="llama9:1b")))
        self.assertIn("ollama pull llama9:1b", raised.exception.detail)
        self.assertEqual(server.app.state.model, "qwen3:4b")
        self.assertEqual(self.saved_model(), "qwen3:4b")


class ToolCallEventTest(WebStateTest):
    def test_a_masked_tool_result_keeps_the_tool_name(self):
        result = json.dumps({"result": {"stdout": "contact: ana@example.com\n", "stderr": "", "returncode": 0}})

        async def run_turn(messages, user_input, on_text_chunk=None, on_tool_call=None, **kw):
            trace = {"arguments": {"code": "print(...)"}, "decision": "approved"}
            messages += [
                {"role": "user", "content": user_input},
                {"role": "tool", "tool_name": "run_python", "content": result, "call": trace},
                {"role": "assistant", "content": "done"},
            ]
            on_tool_call("run_python", trace, result)
            on_text_chunk("done")
            return ModelResponse("done")

        state = server.app.state
        state.agent.run_turn = run_turn
        state.agent.answer_store = None
        state.guard_config = {"pii": {"EMAIL": "mask"}, "blocked_message": "Sorry"}
        allow = {"action": "allow", "category": None, "pii": {}, "text": "run it"}

        async def events():
            response = await server.chat(server.ChatRequest(message="run it"))
            return "".join([chunk async for chunk in response.body_iterator])

        with mock.patch.object(server, "guard_input", lambda *a: allow), \
             mock.patch.object(server, "guard_output", lambda *a: None):
            stream = asyncio.run(events())
        call = next(json.loads(block.split("data: ", 1)[1]) for block in stream.split("\n\n") if block.startswith("event: tool_call"))
        self.assertEqual(call["name"], "run_python")
        self.assertNotIn("ana@example.com", call["result"])


if __name__ == "__main__":
    unittest.main()
