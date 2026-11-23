import json

from pathlib import Path

import jsonschema

from core.config import GUARDRAILS_CONFIG_PATH
from guardrails.pii import PII_TYPES

CONFIG_PATH = Path(GUARDRAILS_CONFIG_PATH)
ACTIONS = ["allow", "mask", "block"]
CATEGORIES = ["off_topic", "harmful", "asks_for_others_data"]

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "pii": {
            "type": "object",
            "properties": {name: {"enum": ACTIONS} for name in PII_TYPES},
            "required": list(PII_TYPES),
            "additionalProperties": False,
        },
        "scope": {"type": "string", "minLength": 1},
        "blocked_categories": {"type": "array", "items": {"enum": CATEGORIES}, "uniqueItems": True},
        "blocked_message": {"type": "string", "minLength": 1},
        "output_check": {"type": "boolean"},
    },
    "required": ["pii", "scope", "blocked_categories", "blocked_message"],
    "additionalProperties": False,
}


class GuardrailError(Exception):
    pass


def load_config(path=CONFIG_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        raise GuardrailError(f"guardrails config not found: {path}")
    except (OSError, ValueError) as e:
        raise GuardrailError(f"cannot read guardrails config {path}: {e}")
    try:
        jsonschema.validate(config, CONFIG_SCHEMA)
    except jsonschema.ValidationError as e:
        where = ".".join(str(part) for part in e.absolute_path) or "top level"
        raise GuardrailError(f"invalid guardrails config {path}: {where}: {e.message}")
    return config


def save_config(config, path=CONFIG_PATH):
    try:
        jsonschema.validate(config, CONFIG_SCHEMA)
    except jsonschema.ValidationError as e:
        where = ".".join(str(part) for part in e.absolute_path) or "top level"
        raise GuardrailError(f"invalid guardrails setting: {where}: {e.message}")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        raise GuardrailError(f"cannot save guardrails config {path}: {e.strerror or e}")
