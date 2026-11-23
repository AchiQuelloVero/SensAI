import json
import os
import re

import jsonschema
import requests

from core.config import HOST, INTERNAL_SCHEMAS_DIR, SCHEMAS_DIR, load_app_config

MAX_RETRIES = 2
REMOTE_PREFIX = "openrouter/"


class StructuredOutputError(Exception):
    pass


class InvalidOutputError(StructuredOutputError):
    pass


def list_schemas(schemas_dir=SCHEMAS_DIR):
    try:
        names = os.listdir(schemas_dir)
    except FileNotFoundError:
        return []
    return sorted(n[:-len(".json")] for n in names if n.endswith(".json"))


def load_schema(name, schemas_dir=SCHEMAS_DIR):
    if not name or os.path.basename(name) != name or name.startswith("."):
        raise StructuredOutputError(f"invalid schema name: {name!r}")
    path = os.path.join(schemas_dir, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
    except FileNotFoundError:
        available = ", ".join(list_schemas(schemas_dir)) or "none"
        raise StructuredOutputError(f"schema '{name}' not found in {schemas_dir}/ (available: {available})")
    except (OSError, ValueError) as e:
        raise StructuredOutputError(f"cannot read schema file {path}: {e}")
    if not isinstance(schema, dict):
        raise StructuredOutputError(f"schema file must contain a JSON object: {path}")
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except jsonschema.SchemaError as e:
        raise StructuredOutputError(f"{path} is not a valid JSON schema: {e.message}")
    return schema


SCHEMA_NAME = re.compile(r"^[a-z0-9_]{1,40}$")


def check_user_schema(schema):
    """Why `schema` can't be used for /json, or None if it can."""
    if not isinstance(schema, dict):
        return "a schema must be a JSON object, like {\"type\": \"object\", \"properties\": {...}}"
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except jsonschema.SchemaError as e:
        where = ".".join(str(part) for part in e.absolute_path) or "top level"
        return f"not a valid JSON Schema: {where}: {e.message}"
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict) or not schema["properties"]:
        return 'the top level must be {"type": "object", "properties": {...}} with at least one property, so the reply is a JSON object'
    return None


def save_schema(name, schema, schemas_dir=SCHEMAS_DIR):
    if not SCHEMA_NAME.match(name or ""):
        raise StructuredOutputError(f"invalid schema name {name!r}: use lowercase letters, digits and _ (at most 40)")
    problem = check_user_schema(schema)
    if problem:
        raise StructuredOutputError(problem)
    path = os.path.join(schemas_dir, f"{name}.json")
    if os.path.exists(path):
        raise StructuredOutputError(f"schema '{name}' already exists: delete it first, or pick another name")
    try:
        os.makedirs(schemas_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        raise StructuredOutputError(f"cannot save {path}: {e.strerror or e}")


def delete_schema(name, schemas_dir=SCHEMAS_DIR):
    if name not in list_schemas(schemas_dir):
        raise StructuredOutputError(f"schema '{name}' not found (available: {', '.join(list_schemas(schemas_dir)) or 'none'})")
    try:
        os.remove(os.path.join(schemas_dir, f"{name}.json"))
    except OSError as e:
        raise StructuredOutputError(f"cannot delete schema '{name}': {e.strerror or e}")


def internal_schema(name):
    """A schema the app's own checks use (guardrails, cache, fact-check): kept apart from the user's."""
    return load_schema(name, INTERNAL_SCHEMAS_DIR)


def schema_errors(data, schema):
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = []
    for error in validator.iter_errors(data):
        where = ".".join(str(part) for part in error.absolute_path) or "top level"
        errors.append(f"{where}: {error.message}")
    return errors


def with_schema_instructions(messages, schema):
    instructions = "Reply only with JSON that matches this JSON schema:\n" + json.dumps(schema, indent=2)
    if messages and messages[0]["role"] == "system":
        system = {"role": "system", "content": messages[0]["content"] + "\n\n" + instructions}
        return [system, *messages[1:]]
    return [{"role": "system", "content": instructions}, *messages]


def check_options():
    # same num_ctx as the chat, or Ollama reloads the model each time the checks run
    return {"temperature": 0, "num_ctx": load_app_config().num_ctx}


def local_model(model):
    """chat_json only talks to Ollama, so a cloud chat model is swapped for the local check model
    (`check_model` in config.json, read on each call so a change applies to the next check).
    The checks also read the raw user text: keeping them local keeps that text on this machine."""
    if not model.startswith(REMOTE_PREFIX):
        return model
    return load_app_config().check_model


def chat_json(model, messages, schema, host=HOST, think=False, max_retries=MAX_RETRIES,
              use_format=True, stats=None):
    model = local_model(model)
    messages = with_schema_instructions(messages, schema)
    if stats is not None:
        stats["retries"] = []
    for attempt in range(1, max_retries + 2):
        if stats is not None:
            stats["attempts"] = attempt
        content, done_reason = ask_ollama(model, messages, schema if use_format else None, think, host)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            if done_reason == "length":
                problems = ["the reply was cut off before the JSON was complete"]
            else:
                problems = ["the reply is not valid JSON"]
        else:
            problems = schema_errors(data, schema)
            if not problems:
                return data
        if stats is not None:
            stats["retries"].append({"attempt": attempt, "problems": problems})
        feedback = "Your JSON does not match the schema:\n- " + "\n- ".join(problems)
        messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": feedback + "\nReply again with corrected JSON only."},
        ]
    raise InvalidOutputError(f"no valid JSON after {max_retries + 1} attempts: {'; '.join(problems)}")


def ask_ollama(model, messages, schema, think, host):
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": check_options(),
    }
    if schema is not None:
        body["format"] = schema
    try:
        response = requests.post(f"{host}/api/chat", json=body, timeout=(5, 120))
    except requests.ConnectionError:
        raise StructuredOutputError(f"cannot reach Ollama at {host}. Is it running?")
    except requests.Timeout:
        raise StructuredOutputError("Ollama took too long to respond")

    try:
        data = response.json()
    except ValueError:
        raise StructuredOutputError(f"Ollama returned HTTP {response.status_code}: {response.text}")
    if response.status_code != 200 or "error" in data:
        raise StructuredOutputError(f"Ollama returned HTTP {response.status_code}: {data.get('error')}")

    return data.get("message", {}).get("content", ""), data.get("done_reason")
