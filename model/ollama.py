import json
from typing import Any, Callable

import requests

from core.contracts import (
    Message,
    ModelResponse,
    ToolDefinition,
)


OLLAMA_DEFAULT_CTX = 4096


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        num_ctx: int | None = None,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx

    @property
    def context_window(self) -> int:
        return self.num_ctx or OLLAMA_DEFAULT_CTX

    def list_models(self) -> list[str]:
        try:
            response = requests.get(
                f"{self.host}/api/tags",
                timeout=5,
            )
            response.raise_for_status()

        except requests.ConnectionError as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}. "
                "Is it running?"
            ) from exc

        except requests.RequestException as exc:
            raise OllamaError(
                f"Ollama did not respond correctly: {exc}"
            ) from exc

        return [
            item["name"]
            for item in response.json().get("models", [])
        ]

    def capabilities(self, model: str) -> list[str] | None:
        """What the model can do ('completion', 'tools', 'embedding', ...), or None if Ollama doesn't say."""
        try:
            response = requests.post(f"{self.host}/api/show", json={"model": model}, timeout=5)
            response.raise_for_status()
            return response.json().get("capabilities")
        except (requests.RequestException, ValueError):
            return None

    def can_chat_with_tools(self, model: str) -> bool:
        capabilities = self.capabilities(model)
        return capabilities is None or {"completion", "tools"} <= set(capabilities)

    def list_chat_models(self) -> list[str]:
        """The installed models this app can chat with: embedding models and models without tools are left out."""
        return [name for name in self.list_models() if self.can_chat_with_tools(name)]

    def check_model_available(self) -> None:
        installed = self.list_models()

        full_name = (
            self.model
            if ":" in self.model
            else f"{self.model}:latest"
        )

        if full_name not in installed:
            raise OllamaError(
                f"Model '{self.model}' is not installed. "
                f"Run: ollama pull {self.model}"
            )

        if not self.can_chat_with_tools(full_name):
            raise OllamaError(
                f"Model '{self.model}' can't chat with tools, which sensAI needs. "
                "Pick another one, for example qwen3:8b"
            )

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        on_text_chunk: Callable[[str], None] | None = None,
        options: dict[str, Any] | None = None,
    ) -> ModelResponse:
        parts = []
        tool_calls = []
        stats = {}

        response = None
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": True,
        }
        if self.num_ctx:
            options = {"num_ctx": self.num_ctx, **(options or {})}
        if options:
            body["options"] = options

        try:
            response = requests.post(
                f"{self.host}/api/chat",
                json=body,
                stream=True,
                timeout=(5, 120),
            )

            if response.status_code != 200:
                try:
                    detail = response.json().get(
                        "error", response.text
                    )
                except ValueError:
                    detail = response.text

                raise OllamaError(
                    f"Ollama returned HTTP "
                    f"{response.status_code}: {detail}"
                )

            for line in response.iter_lines():
                if not line:
                    continue

                try:
                    chunk = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise OllamaError(
                        "Received malformed data from Ollama"
                    ) from exc

                if "error" in chunk:
                    raise OllamaError(
                        f"Model error: {chunk['error']}"
                    )

                message = chunk.get("message", {})
                piece = message.get("content", "")

                parts.append(piece)

                if piece and on_text_chunk is not None:
                    on_text_chunk(piece)

                tool_calls.extend(
                    message.get("tool_calls") or []
                )

                if chunk.get("done"):
                    stats = {
                        "prompt_tokens": (
                            chunk.get("prompt_eval_count", 0) or 0
                        ),
                        "response_tokens": (
                            chunk.get("eval_count", 0) or 0
                        ),
                        "total_duration_s": (
                            chunk.get("total_duration", 0) / 1e9
                        ),
                        "eval_duration_s": (
                            chunk.get("eval_duration", 0) / 1e9
                        ),
                    }
                    break

        except requests.ConnectionError as exc:
            raise OllamaError(
                "Lost connection to Ollama."
            ) from exc

        except requests.Timeout as exc:
            raise OllamaError(
                "Ollama took too long to respond."
            ) from exc

        except requests.RequestException as exc:
            raise OllamaError(
                f"Request to Ollama failed: {exc}"
            ) from exc

        finally:
            if response is not None:
                response.close()

        return ModelResponse(
            content="".join(parts),
            tool_calls=tool_calls,
            stats=stats,
        )