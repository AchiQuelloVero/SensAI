import json
import os
import time
from typing import Any, Callable

import requests

from core.contracts import Message, ModelResponse, ToolDefinition
from model.ollama import OllamaClient, OllamaError

PREFIX = "openrouter/"
API_URL = "https://openrouter.ai/api/v1"


# a subclass, so every place that catches OllamaError handles it too
class OpenRouterError(OllamaError):
    pass


def to_openai_messages(messages: list[Message]) -> list[dict]:
    # Ollama messages have no tool call ids, OpenAI-style APIs need them to pair calls and results
    out = []
    pending = []
    for index, message in enumerate(messages):
        role = message["role"]
        if role == "assistant":
            converted = {"role": "assistant", "content": message.get("content") or ""}
            calls = []
            for number, call in enumerate(message.get("tool_calls") or []):
                function = call.get("function", {})
                arguments = function.get("arguments", {})
                calls.append({
                    "id": call.get("id") or f"call_{index}_{number}",
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                    },
                })
            if calls:
                converted["tool_calls"] = calls
                pending = [call["id"] for call in calls]
            out.append(converted)
        elif role == "tool":
            call_id = pending.pop(0) if pending else f"call_{index}"
            out.append({"role": "tool", "tool_call_id": call_id, "content": message.get("content") or ""})
        else:
            out.append({"role": role, "content": message.get("content") or ""})
    return out


class OpenRouterClient:
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.remote_model = model.removeprefix(PREFIX)
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self._context_window: int | None = None

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not set. Create a key at openrouter.ai/keys and export it.")
        return {"Authorization": f"Bearer {self.api_key}", "X-Title": "Sensai"}

    def _models(self) -> list[dict]:
        try:
            response = requests.get(f"{API_URL}/models", timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OpenRouterError(f"Cannot reach OpenRouter: {exc}") from exc
        return response.json().get("data", [])

    def _model_info(self) -> dict | None:
        model = next((m for m in self._models() if m["id"] == self.remote_model), None)
        if model is not None:
            self._context_window = model.get("context_length") or 0
        return model

    @property
    def context_window(self) -> int:
        if self._context_window is None:
            try:
                self._model_info()
            except OpenRouterError:
                return 0
        return self._context_window or 0

    def check_model_available(self) -> None:
        self._headers()
        model = self._model_info()
        if model is None:
            raise OpenRouterError(f"OpenRouter has no model '{self.remote_model}'. See openrouter.ai/models")
        if "tools" not in model.get("supported_parameters", []):
            raise OpenRouterError(f"OpenRouter model '{self.remote_model}' does not support tool calling")

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        on_text_chunk: Callable[[str], None] | None = None,
        options: dict[str, Any] | None = None,
    ) -> ModelResponse:
        body = {
            "model": self.remote_model,
            "messages": to_openai_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
        if options and "temperature" in options:
            body["temperature"] = options["temperature"]

        parts = []
        calls: dict[int, dict] = {}
        usage = {}
        started = time.perf_counter()
        first_token = None
        response = None

        try:
            response = requests.post(
                f"{API_URL}/chat/completions",
                headers=self._headers(),
                json=body,
                stream=True,
                timeout=(10, 120),
            )
            if response.status_code != 200:
                try:
                    error = response.json().get("error", {})
                    detail = error.get("message", response.text)
                    raw = (error.get("metadata") or {}).get("raw")
                    if raw:
                        detail = f"{detail} ({raw})"
                except ValueError:
                    detail = response.text
                raise OpenRouterError(f"OpenRouter returned HTTP {response.status_code}: {detail}")

            for line in response.iter_lines():
                if not line or line.startswith(b":") or not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise OpenRouterError("Received malformed data from OpenRouter") from exc

                if "error" in chunk:
                    raise OpenRouterError(f"Model error: {chunk['error'].get('message', chunk['error'])}")
                if chunk.get("usage"):
                    usage = chunk["usage"]

                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        first_token = first_token or time.perf_counter()
                        parts.append(piece)
                        if on_text_chunk is not None:
                            on_text_chunk(piece)
                    for fragment in delta.get("tool_calls") or []:
                        slot = calls.setdefault(fragment.get("index", 0), {"id": None, "name": "", "arguments": ""})
                        slot["id"] = fragment.get("id") or slot["id"]
                        function = fragment.get("function") or {}
                        slot["name"] += function.get("name") or ""
                        slot["arguments"] += function.get("arguments") or ""

        except requests.ConnectionError as exc:
            raise OpenRouterError("Lost connection to OpenRouter.") from exc
        except requests.Timeout as exc:
            raise OpenRouterError("OpenRouter took too long to respond.") from exc
        except requests.RequestException as exc:
            raise OpenRouterError(f"Request to OpenRouter failed: {exc}") from exc
        finally:
            if response is not None:
                response.close()

        tool_calls = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                arguments = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                arguments = slot["arguments"]
            call = {"function": {"name": slot["name"], "arguments": arguments}}
            if slot["id"]:
                call["id"] = slot["id"]
            tool_calls.append(call)

        ended = time.perf_counter()
        return ModelResponse(
            content="".join(parts),
            tool_calls=tool_calls,
            stats={
                "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
                "response_tokens": usage.get("completion_tokens", 0) or 0,
                "total_duration_s": ended - started,
                "eval_duration_s": ended - (first_token or started),
            },
        )


def make_model_client(model: str, host: str, num_ctx: int | None = None):
    if model.startswith(PREFIX):
        return OpenRouterClient(model)
    return OllamaClient(model, host, num_ctx)
