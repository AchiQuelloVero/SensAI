import json

from pathlib import Path

import jsonschema

from core.config import HALLUCINATION_CONFIG_PATH

CONFIG_PATH = Path(HALLUCINATION_CONFIG_PATH)
ACTIONS = ["warn", "regenerate", "refuse"]

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        # null: the judge is the chat model
        "judge_model": {"type": ["string", "null"], "minLength": 1},
        # verdicts the judge is less sure of than this are not acted on
        "confidence_threshold": {"type": "number", "minimum": 0, "maximum": 1},
        "action": {"enum": ACTIONS},
        # sample the answer again and flag the claims the samples disagree on
        "self_consistency": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "samples": {"type": "integer", "minimum": 2, "maximum": 10},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            },
            "required": ["enabled", "samples", "temperature"],
            "additionalProperties": False,
        },
        "refusal_message": {"type": "string", "minLength": 1},
    },
    "required": [
        "enabled", "judge_model", "confidence_threshold", "action", "self_consistency", "refusal_message",
    ],
    "additionalProperties": False,
}


class HallucinationError(Exception):
    pass


def load_config(path=CONFIG_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        raise HallucinationError(f"hallucination config not found: {path}")
    except (OSError, ValueError) as e:
        raise HallucinationError(f"cannot read hallucination config {path}: {e}")
    try:
        jsonschema.validate(config, CONFIG_SCHEMA)
    except jsonschema.ValidationError as e:
        where = ".".join(str(part) for part in e.absolute_path) or "top level"
        raise HallucinationError(f"invalid hallucination config {path}: {where}: {e.message}")
    return config


def save_config(config, path=CONFIG_PATH):
    try:
        jsonschema.validate(config, CONFIG_SCHEMA)
    except jsonschema.ValidationError as e:
        where = ".".join(str(part) for part in e.absolute_path) or "top level"
        raise HallucinationError(f"invalid hallucination setting: {where}: {e.message}")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        raise HallucinationError(f"cannot save hallucination config {path}: {e.strerror or e}")


def judge_model(config, chat_model):
    return config["judge_model"] or chat_model
