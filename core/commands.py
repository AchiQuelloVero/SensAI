import json
import os
import time

from cache.commands import run_cache_command
from cache.service import format_seconds
from call_tracking.call_log import compute_stats, log_call
from core.config import PROFILE_PATH, WORKSPACE_DIR, AppConfig, load_app_config
from guardrails.commands import run_guardrails_command
from guardrails.guard import guard_input
from guardrails.output import types_to_hide
from guardrails.pii import mask_pii
from model.ollama import OllamaError
from model.openrouter import PREFIX as REMOTE_PREFIX, OpenRouterClient
from prompting.persona import (
    FIELDS, RESERVED_KEYS, PersonaError, delete_persona, find_persona_key, guard_config_for, list_personas,
    edit_note, load_persona, save_persona, session_persona, switch_note,
)
from prompting.prompt_builder import PromptError, build_system_prompt, load_profile, load_system_prompt
from prompting.versioning import VersionError, get_version, list_versions, rollback
from rag.commands import run_docs_command, run_ingest_command, run_kb_command, run_rag_command
from storage.branching import BranchError, fork_before_user_turn, list_leaves, switch_branch
from storage.branching import BranchError, fork_before_user_turn, list_leaves, set_root_message, switch_branch
from structured import MAX_RETRIES, StructuredOutputError, ask_ollama, chat_json, delete_schema, list_schemas, load_schema, save_schema
from tools.permissions import LEVELS, PermissionsError, describe_permissions, load_permissions, resolve, save_permissions


def by_name(counts: dict) -> str:
    return ", ".join(f"{name} {n}" for name, n in sorted(counts.items(), key=lambda item: -item[1]))


def guardrail_lines(g: dict) -> list[str]:
    lines = []
    if g["messages_checked"]:
        refused = sum(g["messages_refused"].values())
        line = f"  messages: {g['messages_checked']} checked, {refused} refused"
        lines.append(line + (f" ({by_name(g['messages_refused'])})" if refused else ""))
    if g["answers_checked"]:
        withdrawn = sum(g["answers_withdrawn"].values())
        line = f"  answers: {g['answers_checked']} checked, {withdrawn} withdrawn"
        lines.append(line + (f" ({by_name(g['answers_withdrawn'])})" if withdrawn else ""))
    if g["masked_in_messages"]:
        lines.append(f"  masked in messages: {by_name(g['masked_in_messages'])}")
    if g["masked_in_answers"]:
        lines.append(f"  masked in answers: {by_name(g['masked_in_answers'])}")
    return ["guardrails:", *lines] if lines else []


def run_stats_command(config: AppConfig | None = None) -> dict:
    cfg = config or load_app_config()
    stats = compute_stats(cfg.log_file)

    if not stats["total_records"]:
        return {
            "ok": True,
            "command": "stats",
            "stats": stats,
            "output": "(no calls logged yet)",
        }

    lines = []
    if stats["model_calls"]:
        lines.append(
            f"{stats['model_calls']} call(s) logged, "
            f"{stats['errors']} error(s) ({stats['error_rate']:.0%})"
        )
        if stats["ok_calls"]:
            lines.append(
                f"avg latency: {stats['avg_latency_s']:.2f}s, "
                f"{stats['tokens_per_s']:.1f} tokens/s"
            )

    if stats["tool_decisions_total"]:
        summary = ", ".join(f"{v} {k}" for k, v in stats["tool_decisions"].items())
        lines.append(f"{stats['tool_decisions_total']} tool decision(s): {summary}")

    if stats["checks"]:
        line = f"{stats['checks']} answer(s) fact-checked, {stats['flagged_answers']} flagged ({stats['flagged_rate']:.0%})"
        if stats["check_actions"]:
            line += ": " + ", ".join(f"{v} {k}" for k, v in stats["check_actions"].items())
        lines.append(line)

    if stats["cache_hits"]:
        lines.append(f"{stats['cache_hits']} answer(s) from cache, {format_seconds(stats['cache_saved_s'])} saved")

    if stats["json_replies"]:
        fixed = stats["json_retried"] - stats["json_failed"]
        lines.append(f"{stats['json_replies']} JSON reply(ies): {fixed} fixed by a retry, {stats['json_failed']} failed after every retry")

    lines += guardrail_lines(stats["guardrails"])

    return {
        "ok": True,
        "command": "stats",
        "stats": stats,
        "output": "\n".join(lines),
    }


