import json
import os
import re

import jsonschema

from core.config import PERSONAS_DIR

PERSONA_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "role": {"type": "string", "minLength": 1},
        "tone": {"type": "string", "minLength": 1},
        "scope": {"type": "string", "minLength": 1},
        "instructions": {"type": "string"},
    },
    "required": ["name", "role", "tone", "scope"],
    "additionalProperties": False,
}


FIELDS = ("name", "role", "tone", "scope", "instructions")
PERSONA_KEY = re.compile(r"^[a-z0-9_-]{1,40}$")
RESERVED_KEYS = {"show", "add", "edit", "delete"}


class PersonaError(Exception):
    pass


def list_personas(personas_dir=PERSONAS_DIR):
    try:
        names = os.listdir(personas_dir)
    except FileNotFoundError:
        return []
    return sorted(n[:-len(".json")] for n in names if n.endswith(".json"))


def load_persona(key, personas_dir=PERSONAS_DIR):
    if not key or os.path.basename(key) != key or key.startswith("."):
        raise PersonaError(f"invalid persona name: {key!r}")
    path = os.path.join(personas_dir, f"{key}.json")
    try:
        with open(path, encoding="utf-8") as f:
            persona = json.load(f)
    except FileNotFoundError:
        available = ", ".join(list_personas(personas_dir)) or "none"
        raise PersonaError(f"persona '{key}' not found in {personas_dir}/ (available: {available})")
    except (OSError, ValueError) as e:
        raise PersonaError(f"cannot read persona file {path}: {e}")
    try:
        jsonschema.validate(persona, PERSONA_SCHEMA)
    except jsonschema.ValidationError as e:
        where = ".".join(str(part) for part in e.absolute_path) or "top level"
        raise PersonaError(f"invalid persona file {path}: {where}: {e.message}")
    return persona


def clean_persona(persona):
    """The persona with its text trimmed, or PersonaError saying what is wrong with it."""
    if not isinstance(persona, dict):
        raise PersonaError("a persona must be a JSON object with name, role, tone and scope")
    persona = {k: v.strip() if isinstance(v, str) else v for k, v in persona.items() if v != "" or k != "instructions"}
    try:
        jsonschema.validate(persona, PERSONA_SCHEMA)
    except jsonschema.ValidationError as e:
        where = ".".join(str(part) for part in e.absolute_path) or "top level"
        if e.validator == "minLength":
            raise PersonaError(f"{where} can't be empty")
        raise PersonaError(f"{where}: {e.message}")
    return {k: persona[k] for k in FIELDS if k in persona}


def persona_name(key, personas_dir=PERSONAS_DIR):
    try:
        return load_persona(key, personas_dir)["name"]
    except PersonaError:
        return ""


def save_persona(key, persona, personas_dir=PERSONAS_DIR, new=True):
    if not PERSONA_KEY.match(key or "") or key in RESERVED_KEYS:
        raise PersonaError(f"invalid persona file name {key!r}: use lowercase letters, digits, - and _ "
                           f"(not {', '.join(sorted(RESERVED_KEYS))})")
    persona = clean_persona(persona)
    existing = list_personas(personas_dir)
    if new and key in existing:
        raise PersonaError(f"persona '{key}' already exists: edit it, or pick another file name")
    if not new and key not in existing:
        raise PersonaError(f"persona '{key}' not found (available: {', '.join(existing) or 'none'})")
    for other in existing:
        if other != key and simplify(persona_name(other, personas_dir)) == simplify(persona["name"]):
            raise PersonaError(f"the name {persona['name']!r} is already used by persona '{other}'")
    path = os.path.join(personas_dir, f"{key}.json")
    try:
        os.makedirs(personas_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(persona, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        raise PersonaError(f"cannot save {path}: {e.strerror or e}")
    return persona


def delete_persona(key, personas_dir=PERSONAS_DIR):
    if key not in list_personas(personas_dir):
        raise PersonaError(f"persona '{key}' not found (available: {', '.join(list_personas(personas_dir)) or 'none'})")
    try:
        os.remove(os.path.join(personas_dir, f"{key}.json"))
    except OSError as e:
        raise PersonaError(f"cannot delete persona '{key}': {e.strerror or e}")


def simplify(text):
    return "".join(text.lower().split())


def find_persona_key(text, personas_dir=PERSONAS_DIR):
    """The persona file name matching what the user typed: the file name or the
    persona's name, ignoring capitals and spaces ("vera" -> "reviewer")."""
    wanted = simplify(text)
    keys = list_personas(personas_dir)
    for key in keys:
        if simplify(key) == wanted:
            return key
    for key in keys:
        try:
            if simplify(load_persona(key, personas_dir)["name"]) == wanted:
                return key
        except PersonaError:
            continue
    return text


def session_persona(session, default_key, personas_dir=PERSONAS_DIR):
    """The chat's persona and None, or the default persona and why the chat's one can't be used
    (deleted or broken file): an old chat must keep working when its persona is gone."""
    key = session.get("persona")
    if not key:
        return None, None
    try:
        return load_persona(key, personas_dir), None
    except PersonaError as error:
        return load_persona(default_key, personas_dir), f"{error}; using {default_key} instead"


def guard_config_for(session, guard_config, personas_dir=PERSONAS_DIR):
    """The guardrails config, with the scope of the active persona."""
    key = session.get("persona")
    try:
        persona = load_persona(key, personas_dir) if key else None
    except PersonaError:
        persona = None
    if persona is None:
        return guard_config
    return dict(guard_config, scope=persona["scope"])


def switch_note(previous_name, persona):
    """Placed in the history where the persona changes, so the model doesn't continue the old style."""
    return (
        f"The persona changed here: from now on you are {persona['name']}. "
        f"The answers above were written by {previous_name}: keep the facts from them, "
        "but never copy their tone or style."
    )


def edit_note(persona):
    """Placed in the history where the active persona is edited, so the model follows the new
    version instead of copying the answers it already gave."""
    return (
        f"The persona was edited here: from now on you are {persona['name']}, {persona['role']}. "
        f"Tone: {persona['tone']}. Scope: {persona['scope']}. "
        "Keep the facts from the answers above, but never copy their tone or wording."
    )


def describe_persona(persona):
    lines = [
        f"For this conversation you are {persona['name']}, {persona['role']}. "
        "This replaces any name or role given above.",
        f"Tone: {persona['tone']}. Write every answer in this tone.",
        f"Scope: {persona['scope']}.",
        "Earlier answers in this conversation may come from other personas: "
        f"keep their facts, but never copy their style. Answer only as {persona['name']}.",
    ]
    instructions = persona.get("instructions", "").strip()
    if instructions:
        lines.append(f"Persona instructions: {instructions}")
    return "\n".join(lines)
