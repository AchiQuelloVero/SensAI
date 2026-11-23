from datetime import datetime

from cache.semantic_cache import CacheError
from cache.service import AnswerCache, format_seconds
from core.config import AppConfig, load_app_config, save_app_config

USAGE = (
    "usage: /cache [on|off]           settings, saved answers, time saved\n"
    "       /cache list               the saved answers\n"
    "       /cache delete <id>        forget one saved answer\n"
    "       /cache clear              forget every saved answer\n"
    "       /cache threshold <0-1>    similarity needed to reuse an answer\n"
    "       /cache verify on|off      model confirms it's the same question\n"
    "       /cache mid-chat on|off    also look up questions mid-chat"
)
SWITCHES = {"verify": "cache_verify", "mid-chat": "cache_mid_chat"}


def remember(field: str, value) -> str:
    """Keep the choice for the next start, in config.json only (like /rag). Returns a warning if it failed."""
    try:
        saved = load_app_config()
        setattr(saved, field, value)
        save_app_config(saved)
    except OSError as e:
        return f" (could not save the choice to config.json: {e.strerror or e})"
    return ""


def usage() -> dict:
    return {"ok": False, "command": "cache", "usage": True, "output": USAGE}


def change_setting(config: AppConfig, words: list[str]) -> tuple[str, dict | None]:
    """Applies `/cache <setting> <value>`. Returns (note, None), or ("", usage) if the command is wrong."""
    name, value = words
    if name == "threshold":
        try:
            threshold = float(value)
        except ValueError:
            return "", usage()
        if not 0 <= threshold <= 1:
            return "", usage()
        config.cache_threshold = threshold
        return remember("cache_threshold", threshold), None
    if name in SWITCHES and value in ("on", "off"):
        setattr(config, SWITCHES[name], value == "on")
        return remember(SWITCHES[name], value == "on"), None
    return "", usage()


def short(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


def list_entries(config: AppConfig) -> dict:
    try:
        entries = AnswerCache(config).store().entries()
    except CacheError as error:
        return {"ok": False, "command": "cache", "error": str(error), "output": f"error: {error}"}
    if not entries:
        return {"ok": True, "command": "cache", "action": "list", "entries": [], "output": "(no saved answers)"}
    lines = [f"{'id':>4}  {'saved on':<11}  {'hits':>4}  {'time saved':>10}  question"]
    for e in entries:
        date = datetime.fromtimestamp(e["created"]).strftime("%d/%m %H:%M")
        lines.append(f"{e['id']:>4}  {date:<11}  {e['hits']:>4}  {format_seconds(e['saved_s']):>10}  {short(e['question'], 60)}")
    return {"ok": True, "command": "cache", "action": "list", "entries": entries, "output": "\n".join(lines)}


def delete_entry(config: AppConfig, value: str) -> dict:
    if not value.isdigit():
        return usage()
    try:
        deleted = AnswerCache(config).store().delete(int(value))
    except CacheError as error:
        return {"ok": False, "command": "cache", "error": str(error), "output": f"error: {error}"}
    if not deleted:
        return {"ok": False, "command": "cache", "error": "not found", "output": f"error: no saved answer with id {value}"}
    return {"ok": True, "command": "cache", "action": "delete", "id": int(value), "output": f"saved answer {value} deleted"}


def run_cache_command(config: AppConfig, text: str) -> dict:
    words = text.strip().lower().split()
    choice = words[0] if len(words) == 1 else ""
    if choice == "list":
        return list_entries(config)
    if len(words) == 2 and words[0] == "delete":
        return delete_entry(config, words[1])

    note = ""
    if len(words) == 2:
        note, error = change_setting(config, words)
        if error is not None:
            return error
    elif len(words) > 2 or choice not in ("", "status", "on", "off", "clear"):
        return usage()

    try:
        store = AnswerCache(config).store()
        if choice == "clear":
            note = f" ({store.clear()} answer(s) removed)"
        stats = store.stats()
    except CacheError as error:
        return {"ok": False, "command": "cache", "error": str(error), "output": f"error: {error}"}

    if choice in ("on", "off"):
        config.cache_enabled = choice == "on"
        note = remember("cache_enabled", config.cache_enabled)
    if config.cache_enabled and config.rag_enabled:
        note += " (skipped while RAG is on: answers then depend on your documents)"

    state = "on" if config.cache_enabled else "off"
    settings = (
        f"threshold {config.cache_threshold:.2f}, "
        f"verify {'on' if config.cache_verify else 'off'}, "
        f"mid-chat {'on' if config.cache_mid_chat else 'off'}"
    )
    return {
        "ok": True,
        "command": "cache",
        "action": "status",
        "note": note.strip().strip("()").replace(") (", "; "),
        "enabled": config.cache_enabled,
        "threshold": config.cache_threshold,
        "verify": config.cache_verify,
        "mid_chat": config.cache_mid_chat,
        "entries": stats["entries"],
        "hits": stats["hits"],
        "saved_s": stats["saved_s"],
        "output": (
            f"cache is {state} ({settings}): {stats['entries']} saved answer(s), {stats['hits']} hit(s), "
            f"{format_seconds(stats['saved_s'])} saved{note}"
        ),
    }
