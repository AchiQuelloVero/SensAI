import json
import textwrap
from datetime import date

from prompting.persona import describe_persona
from tools.permissions import PermissionsError, describe_permissions, load_permissions


class PromptError(Exception):
    pass


def load_system_prompt(path):
    try:
        with open(path, encoding="utf-8") as f:
            prompt = f.read().strip()
    except FileNotFoundError:
        raise PromptError(f"system prompt file not found: {path}")
    except OSError as e:
        raise PromptError(f"cannot read system prompt file {path}: {e}")

    if not prompt:
        raise PromptError(f"system prompt file is empty: {path}")

    return prompt


def load_profile(path, required):
    try:
        with open(path, encoding="utf-8") as f:
            profile = json.load(f)
    except FileNotFoundError:
        if required:
            raise PromptError(f"profile file not found: {path}")
        return None
    except (OSError, ValueError) as e:
        raise PromptError(f"cannot read profile file {path}: {e}")

    if not isinstance(profile, dict):
        raise PromptError(
            f"profile file must contain a JSON object: {path}"
        )

    preferences = profile.get("preferences", {})
    if not isinstance(preferences, dict):
        raise PromptError(
            f"'preferences' must be a JSON object in {path}"
        )

    return profile


# where the tool and persona instructions start, after the base prompt, profile and date
INSTRUCTIONS_START = "\n\nWeb tools:"

TOOLS_PROMPT = """Today's date is {today}.

Web tools:
- Call web_search before answering questions about current events, recent releases or versions, prices, people, or any fact you are not sure about. Do not answer these from memory.
- Write short, specific search queries. Use news=True only for news and events. Use timelimit only when the question is about a recent period.
- Snippets are often enough. When they are not, call web_fetch on the most relevant url with a query describing what you need. Never guess urls, only fetch urls returned by web_search.
- If a tool returns an error, try a different query or url once, then answer with what you have and say what you could not find.
- If the results are not about the question, say so instead of answering about them.

When your answer uses web results, you must cite them: put the result number in brackets after each fact, like [1], and end with a "Sources:" list of the numbers, titles and urls you used.

File tools:
- File tools work inside the safe_test/ folder only, and every path is relative to it.
- Write paths like "hello.txt" or "notes/todo.md". Never start a path with "safe_test/", "./safe_test/" or "/".
- To create a file, call write_file with "path" and "content". edit_file only changes a file that already exists.
- The user sets these file permissions, you cannot change them:
{permissions}
- If a file tool says "denied", tell the user which permission is missing and that they can change it with /permissions. Do not retry with another path.

Other tools:
- If a tool error says what is wrong with the arguments (for example which path is allowed), fix them and call the tool again once. Do not tell the user how to call the tool."""


def file_permissions():
    try:
        text = describe_permissions(load_permissions())
    except PermissionsError as e:
        text = f"- no access: the permissions file is broken ({e})"
    return textwrap.indent(text, "  ")


def build_system_prompt(base_prompt, profile, persona=None):
    prompt = add_profile(base_prompt, profile)
    prompt += "\n\n" + TOOLS_PROMPT.format(
        today=date.today().strftime("%A %d %B %Y"),
        permissions=file_permissions(),
    )
    # the persona goes last: small models follow the most recent instructions most closely
    if persona:
        prompt += "\n\n" + describe_persona(persona)
    return prompt


def system_prompt_facts(prompt):
    """The base prompt, profile and date of a built system prompt, without the tool and persona instructions."""
    end = prompt.find(INSTRUCTIONS_START)
    return prompt if end == -1 else prompt[:end]


def add_profile(base_prompt, profile):
    if not profile:
        return base_prompt

    lines = []

    if profile.get("name"):
        lines.append(f"The user's name is {profile['name']}.")

    preferences = profile.get("preferences", {})
    if preferences:
        lines.append("User preferences:")
        lines.extend(
            f"- {key}: {value}"
            for key, value in preferences.items()
        )

    instructions = str(
        profile.get("custom_instructions", "")
    ).strip()

    if instructions:
        lines.append(f"Custom instructions: {instructions}")

    if not lines:
        return base_prompt

    return base_prompt + "\n\n" + "\n".join(lines)