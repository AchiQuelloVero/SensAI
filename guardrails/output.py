import re

from guardrails.pii import mask_pii

# end of the stream that could still become personal data
UNFINISHED = re.compile(r"(?:[+(]?[A-Z0-9][A-Z0-9().-]* )*\S*$")


def types_to_hide(config):
    return tuple(name for name, action in config["pii"].items() if action != "allow")


class StreamMasker:
    def __init__(self, types, write):
        self.types = types
        self.write = write
        self.buffer = ""
        self.counts = {}

    def __call__(self, chunk):
        self.buffer += chunk
        start = UNFINISHED.search(self.buffer).start()
        if start:
            self.emit(self.buffer[:start])
            self.buffer = self.buffer[start:]

    def flush(self):
        if self.buffer:
            self.emit(self.buffer)
            self.buffer = ""

    def emit(self, text):
        masked, counts = mask_pii(text, self.types)
        for name, count in counts.items():
            self.counts[name] = self.counts.get(name, 0) + count
        self.write(masked)


def mask_messages(messages, types):
    for message in messages:
        if message["role"] in ("assistant", "tool") and message.get("content"):
            message["content"], _ = mask_pii(message["content"], types)


def turn_answer(messages, turn_start):
    return "\n".join(m["content"] for m in messages[turn_start + 1:] if m["role"] == "assistant" and m.get("content"))


def withdraw_answer(messages, turn_start, config, decision, answer_store=None):
    """Replaces the turn's answer with the refusal. Returns True if a saved copy was removed from the cache."""
    question, answer = messages[turn_start]["content"], turn_answer(messages, turn_start)
    del messages[turn_start + 1:]
    messages.append({
        "role": "assistant",
        "content": config["blocked_message"],
        "guardrail": {"category": decision["category"], "reason": decision["reason"]},
    })
    return answer_store is not None and answer_store.forget(question, answer)
