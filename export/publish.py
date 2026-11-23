import html
import json
import os
import re
import secrets

from guardrails.pii import mask_pii
from storage.session_store import load_session

SHARES_DIR = "shared"
TOKEN = re.compile(r"[A-Za-z0-9_-]{16}")


def publish_session(session_id: str, sessions_dir: str = "sessions", shares_dir: str = SHARES_DIR) -> str:
    # frozen snapshot: no system prompt, no tool traffic, PII masked
    session, messages = load_session(sessions_dir, session_id)
    snapshot = {
        "title": session.get("title") or f"Conversation {session_id}",
        "model": session.get("model", ""),
        "created_at": session.get("created_at", ""),
        "messages": [
            {"role": m["role"], "content": mask_pii(m["content"])[0]}
            for m in messages
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"]
        ],
    }
    token = secrets.token_urlsafe(12)
    os.makedirs(shares_dir, exist_ok=True)
    with open(os.path.join(shares_dir, f"{token}.json"), "w", encoding="utf-8") as file:
        json.dump(snapshot, file, indent=2, ensure_ascii=False)
    return token


def load_share(token: str, shares_dir: str = SHARES_DIR) -> dict | None:
    if not TOKEN.fullmatch(token):
        return None
    try:
        with open(os.path.join(shares_dir, f"{token}.json"), encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def render_share(snapshot: dict) -> str:
    title = html.escape(snapshot["title"])
    rows = "\n".join(
        f'<div class="msg {m["role"]}"><b>{"You" if m["role"] == "user" else "Sensai"}</b>'
        f'<p>{html.escape(m["content"])}</p></div>'
        for m in snapshot["messages"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 0 auto; padding: 24px 16px; background: #fafafa; color: #222; }}
.meta {{ color: #777; font-size: 14px; }}
.msg {{ background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; padding: 12px 16px; margin: 12px 0; }}
.msg.user {{ background: #eef4ff; }}
.msg p {{ white-space: pre-wrap; margin: 6px 0 0; }}
</style></head><body>
<h1>{title}</h1>
<p class="meta">{html.escape(snapshot["model"])} · {html.escape(snapshot["created_at"])}</p>
{rows}
</body></html>
"""
