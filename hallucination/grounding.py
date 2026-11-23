GROUNDED_PROMPT = """Grounding rules (they override the citation instructions above):
- Your evidence is this conversation, the tool results and the document excerpts you were given. Facts about the user, their project or their documents, recent events, prices, versions, people, numbers, dates, quotes and sources must come from it. If they are not there, use a tool to find them; if you still can't, say "I don't know" instead of guessing.
- You may also use common knowledge you are sure of: how to write code, well-known definitions, basic science and history.
- Never describe a project, product, person, event or quote that you have no evidence for and are not sure exists: say "I don't know of" it and stop. Do not guess who made it or what it does.
- If the question takes for granted something false, say it is false instead of going along with it.
- Only cite what a tool result or an excerpt gave you. With no tool result or excerpt, write no [n] citations and no "Sources:" list.
- If you can only answer part of the question, answer that part and say "I don't know" for the rest."""


def grounding_prompt(config):
    """The rules added to the system prompt of every chat request, or None when the check is off."""
    return GROUNDED_PROMPT if config["enabled"] else None
