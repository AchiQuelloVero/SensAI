from guardrails.classifier import check_input, check_output
from guardrails.pii import mask_pii
from structured import StructuredOutputError


def types_with_action(config, action):
    return tuple(name for name, value in config["pii"].items() if value == action)


def guard_input(model, text, config):
    _, found = mask_pii(text, types_with_action(config, "block"))
    if found:
        return {"action": "block", "category": "pii", "reason": f"contains {', '.join(found)}", "pii": found}

    text, masked = mask_pii(text, types_with_action(config, "mask"))
    try:
        check = check_input(model, text, config)
    except StructuredOutputError as error:
        return {"action": "block", "category": "check_failed", "reason": str(error), "pii": masked}

    if check["blocked"]:
        return {"action": "block", "category": check["category"], "reason": check["reason"], "pii": masked}
    return {
        "action": "mask" if masked else "allow",
        "category": check["category"],
        "reason": check["reason"],
        "pii": masked,
        "text": text,
    }


def guard_output(model, question, answer, config):
    if not config.get("output_check", True) or not answer.strip():
        return None
    try:
        check = check_output(model, question, answer)
    except StructuredOutputError as error:
        return {"action": "block", "category": "check_failed", "reason": str(error)}
    if check["category"] == "safe":
        return {"action": "allow", "category": "safe", "reason": check["reason"]}
    return {"action": "block", "category": check["category"], "reason": check["reason"]}
