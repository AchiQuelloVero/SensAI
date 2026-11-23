import unittest
from unittest import mock

from core import commands
from core.config import AppConfig
from core.contracts import ModelResponse
from model.openrouter import OpenRouterError
from prompting.versioning import VersionError

VERSIONS = {1: "Answer plainly.", 2: "Answer like a pirate."}


def fake_version(path, version):
    if version not in VERSIONS:
        raise VersionError(f"version {version} not found for {path}")
    return VERSIONS[version]


def compare(model, text="compare 1 2 Say hi", reply=None):
    """Run /prompt compare with fake prompt versions; returns the result, the cloud client and the Ollama call."""
    client = mock.Mock()
    client.return_value.chat.side_effect = reply or (lambda messages, **kw: ModelResponse(f"cloud: {messages[0]['content']}"))
    local = mock.Mock(side_effect=lambda model, messages, *a: (f"local: {messages[0]['content']}", "stop"))
    messages = [{"role": "system", "content": "the chat's prompt"}]
    with mock.patch.object(commands, "get_version", fake_version), \
         mock.patch.object(commands, "OpenRouterClient", client), \
         mock.patch.object(commands, "ask_ollama", local):
        result = commands.run_prompt_command(model, AppConfig(), {}, messages, {}, text)
    return result, client, local, messages


class PromptCompareTest(unittest.TestCase):
    def test_a_cloud_model_answers_through_openrouter(self):
        result, client, local, _ = compare("openrouter/openai/gpt-oss-20b:free")
        local.assert_not_called()
        client.assert_called_once_with("openrouter/openai/gpt-oss-20b:free")
        self.assertEqual([c["reply"] for c in result["comparisons"]],
                         ["cloud: Answer plainly.", "cloud: Answer like a pirate."])
        self.assertEqual(client.return_value.chat.call_args.kwargs["tools"], [])

    def test_a_local_model_still_answers_through_ollama(self):
        result, client, local, _ = compare("qwen3:4b")
        client.assert_not_called()
        self.assertEqual(local.call_count, 2)
        self.assertEqual(result["comparisons"][1]["reply"], "local: Answer like a pirate.")

    def test_a_cloud_error_is_shown_for_that_version(self):
        def fail(messages, **kw):
            raise OpenRouterError("OpenRouter returned HTTP 429: rate limited")
        result, *_ = compare("openrouter/openai/gpt-oss-20b:free", reply=fail)
        self.assertTrue(result["ok"])
        self.assertEqual([c["error"] for c in result["comparisons"]], ["OpenRouter returned HTTP 429: rate limited"] * 2)

    def test_a_missing_version_is_a_clear_error_and_the_chat_is_untouched(self):
        result, client, local, messages = compare("qwen3:4b", "compare 1 99 Say hi")
        self.assertFalse(result["ok"])
        self.assertIn("version 99 not found", result["output"])
        local.assert_not_called()
        self.assertEqual(messages, [{"role": "system", "content": "the chat's prompt"}])


if __name__ == "__main__":
    unittest.main()
