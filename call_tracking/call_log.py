import json
import sys
from datetime import datetime


def log_call(path, record):
    record["time"] = datetime.now().isoformat(timespec="seconds")

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"warning: could not write to log {path}: {e}", file=sys.stderr)


def count(records, key) -> dict:
    counts = {}
    for record in records:
        counts[record.get(key, "unknown")] = counts.get(record.get(key, "unknown"), 0) + 1
    return counts


def add_pii(records) -> dict:
    counts = {}
    for record in records:
        for name, n in (record.get("pii") or {}).items():
            counts[name] = counts.get(name, 0) + n
    return counts


def guardrail_stats(calls) -> dict:
    inputs = [c for c in calls if c.get("guardrail") == "input"]
    masks = [c for c in calls if c.get("guardrail") == "output" and c.get("action") == "mask"]
    answers = [c for c in calls if c.get("guardrail") == "output" and c.get("action") in ("allow", "block")]
    refused = [c for c in inputs if c.get("action") == "block"]
    withdrawn = [c for c in answers if c.get("action") == "block"]
    return {
        "messages_checked": len(inputs),
        "messages_refused": count(refused, "category"),
        "answers_checked": len(answers),
        "answers_withdrawn": count(withdrawn, "category"),
        "masked_in_messages": add_pii(c for c in inputs if c.get("action") != "block"),
        "masked_in_answers": add_pii(masks),
    }


def compute_stats(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    calls = []
    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            calls.append(json.loads(line))
        except ValueError:
            continue

    model_calls = [c for c in calls if "model" in c]
    tool_decisions = [c for c in calls if "tool" in c]
    checks = [c for c in calls if c.get("hallucination") == "check" and not c.get("error")]
    cache_hits = [c for c in calls if c.get("cache") == "hit"]

    errors = [c for c in model_calls if c.get("error")]
    ok = [c for c in model_calls if not c.get("error")]

    avg_latency = 0.0
    total_tokens = 0
    tok_per_sec = 0.0

    if ok:
        avg_latency = sum(c.get("total_duration_s", 0) for c in ok) / len(ok)
        total_tokens = sum(c.get("response_tokens") or 0 for c in ok)
        total_eval_time = sum(c.get("eval_duration_s", 0) for c in ok)
        tok_per_sec = total_tokens / total_eval_time if total_eval_time else 0.0

    by_decision = {}
    for d in tool_decisions:
        key = d.get("decision", "unknown")
        by_decision[key] = by_decision.get(key, 0) + 1

    flagged = [c for c in checks if c.get("flagged")]
    by_action = {}
    for c in flagged:
        key = c.get("action", "unknown")
        by_action[key] = by_action.get(key, 0) + 1

    return {
        "total_records": len(calls),
        "model_calls": len(model_calls),
        "ok_calls": len(ok),
        "errors": len(errors),
        "error_rate": (len(errors) / len(model_calls)) if model_calls else 0.0,
        "avg_latency_s": avg_latency,
        "tokens_per_s": tok_per_sec,
        "total_tokens": total_tokens,
        "tool_decisions_total": len(tool_decisions),
        "tool_decisions": by_decision,
        "checks": len(checks),
        "flagged_answers": len(flagged),
        "flagged_rate": (len(flagged) / len(checks)) if checks else 0.0,
        "check_actions": by_action,
        "cache_hits": len(cache_hits),
        "cache_saved_s": sum(c.get("saved_s", 0) for c in cache_hits),
        "guardrails": guardrail_stats(calls),
        "json_replies": len([c for c in calls if "json" in c]),
        "json_retried": len([c for c in calls if "json" in c and c.get("attempts", 1) > 1]),
        "json_failed": len([c for c in calls if "json" in c and not c.get("ok", True)]),
    }


def print_stats(path):
    stats = compute_stats(path)
    if not stats["total_records"]:
        print("(no calls logged yet)")
        return

    if stats["model_calls"]:
        print(
            f"{stats['model_calls']} call(s) logged, "
            f"{stats['errors']} error(s) ({stats['error_rate']:.0%})"
        )

        if stats["ok_calls"]:
            print(
                f"avg latency: {stats['avg_latency_s']:.2f}s, "
                f"{stats['tokens_per_s']:.1f} tokens/s"
            )

    if stats["tool_decisions_total"]:
        summary = ", ".join(f"{v} {k}" for k, v in stats["tool_decisions"].items())
        print(f"{stats['tool_decisions_total']} tool decision(s): {summary}")

LOGGED_RESULT_CHARS = 4000


def log_tool_call(path, name, trace, content, session_id=None):
    log_call(path, {
        "tool_call": name,
        "server": trace.get("server"),
        "session": session_id,
        "round": trace.get("round"),
        "arguments": trace.get("arguments"),
        "decision": trace.get("decision"),
        "ok": trace.get("ok"),
        "duration_s": trace.get("duration_s"),
        "result": content[:LOGGED_RESULT_CHARS],
        "result_chars": len(content),
    })


def recent_tool_calls(path, limit=50) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    calls = []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if "tool_call" in record:
            calls.append(record)
            if len(calls) >= limit:
                break
    return calls