def run_json_command(
    model: str,
    messages: list,
    text: str,
    config: AppConfig | None = None,
) -> dict:
    cfg = config or load_app_config()
    name, _, prompt = text.partition(" ")
    prompt = prompt.strip()

    if not name or not prompt:
        schemas = list_schemas(cfg.schemas_dir)
        output = (
            "usage: /json <schema> <prompt>\n"
            f"available schemas: {', '.join(schemas) or 'none'}"
        )
        return {
            "ok": False,
            "command": "json",
            "usage": True,
            "schemas": schemas,
            "output": output,
        }

    try:
        schema = load_schema(name, schemas_dir=cfg.schemas_dir)
    except StructuredOutputError as error:
        return {"ok": False, "command": "json", "error": str(error), "output": f"error: {error}"}

    stats = {}
    start = time.perf_counter()
    try:
        result = chat_json(
            model,
            [*messages, {"role": "user", "content": prompt}],
            schema,
            host=cfg.host,
            stats=stats,
        )
    except StructuredOutputError as error:
        attempts = stats.get("attempts", 0)
        log_call(cfg.log_file, {"json": name, "ok": False, "attempts": attempts, "retries": stats.get("retries", []),
                                "error": str(error), "duration_s": round(time.perf_counter() - start, 2)})
        lines = [f"error: {error}", *retry_lines(stats.get("retries", []))]
        return {"ok": False, "command": "json", "error": str(error), "attempts": attempts,
                "retries": stats.get("retries", []), "output": "\n".join(lines)}

    duration = round(time.perf_counter() - start, 2)
    log_call(cfg.log_file, {"json": name, "ok": True, "attempts": stats["attempts"], "retries": stats["retries"],
                            "duration_s": duration})
    formatted = json.dumps(result, indent=2, ensure_ascii=False)
    return {
        "ok": True,
        "command": "json",
        "schema": name,
        "data": result,
        "attempts": stats["attempts"],
        "max_attempts": MAX_RETRIES + 1,
        "retries": stats["retries"],
        "duration_s": duration,
        "notes": attempt_notes(stats["attempts"], stats["retries"], duration),
        "output": formatted,
    }


def retry_lines(retries: list) -> list[str]:
    return [f"  attempt {r['attempt']} failed: {'; '.join(r['problems'])}" for r in retries]


def attempt_notes(attempts: int, retries: list, duration: float) -> list[str]:
    first = f"valid JSON on attempt {attempts} of {MAX_RETRIES + 1} ({duration:.1f} s)"
    return [first, *retry_lines(retries)]


SCHEMAS_USAGE = (
    "usage: /schemas                       the schemas /json can use\n"
    "       /schemas show <name>           the whole schema\n"
    "       /schemas add <name> <file>     add one from a .json file\n"
    "       /schemas add <name> {...}      add one written inline\n"
    "       /schemas delete <name>         remove one"
)


def schema_fields(schema: dict) -> list[str]:
    required = set(schema.get("required", []))
    return [f"{key}{'' if key in required else '?'}" for key in schema.get("properties", {})]


def schemas_failed(error) -> dict:
    return {"ok": False, "command": "schemas", "error": str(error), "output": f"error: {error}"}


