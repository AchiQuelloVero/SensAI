import asyncio
import os
import tempfile
import unittest
from functools import partial
from unittest import mock

from core import commands
from core.agent import REPEATED_CALL, AgentRuntime
from core.contracts import ModelResponse
from core.config import load_app_config
from tools import permissions as perms
from tools.executor import ToolExecutor

RULES = {"default": "read", "rules": {"notes": "write", "private": "none"}}


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.ws = os.path.join(self.dir, "ws")
        os.makedirs(os.path.join(self.ws, "notes"))
        os.makedirs(os.path.join(self.ws, "private"))

    def check(self, tool, args):
        return perms.check(tool, args, RULES, self.ws)

    def write_file(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_read_allowed_by_default(self):
        self.assertIsNone(self.check("read_file", {"path": "hello.txt"}))

    def test_write_denied_in_read_only_folder(self):
        self.assertIn("denied", self.check("write_file", {"path": "hello.txt", "content": "hi"}))

    def test_write_allowed_in_nested_write_folder(self):
        self.assertIsNone(self.check("write_file", {"path": "notes/a/b.md", "content": "hi"}))

    def test_no_access_folder(self):
        self.assertIn("denied", self.check("read_file", {"path": "private/secret.txt"}))

    def test_every_path_must_pass(self):
        self.assertIn("denied", self.check("read_multiple_files", {"paths": ["notes/a", "private/b"]}))

    def test_move_across_boundary(self):
        self.assertIn("denied", self.check("move_file", {"source": "notes/a", "destination": "private/a"}))
        self.assertIsNone(self.check("move_file", {"source": "notes/a", "destination": "notes/b"}))

    def test_paths_outside_workspace(self):
        for path in ["../x", "/etc/passwd", "~/x", 123]:
            self.assertIn("outside the workspace", self.check("read_file", {"path": path}))

    def test_symlink_uses_real_location(self):
        os.symlink("/etc", os.path.join(self.ws, "escape"))
        os.symlink("../private", os.path.join(self.ws, "notes", "link"))
        self.assertIn("outside the workspace", self.check("read_file", {"path": "escape/passwd"}))
        self.assertIn("denied", self.check("write_file", {"path": "notes/link/x", "content": "hi"}))

    def test_unknown_path_argument_denied(self):
        self.assertIn("no 'path'", self.check("edit_file", {"file_path": "notes/a.md", "content": ""}))

    def test_executor_marks_denied_without_asking(self):
        asked = []
        tool = {"type": "mcp", "server_id": "filesystem", "original_name": "write_file", "requires_confirmation": True}
        registry = mock.Mock(get=lambda name: tool)
        executor = ToolExecutor(registry, mock.Mock(), on_confirm=lambda n, a: asked.append(n) or ("y", a))
        trace = {}
        with mock.patch("tools.executor.check", partial(perms.check, permissions=RULES, workspace=self.ws)):
            result = asyncio.run(executor.execute("filesystem_write_file", {"path": "private/x"}, trace))
        self.assertIn("Do not retry", result["error"])
        self.assertEqual(trace["decision"], "denied")
        self.assertEqual(asked, [])

    def test_other_tools_not_checked(self):
        self.assertIsNone(self.check("web_search", {"query": "x"}))

    def test_missing_file_allows_everything(self):
        self.assertEqual(perms.load_permissions(os.path.join(self.dir, "nope.json")), {"default": "write", "rules": {}})

    def test_invalid_file(self):
        path = self.write_file("bad.json", '{"rules": [{"path": "x", "access": "all"}]}')
        with self.assertRaises(perms.PermissionsError):
            perms.load_permissions(path)

    def test_save_round_trip(self):
        path = os.path.join(self.dir, "p.json")
        perms.save_permissions({"default": "read", "rules": {"": "write", "notes": "none"}}, path)
        self.assertEqual(perms.load_permissions(path), {"default": "read", "rules": {"": "write", "notes": "none"}})

    def test_command_set_then_check(self):
        path = self.write_file("p.json", '{"default": "read", "rules": []}')
        session, messages = {"persona": None}, [{"role": "system", "content": "old"}]
        with mock.patch.object(commands, "load_permissions", partial(perms.load_permissions, path)), \
             mock.patch.object(commands, "save_permissions", partial(perms.save_permissions, path=path)), \
             mock.patch.object(commands, "set_system_message", lambda s, m, p: m.__setitem__(0, {"role": "system", "content": p})):
            result = commands.run_permissions_command(load_app_config(), session, messages, "set notes write")
        self.assertTrue(result["ok"], result["output"])
        loaded = perms.load_permissions(path)
        self.assertIsNone(perms.check("write_file", {"path": "notes/a.md"}, loaded, self.ws))
        self.assertNotEqual(messages[0]["content"], "old")

    def test_command_adds_note_in_started_chat(self):
        path = self.write_file("p.json", '{"default": "read", "rules": [{"path": "test", "access": "none"}]}')
        messages = [{"role": "system", "content": "old"}, {"role": "user", "content": "create 111 in test"}]
        with mock.patch.object(commands, "load_permissions", partial(perms.load_permissions, path)), \
             mock.patch.object(commands, "save_permissions", partial(perms.save_permissions, path=path)), \
             mock.patch.object(commands, "set_system_message", lambda s, m, p: None):
            commands.run_permissions_command(load_app_config(), {"persona": None}, messages, "set test write")
        self.assertEqual(messages[-1]["role"], "system")
        self.assertIn("test: read and write", messages[-1]["content"])

    def test_command_rejects_bad_input(self):
        config = load_app_config()
        self.assertFalse(commands.run_permissions_command(config, {}, [], "set notes all")["ok"])
        self.assertIn("outside", commands.run_permissions_command(config, {}, [], "set ../x write")["output"])


class RepeatedCallTest(unittest.TestCase):
    def test_same_failed_call_is_not_run_twice(self):
        call = {"function": {"name": "write", "arguments": {"path": "test/111"}}}
        replies = iter([ModelResponse("", [call]), ModelResponse("", [call]), ModelResponse("cannot do it")])
        model = mock.Mock(chat=lambda **kw: next(replies))
        runs = []

        async def execute(name, args, trace):
            runs.append(name)
            return {"error": "denied"}

        agent = AgentRuntime(model, mock.Mock(to_ollama_tools=lambda: []), mock.Mock(execute=execute))
        messages = []
        asyncio.run(agent.run_turn(messages, "create 111 in test"))
        self.assertEqual(len(runs), 1)
        self.assertIn(REPEATED_CALL, messages[-2]["content"])
        self.assertEqual(messages[-2]["call"]["decision"], "skipped")


if __name__ == "__main__":
    unittest.main()
