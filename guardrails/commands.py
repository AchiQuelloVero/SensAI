import copy

from cache.commands import remember
from core.config import AppConfig
from guardrails.config import ACTIONS, CATEGORIES, GuardrailError, load_config, save_config
from guardrails.pii import PII_TYPES

USAGE = (
    "usage: /guardrails [on|off]               the rules, or every check on/off\n"
    "       /guardrails pii <type> <action>    allow, mask or block a data type\n"
    "       /guardrails refuse <cat> on|off    refuse a kind of message or not\n"
    "       /guardrails output on|off          withdraw unsafe answers\n"
    "       /guardrails message <text>         the reply to a refused message\n"
    "types: EMAIL, PHONE, IBAN, CARD\n"
    "categories: off_topic, harmful, asks_for_others_data"
)


def usage() -> dict:
    return {"ok": False, "command": "guardrails", "usage": True, "output": USAGE}


def failed(error) -> dict:
    return {"ok": False, "command": "guardrails", "error": str(error), "output": f"error: {error}"}


def guardrails_view(config: AppConfig, rules: dict) -> dict:
    return {
        "enabled": not config.no_guardrails,
        "pii": rules["pii"],
        "blocked_categories": rules["blocked_categories"],
        "blocked_message": rules["blocked_message"],
        "output_check": rules.get("output_check", True),
    }


def describe(view: dict) -> str:
    on = lambda value: "on" if value else "off"
    allowed = [c for c in CATEGORIES if c not in view["blocked_categories"]]
    lines = [
        f"guardrails: {on(view['enabled'])}" + ("" if view["enabled"] else " (/guardrails on to check messages again)"),
        f"  personal data: {' · '.join(f'{name} {action}' for name, action in view['pii'].items())}",
        f"  refused: {', '.join(view['blocked_categories']) or 'nothing'}" + (f" (allowed: {', '.join(allowed)})" if allowed else ""),
        f"  answer check: {on(view['output_check'])}",
        f"  refusal message: {view['blocked_message']}",
    ]
    return "\n".join(lines)


def change_rule(rules: dict, words: list[str]) -> dict | None:
    """The rules after `/guardrails <words>`, or None if the command is wrong."""
    rules = copy.deepcopy(rules)
    name, args = words[0], words[1:]
    if name == "pii" and len(args) == 2 and args[0].upper() in PII_TYPES and args[1] in ACTIONS:
        rules["pii"][args[0].upper()] = args[1]
    elif name == "refuse" and len(args) == 2 and args[0] in CATEGORIES and args[1] in ("on", "off"):
        refused = [c for c in rules["blocked_categories"] if c != args[0]]
        if args[1] == "on":
            refused.append(args[0])
        rules["blocked_categories"] = [c for c in CATEGORIES if c in refused]
    elif name == "output" and args in (["on"], ["off"]):
        rules["output_check"] = args[0] == "on"
    elif name == "message" and args:
        rules["blocked_message"] = " ".join(args)
    else:
        return None
    return rules


def run_guardrails_command(config: AppConfig, guard_config: dict | None, text: str) -> dict:
    """`guard_config` is the live rules dict (None while guardrails are off): it is changed in place,
    so the next message uses the new rules. Turning them on or off only sets `config.no_guardrails`:
    the caller loads the rules again or drops them."""
    words = text.split()
    try:
        rules = guard_config if guard_config is not None else load_config(config.guardrails_config_path)
    except GuardrailError as error:
        return failed(error)

    note = ""
    if words in (["on"], ["off"]):
        config.no_guardrails = words[0] == "off"
        note = remember("no_guardrails", config.no_guardrails)
    elif words:
        changed = change_rule(rules, words)
        if changed is None:
            return usage()
        try:
            save_config(changed, config.guardrails_config_path)
        except GuardrailError as error:
            return failed(error)
        if guard_config is not None:
            guard_config.clear()
            guard_config.update(changed)
        rules = changed

    view = guardrails_view(config, rules)
    output = describe(view)
    if words:
        output = "saved, applies to the next message" + note + "\n" + output
    return {"ok": True, "command": "guardrails", "guardrails": view, "changed": bool(words), "output": output}