def read_schema_source(source: str):
    if source.startswith(("{", "[")):
        try:
            return json.loads(source)
        except ValueError as e:
            raise StructuredOutputError(f"the schema is not valid JSON: {e}")
    try:
        with open(os.path.expanduser(source), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise StructuredOutputError(f"file not found: {source}")
    except (OSError, ValueError) as e:
        raise StructuredOutputError(f"cannot read {source} as JSON: {e}")


def run_schemas_command(config: AppConfig, text: str) -> dict:
    words = text.split(maxsplit=2)
    folder = config.schemas_dir
    try:
        if not words:
            schemas = []
            for name in list_schemas(folder):
                try:
                    schemas.append({"name": name, "fields": schema_fields(load_schema(name, folder))})
                except StructuredOutputError as error:
                    schemas.append({"name": name, "error": str(error)})
            lines = [f"{s['name']}: {', '.join(s['fields']) if 'fields' in s else s['error']}" for s in schemas]
            output = "\n".join(lines) if lines else "(no schemas yet: /schemas add <name> <file>)"
            return {"ok": True, "command": "schemas", "action": "list", "schemas": schemas, "output": output + "\n(? = optional)" * bool(lines)}
        if words[0] == "show" and len(words) == 2:
            schema = load_schema(words[1], folder)
            return {"ok": True, "command": "schemas", "action": "show", "name": words[1], "schema": schema,
                    "output": json.dumps(schema, indent=2, ensure_ascii=False)}
        if words[0] == "add" and len(words) == 3:
            schema = read_schema_source(words[2].strip())
            save_schema(words[1], schema, folder)
            return {"ok": True, "command": "schemas", "action": "add", "name": words[1],
                    "output": f"schema '{words[1]}' added: /json {words[1]} <prompt>"}
        if words[0] == "delete" and len(words) == 2:
            delete_schema(words[1], folder)
            return {"ok": True, "command": "schemas", "action": "delete", "name": words[1],
                    "output": f"schema '{words[1]}' deleted"}
    except StructuredOutputError as error:
        return schemas_failed(error)
    return {"ok": False, "command": "schemas", "usage": True, "output": SCHEMAS_USAGE}


def run_guarded_json_command(
    model: str,
    messages: list,
    text: str,
    config: AppConfig,
    session: dict,
    guard_config: dict | None,
) -> dict:
    """/json with the same guardrails as a chat message: the prompt is checked and masked
    before it reaches the model, and PII in the model's JSON is hidden."""
    if guard_config is None:
        return run_json_command(model, messages, text, config=config)

    name, _, prompt = text.partition(" ")
    if not name or not prompt.strip() or name not in list_schemas(config.schemas_dir):
        return run_json_command(model, messages, text, config=config)  # usage or unknown schema, nothing to check

    decision = guard_input(model, prompt.strip(), guard_config_for(session, guard_config, config.personas_dir))
    log_call(config.log_file, {"guardrail": "input", **{k: v for k, v in decision.items() if k != "text"}})
    if decision["action"] == "block":
        return {
            "ok": False,
            "command": "json",
            "blocked": decision,
            "output": f"{guard_config['blocked_message']}\n  ✗ blocked: {decision['category']}, {decision['reason']}",
        }

    result = run_json_command(model, messages, f"{name} {decision['text']}", config=config)
    result["input_pii"] = decision["pii"]
    if result["ok"]:
        masked, counts = mask_pii(json.dumps(result["data"], ensure_ascii=False), types_to_hide(guard_config))
        result["data"] = json.loads(masked)
        result["output"] = json.dumps(result["data"], indent=2, ensure_ascii=False)
        result["output_pii"] = counts
        if counts:
            log_call(config.log_file, {"guardrail": "output", "action": "mask", "pii": counts})
    return result


def set_system_message(session: dict, messages: list, content: str) -> None:
    """Replace the system message live and in the saved branch tree."""
    message = {"role": "system", "content": content}
    if messages and messages[0]["role"] == "system":
        messages[0] = message
        set_root_message(session, message)
    else:
        messages.insert(0, message)


def active_persona(session: dict, config: AppConfig) -> dict | None:
    return session_persona(session, config.persona, config.personas_dir)[0]


def apply_rollback(session: dict, messages: list, config: AppConfig, version: int) -> int:
    """Roll back the prompt file and apply it to the live conversation right away."""
    rollback(config.system_prompt_path, version)
    new_base = get_version(config.system_prompt_path, version)
    profile = load_profile(
        config.profile_path,
        required=config.profile_path != PROFILE_PATH,
    )
    persona = active_persona(session, config)

    set_system_message(session, messages, build_system_prompt(new_base, profile, persona))
    return version


PERSONA_USAGE = (
    "usage: /persona                          the personas, * = the one answering\n"
    "       /persona <name>                   switch to it, the conversation is kept\n"
    "       /persona show <name>              its name, role, tone, scope and instructions\n"
    "       /persona add <file> <json|path>   create one: name, role, tone, scope, instructions\n"
    "       /persona edit <file> <json|path>  change some fields, e.g. {\"tone\": \"very formal\"}\n"
    "       /persona delete <file>            remove one (not the active or the default one)"
)


def persona_failed(error, action) -> dict:
    return {"ok": False, "command": "persona", "action": action, "error": str(error), "output": f"error: {error}"}


def read_persona_source(source: str) -> dict:
    if source.startswith(("{", "[")):
        try:
            return json.loads(source)
        except ValueError as e:
            raise PersonaError(f"the persona is not valid JSON: {e}")
    try:
        with open(os.path.expanduser(source), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise PersonaError(f"file not found: {source}")
    except (OSError, ValueError) as e:
        raise PersonaError(f"cannot read {source} as JSON: {e}")


def persona_rows(config: AppConfig, current: str | None) -> list[dict]:
    rows = []
    for key in list_personas(config.personas_dir):
        row = {"key": key, "current": key == current, "default": key == config.persona}
        try:
            row.update(load_persona(key, config.personas_dir))
        except PersonaError as error:
            row["problem"] = str(error)
        rows.append(row)
    return rows


def apply_persona_to_chat(config: AppConfig, session: dict, messages: list, persona: dict) -> None:
    base_prompt = load_system_prompt(config.system_prompt_path)
    profile = load_profile(config.profile_path, required=config.profile_path != PROFILE_PATH)
    set_system_message(session, messages, build_system_prompt(base_prompt, profile, persona))


def create_persona(config: AppConfig, key: str, persona: dict) -> dict:
    return save_persona(key, persona, config.personas_dir, new=True)


def update_persona(config: AppConfig, session: dict, messages: list, key: str, changes: dict) -> dict:
    """Saves the changed fields. If the chat is using this persona, its system message is rebuilt,
    so the next answer already follows the new version."""
    if not isinstance(changes, dict):
        raise PersonaError("the changes must be a JSON object, like {\"tone\": \"very formal\"}")
    if key not in list_personas(config.personas_dir):
        raise PersonaError(f"persona '{key}' not found (available: {', '.join(list_personas(config.personas_dir)) or 'none'})")
    try:
        current = load_persona(key, config.personas_dir)
    except PersonaError:
        current = {}  # a broken file can be repaired by sending every field
    persona = save_persona(key, {**current, **changes}, config.personas_dir, new=False)
    if session.get("persona") == key:
        apply_persona_to_chat(config, session, messages, persona)
        if any(m["role"] in ("user", "assistant") for m in messages):
            messages.append({"role": "system", "content": edit_note(persona)})
    return persona


def remove_persona(config: AppConfig, session: dict, key: str) -> None:
    if key == session.get("persona"):
        raise PersonaError(f"'{key}' is the persona answering in this chat: switch to another one first")
    if key == config.persona:
        raise PersonaError(f"'{key}' is the default persona that new chats start with: it can't be deleted")
    delete_persona(key, config.personas_dir)


def describe_fields(persona: dict) -> str:
    return "\n".join(f"{field}: {persona.get(field) or '(none)'}" for field in FIELDS)


def run_persona_command(config: AppConfig, session: dict, messages: list, text: str) -> dict:
    current = session.get("persona")
    words = text.split(maxsplit=2)

    if not text:
        rows = persona_rows(config, current)
        lines = []
        for row in rows:
            marker = "*" if row["current"] else " "
            if "problem" in row:
                lines.append(f"{marker} {row['key']:8} (broken: {row['problem']})")
            else:
                lines.append(f"{marker} {row['key']:8} {row['name']}: {row['role']}")
        lines.append("Switch with: /persona <name>. Manage with: /persona show|add|edit|delete")
        return {"ok": True, "command": "persona", "action": "list", "personas": rows, "output": "\n".join(lines)}

    if words[0] in RESERVED_KEYS:
        action = words[0]
        try:
            if action == "show" and len(words) == 2:
                key = find_persona_key(words[1], config.personas_dir)
                persona = load_persona(key, config.personas_dir)
                return {"ok": True, "command": "persona", "action": "show", "key": key, "persona": persona,
                        "output": describe_fields(persona)}
            if action == "add" and len(words) == 3:
                persona = create_persona(config, words[1], read_persona_source(words[2].strip()))
                return {"ok": True, "command": "persona", "action": "add", "key": words[1], "persona": persona,
                        "output": f"persona '{words[1]}' ({persona['name']}) added: /persona {words[1]} to talk to it"}
            if action == "edit" and len(words) == 3:
                persona = update_persona(config, session, messages, words[1], read_persona_source(words[2].strip()))
                applied = " and applied to this chat" if current == words[1] else ""
                return {"ok": True, "command": "persona", "action": "edit", "key": words[1], "persona": persona,
                        "output": f"persona '{words[1]}' saved{applied}:\n{describe_fields(persona)}"}
            if action == "delete" and len(words) == 2:
                remove_persona(config, session, words[1])
                return {"ok": True, "command": "persona", "action": "delete", "key": words[1],
                        "output": f"persona '{words[1]}' deleted"}
        except (PersonaError, PromptError) as error:
            return persona_failed(error, action)
        return {"ok": False, "command": "persona", "usage": True, "output": PERSONA_USAGE}

    key = find_persona_key(text, config.personas_dir)
    try:
        persona = load_persona(key, config.personas_dir)
        base_prompt = load_system_prompt(config.system_prompt_path)
        profile = load_profile(
            config.profile_path,
            required=config.profile_path != PROFILE_PATH,
        )
    except (PersonaError, PromptError) as error:
        return persona_failed(error, "switch")

    set_system_message(session, messages, build_system_prompt(base_prompt, profile, persona))
    started = any(m["role"] in ("user", "assistant") for m in messages)
    if started and current != key:
        try:
            previous_name = load_persona(current, config.personas_dir)["name"] if current else "another assistant"
        except PersonaError:
            previous_name = current
        messages.append({"role": "system", "content": switch_note(previous_name, persona)})
    session["persona"] = key
    return {
        "ok": True,
        "command": "persona",
        "action": "switch",
        "persona": key,
        "name": persona["name"],
        "output": f"now talking to {persona['name']}. The conversation so far is kept.",
    }


def run_prompt_compare(
    model: str,
    prompt_path: str,
    version_a: int,
    version_b: int,
    question: str,
    host: str,
) -> dict:
    try:
        content_a = get_version(prompt_path, version_a)
        content_b = get_version(prompt_path, version_b)
    except VersionError as error:
        return {
            "ok": False,
            "command": "prompt",
            "action": "compare",
            "error": str(error),
            "output": f"error: {error}",
        }

    comparisons = []
    chunks = []
    # Ollama doesn't know cloud models: they answer through the same client as the chat
    cloud = OpenRouterClient(model) if model.startswith(REMOTE_PREFIX) else None
    for version, content in ((version_a, content_a), (version_b, content_b)):
        messages = [
            {"role": "system", "content": content},
            {"role": "user", "content": question},
        ]
        start = time.perf_counter()
        try:
            if cloud is not None:
                reply = cloud.chat(messages, tools=[], options={"temperature": 0}).content
            else:
                reply, _ = ask_ollama(model, messages, None, False, host)
        except (StructuredOutputError, OllamaError) as error:
            comparisons.append({"version": version, "error": str(error)})
            chunks.append(f"\n--- v{version} ---\nerror: {error}")
            continue
        elapsed = time.perf_counter() - start
        comparisons.append({
            "version": version,
            "reply": reply,
            "duration_s": elapsed,
        })
        chunks.append(f"\n--- v{version} ({elapsed:.2f}s) ---\n{reply}")

    return {
        "ok": True,
        "command": "prompt",
        "action": "compare",
        "comparisons": comparisons,
        "output": "\n".join(chunks),
    }


def run_prompt_command(
    model: str,
    config: AppConfig,
    session: dict,
    messages: list,
    prompt_state: dict,
    text: str,
) -> dict:
    verb, _, rest = text.partition(" ")
    rest = rest.strip()
    prompt_path = config.system_prompt_path

    if not verb or verb == "versions":
        versions = list_versions(prompt_path)
        if not versions:
            output = "(no versions saved yet)"
        else:
            output = "\n".join(f"v{version}  {when}" for version, when in versions)
        return {
            "ok": True,
            "command": "prompt",
            "action": "versions",
            "versions": [{"version": v, "time": when} for v, when in versions],
            "output": output,
        }

    if verb == "show":
        if not rest.isdigit():
            return {
                "ok": False,
                "command": "prompt",
                "action": "show",
                "usage": True,
                "output": "usage: /prompt show <version>",
            }
        try:
            content = get_version(prompt_path, int(rest))
        except VersionError as error:
            return {
                "ok": False,
                "command": "prompt",
                "action": "show",
                "error": str(error),
                "output": f"error: {error}",
            }
        return {
            "ok": True,
            "command": "prompt",
            "action": "show",
            "version": int(rest),
            "content": content,
            "output": content,
        }

    if verb == "rollback":
        if not rest.isdigit():
            return {
                "ok": False,
                "command": "prompt",
                "action": "rollback",
                "usage": True,
                "output": "usage: /prompt rollback <version>",
            }
        try:
            new_version = apply_rollback(session, messages, config, int(rest))
        except (VersionError, PromptError, PersonaError) as error:
            return {
                "ok": False,
                "command": "prompt",
                "action": "rollback",
                "error": str(error),
                "output": f"error: {error}",
            }
        prompt_state["version"] = new_version
        session["prompt_version"] = new_version
        return {
            "ok": True,
            "command": "prompt",
            "action": "rollback",
            "version": new_version,
            "output": f"rolled back to v{rest} and applied it to this conversation.",
        }

    if verb == "compare":
        version_a, _, remainder = rest.partition(" ")
        version_b, _, question = remainder.partition(" ")
        question = question.strip()
        if not (version_a.isdigit() and version_b.isdigit() and question):
            return {
                "ok": False,
                "command": "prompt",
                "action": "compare",
                "usage": True,
                "output": "usage: /prompt compare <v1> <v2> <question>",
            }
        return run_prompt_compare(
            model,
            prompt_path,
            int(version_a),
            int(version_b),
            question,
            config.host,
        )

    return {
        "ok": False,
        "command": "prompt",
        "usage": True,
        "output": "usage: /prompt [versions|show <n>|rollback <n>|compare <n1> <n2> <question>]",
    }


def run_branches_command(session: dict, messages: list, text: str) -> dict:
    text = text.strip()

    if not text:
        leaves = list_leaves(session)
        if len(leaves) <= 1:
            output = "(only one branch so far, edit a past message with /edit to create more)"
        else:
            lines = [
                f'{"*" if is_current else " "} {leaf_id}  "{preview}"'
                for leaf_id, is_current, preview in leaves
            ]
            lines.append("Switch with: /branches switch <id>")
            output = "\n".join(lines)
        return {
            "ok": True,
            "command": "branches",
            "action": "list",
            "branches": [
                {"id": leaf_id, "current": is_current, "preview": preview}
                for leaf_id, is_current, preview in leaves
            ],
            "output": output,
        }

    verb, _, rest = text.partition(" ")
    if verb == "switch":
        leaf_id = rest.strip()
        try:
            new_messages = switch_branch(session, leaf_id)
        except BranchError as error:
            return {
                "ok": False,
                "command": "branches",
                "action": "switch",
                "error": str(error),
                "output": f"error: {error}",
            }
        messages[:] = new_messages
        turns = sum(m["role"] == "user" for m in messages)
        return {
            "ok": True,
            "command": "branches",
            "action": "switch",
            "branch_id": leaf_id,
            "turns": turns,
            "output": f"switched to branch {leaf_id} ({turns} turn(s))",
        }

    return {
        "ok": False,
        "command": "branches",
        "usage": True,
        "output": "usage: /branches [switch <id>]",
    }


def run_edit_command(session: dict, messages: list, text: str) -> dict:
    n_str, _, new_content = text.partition(" ")
    new_content = new_content.strip()

    if not n_str.isdigit() or not new_content:
        return {
            "ok": False,
            "command": "edit",
            "usage": True,
            "output": "usage: /edit <n> <new text>",
        }

    try:
        prefix = fork_before_user_turn(session, int(n_str))
    except BranchError as error:
        return {
            "ok": False,
            "command": "edit",
            "error": str(error),
            "output": f"error: {error}",
        }

    messages[:] = prefix
    return {
        "ok": True,
        "command": "edit",
        "turn": int(n_str),
        "new_content": new_content,
        "output": f"forked a new branch before message {n_str}; sending the edited message...",
    }


PERMISSIONS_USAGE = "usage: /permissions [set <path> <none|read|write> | remove <path> | default <none|read|write>]"


def permissions_view(permissions: dict) -> dict:
    return {
        "workspace": os.path.basename(WORKSPACE_DIR),
        "default": permissions["default"],
        "rules": [{"path": key or ".", "access": access} for key, access in sorted(permissions["rules"].items())],
    }


def run_permissions_command(config: AppConfig, session: dict, messages: list, text: str) -> dict:
    verb, _, rest = text.strip().partition(" ")
    rest = rest.strip()
    try:
        permissions = load_permissions()
        if not verb:
            return {
                "ok": True,
                "command": "permissions",
                "permissions": permissions_view(permissions),
                "output": f"File permissions in {WORKSPACE_DIR}:\n{describe_permissions(permissions)}\n{PERMISSIONS_USAGE}",
            }

        path, _, level = rest.rpartition(" ")
        if verb == "default" and rest in LEVELS:
            permissions["default"] = rest
            action = f"default is now {rest}"
        elif verb == "set" and path and level in LEVELS:
            key = workspace_key(path)
            permissions["rules"][key] = level
            action = f"{key or '.'} is now {level}"
        elif verb == "remove" and rest:
            key = workspace_key(rest)
            if permissions["rules"].pop(key, None) is None:
                raise PermissionsError(f"no rule for {key or '.'}")
            action = f"removed the rule for {key or '.'}"
        else:
            return {"ok": False, "command": "permissions", "usage": True, "output": PERMISSIONS_USAGE}

        save_permissions(permissions)
        # the model is told the rules in its system prompt, so rebuild it
        base_prompt = load_system_prompt(config.system_prompt_path)
        profile = load_profile(config.profile_path, required=config.profile_path != PROFILE_PATH)
        prompt = build_system_prompt(base_prompt, profile, active_persona(session, config))
    except (PermissionsError, PromptError, PersonaError, OSError) as error:
        return {
            "ok": False,
            "command": "permissions",
            "error": str(error),
            "output": f"error: {error}",
        }

    set_system_message(session, messages, prompt)
    # small models trust earlier "denied" results over the system prompt, so say it where it changed
    if any(m["role"] in ("user", "assistant") for m in messages):
        messages.append({
            "role": "system",
            "content": (
                f"The user just changed the file permissions ({action}). They are now:\n"
                f"{describe_permissions(permissions)}\n"
                "Earlier \"denied\" results may no longer apply: if the user asks again, call the tool again. "
                "For a path you can no longer read, do not answer from earlier messages: "
                "say you no longer have access to it."
            ),
        })
    return {
        "ok": True,
        "command": "permissions",
        "permissions": permissions_view(permissions),
        "output": f"{action}\n{describe_permissions(permissions)}",
    }


def workspace_key(path: str) -> str:
    key = resolve(path)
    if key is None:
        raise PermissionsError(f"{path!r} is outside the workspace")
    return key


def dispatch_command(
    user_input: str,
    *,
    config: AppConfig,
    model: str,
    messages: list,
    session: dict,
    prompt_state: dict,
    guard_config: dict | None = None,
    on_progress=None,
) -> dict | None:
    if user_input == "/stats":
        return run_stats_command(config)

    if user_input == "/json" or user_input.startswith("/json "):
        return run_guarded_json_command(
            model,
            messages,
            user_input[len("/json"):].strip(),
            config,
            session,
            guard_config,
        )

    if user_input == "/prompt" or user_input.startswith("/prompt "):
        return run_prompt_command(
            model,
            config,
            session,
            messages,
            prompt_state,
            user_input[len("/prompt"):].strip(),
        )

    if user_input == "/persona" or user_input.startswith("/persona "):
        return run_persona_command(
            config,
            session,
            messages,
            user_input[len("/persona"):].strip(),
        )

    if user_input == "/branches" or user_input.startswith("/branches "):
        return run_branches_command(
            session,
            messages,
            user_input[len("/branches"):].strip(),
        )

    if user_input == "/edit" or user_input.startswith("/edit "):
        return run_edit_command(
            session,
            messages,
            user_input[len("/edit"):].strip(),
        )

    if user_input == "/permissions" or user_input.startswith("/permissions "):
        return run_permissions_command(
            config,
            session,
            messages,
            user_input[len("/permissions"):].strip(),
        )

    if user_input == "/ingest":
        return run_ingest_command(config, on_progress)

    if user_input == "/docs":
        return run_docs_command(config)

    if user_input == "/rag" or user_input.startswith("/rag "):
        return run_rag_command(config, user_input[len("/rag"):].strip())

    if user_input == "/kb" or user_input.startswith("/kb "):
        return run_kb_command(config, session, user_input[len("/kb"):].strip())

    if user_input == "/cache" or user_input.startswith("/cache "):
        return run_cache_command(config, user_input[len("/cache"):].strip())

    if user_input == "/schemas" or user_input.startswith("/schemas "):
        return run_schemas_command(config, user_input[len("/schemas"):].strip())

    if user_input == "/guardrails" or user_input.startswith("/guardrails "):
        return run_guardrails_command(config, guard_config, user_input[len("/guardrails"):].strip())

    return None
