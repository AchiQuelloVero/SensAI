from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


Message = dict[str, Any]
ToolDefinition = dict[str, Any]
ToolCall = dict[str, Any]
Stats = dict[str, int | float]


@dataclass
class ModelResponse:

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stats: Stats = field(default_factory=dict)
    # the retrieved passages the answer could draw on, each starting with its source
    excerpts: list[str] = field(default_factory=list)
    # the messages the answer was generated from, so it can be sampled again
    request: list[Message] = field(default_factory=list)
    cached: bool = False
    saved_s: float = 0.0
    cache_note: str = ""

    def to_message(self) -> Message:
        message = {
            "role": "assistant",
            "content": self.content,
        }

        if self.tool_calls:
            message["tool_calls"] = self.tool_calls

        return message


class ModelClient(Protocol):

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        on_text_chunk: Callable[[str], None] | None = None,
        options: dict[str, Any] | None = None,
    ) -> ModelResponse:
        ...

class ToolCatalog(Protocol):

    def to_ollama_tools(self) -> list[ToolDefinition]:
        ...


class ToolRunner(Protocol):

    async def execute(
        self,
        name: str,
        arguments: Any,
        trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

@dataclass
class RetrievedContext:

    prompt: str  # sent to the model in place of the question
    excerpts: list[str] = field(default_factory=list)


class ContextProvider(Protocol):

    def build_context(self, question: str) -> RetrievedContext | None:
        """What to send to the model in place of the question, or None to send it as is."""
        ...


@dataclass
class CacheLookup:
    answer: str | None = None
    lookup_s: float = 0.0
    saved_s: float = 0.0
    reason: str = ""
    note: str = ""


class AnswerStore(Protocol):

    def lookup(self, model: str, history: list[Message], question: str) -> CacheLookup:
        """A saved answer to the same question, or why there is none."""
        ...

    def save(self, model: str, history: list[Message], question: str, answer: str, duration_s: float,
             used_tools: bool = False) -> str:
        """Saves the answer if it can be reused, and says what happened."""
        ...

    def forget(self, question: str, answer: str) -> bool:
        """Removes a saved answer that must not be served again. True if there was one."""
        ...


class ToolLookup(Protocol):

    def get(self, name: str) -> dict[str, Any] | None:
        ...