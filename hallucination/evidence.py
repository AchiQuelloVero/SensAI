import json
from dataclasses import dataclass, field

from core.contracts import Message
from prompting.prompt_builder import system_prompt_facts

# earlier user and assistant messages kept as evidence
HISTORY_MESSAGES = 6


@dataclass
class Evidence:
    system: str = ""  # the facts in the system prompt: base prompt, profile, today's date
    question: str = ""  # labelled apart: what a question takes for granted is not evidence
    history: list[Message] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)  # {"name", "content"} for each tool run this turn
    excerpts: list[str] = field(default_factory=list)  # retrieved passages, each starting with its source

    def is_empty(self) -> bool:
        return not (self.history or self.tool_results or self.excerpts)

    def to_text(self) -> str:
        """One labelled block per item, so the judge can say which one supports a claim."""
        blocks = self.blocks()
        return "\n\n".join(blocks) if blocks else "(no evidence)"

    def blocks(self) -> list[str]:
        """Each item as text whose first line is its label, like "[tool multiply]"."""
        blocks = []
        if self.system:
            blocks.append(f"[system prompt]\n{self.system}")
        for message in self.history:
            block = f"[{message['role']} message]\n{message['content']}"
            if message.get("flagged"):
                # an earlier answer must not become evidence for the claims found wrong in it
                block += "\n(Not evidence, found unsupported when this was said: " + "; ".join(message["flagged"]) + ")"
            blocks.append(block)
        if self.question:
            blocks.append(f"[current question]\n{self.question}")
        for result in self.tool_results:
            blocks.append(f"[tool {result['name']}]\n{result['content']}")
        for excerpt in self.excerpts:
            # excerpts already start with their [source, chunk n] line
            blocks.append(f"[document] {excerpt}")
        return blocks


def collect_evidence(
    messages: list[Message],
    turn_start: int,
    excerpts: list[str] | None = None,
    history_messages: int = HISTORY_MESSAGES,
) -> Evidence:
    """What the assistant knew when it answered the turn that starts at messages[turn_start]
    (the user's message): the system prompt, the recent conversation, this turn's tool
    results and the excerpts retrieved for it."""
    question = messages[turn_start]["content"] if turn_start < len(messages) else ""
    system = ""
    if messages and messages[0]["role"] == "system":
        # the tool and persona instructions state no facts and mislead the judge
        system = system_prompt_facts(messages[0]["content"])

    # assistant messages that only call tools have no content and say nothing
    history = [
        {"role": m["role"], "content": m["content"], "flagged": m.get("check", {}).get("flagged", [])}
        for m in messages[:turn_start]
        if m["role"] in ("user", "assistant") and m.get("content")
    ][-history_messages:]

    tool_results = [
        {"name": m.get("tool_name", "unknown"), "content": tool_output(m["content"])}
        for m in messages[turn_start + 1:]
        if m["role"] == "tool"
    ]

    return Evidence(system, question, history, tool_results, list(excerpts or []))


def tool_output(content: str) -> str:
    """The tool's result (or error) without the {"result": ...} wrapper the agent adds."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(data, dict):
        return content
    if "error" in data:
        return f"error: {data['error']}"
    value = data.get("result", data)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # e.g. the user edited the arguments: without it, what really ran looks like a contradiction
    return f"{text}\n{data['note']}" if "result" in data and data.get("note") else text
