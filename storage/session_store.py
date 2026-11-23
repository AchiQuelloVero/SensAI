import json
import os
import sys
from datetime import datetime

from core.config import PROFILE_PATH, AppConfig, load_app_config
from prompting.prompt_builder import (
    PromptError,
    build_system_prompt,
    load_profile,
    load_system_prompt,
)
from prompting.persona import load_persona
from prompting.versioning import VersionError, snapshot_if_changed
from storage.branching import BranchError, path_to_messages, sync_tail


class SessionError(Exception):
    pass


def initialize_session(
    config: AppConfig | None = None,
    resume_id: str | None = None,
    model_override: str | None = None,
):
    cfg = config or load_app_config()
    prompt_version = None

    if resume_id:
        session, messages = load_session(cfg.sessions_dir, resume_id)
        model = model_override or session.get("model") or cfg.model
        prompt_version = session.get("prompt_version")
        cfg.rag_collection = session.get("rag_collection") or cfg.rag_collection
    else:
        base_prompt = load_system_prompt(cfg.system_prompt_path)
        try:
            prompt_version = snapshot_if_changed(cfg.system_prompt_path, base_prompt)
        except VersionError as error:
            raise PromptError(str(error)) from error

        profile = load_profile(
            cfg.profile_path,
            required=cfg.profile_path != PROFILE_PATH,
        )
        persona = load_persona(cfg.persona, cfg.personas_dir)
        system_prompt = build_system_prompt(base_prompt, profile, persona)

        now = datetime.now()
        model = model_override or cfg.model

        session = {
            "id": now.strftime("%Y%m%d_%H%M%S"),
            "model": model,
            "persona": cfg.persona,
            "prompt_version": prompt_version,
            "rag_collection": cfg.rag_collection,
            "created_at": now.isoformat(timespec="seconds"),
        }
        messages = [{"role": "system", "content": system_prompt}]

    session["model"] = model
    session_path = session_path_for(cfg.sessions_dir, session["id"])

    return session, messages, model, session_path, prompt_version


def save_session(path, session, messages):
    session["updated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        sync_tail(session, messages)
    except BranchError as e:
        raise SessionError(f"cannot update branch history: {e}") from e
    tmp_path = f"{path}.tmp"

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)

        os.replace(tmp_path, path)

    except OSError as e:
        print(
            f"warning: could not save session to {path}: {e}",
            file=sys.stderr,
        )


def rename_session(sessions_dir, session_id, title):
    """Set a custom title in the saved file, or drop it when title is empty
    (the first message is shown again). The last update time is left alone."""
    path = session_path_for(sessions_dir, session_id)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise SessionError(f"session '{session_id}' not found") from e
    except (OSError, ValueError) as e:
        raise SessionError(f"cannot read session {path}: {e}") from e

    if title:
        data["title"] = title
    else:
        data.pop("title", None)

    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except OSError as e:
        raise SessionError(f"cannot write session {path}: {e}") from e


def delete_session(sessions_dir, session_id):
    path = session_path_for(sessions_dir, session_id)
    try:
        os.remove(path)
    except FileNotFoundError as e:
        raise SessionError(f"session '{session_id}' not found") from e
    except OSError as e:
        raise SessionError(f"cannot delete session {path}: {e}") from e


def session_path_for(sessions_dir, session_id):
    return os.path.join(sessions_dir, f"session_{session_id}.json")


def load_session(sessions_dir, session_id):
    path = session_path_for(sessions_dir, session_id)

    try:
        with open(path, encoding="utf-8") as f:
            session = json.load(f)

    except FileNotFoundError as e:
        raise SessionError(
            f"session '{session_id}' not found in {sessions_dir} "
            "(list them with /sessions)"
        ) from e

    except (OSError, ValueError) as e:
        raise SessionError(f"cannot read session {path}: {e}") from e

    if session.get("tree"):
        try:
            messages = path_to_messages(session)
        except BranchError as e:
            raise SessionError(f"cannot read branch history in {path}: {e}") from e
    else:
        messages = session.get("messages")

    if not isinstance(messages, list) or not messages:
        raise SessionError(f"session file is invalid: {path}")

    return session, messages


def get_session_summaries(sessions_dir):

    try:
        names = sorted(
            name
            for name in os.listdir(sessions_dir)
            if name.startswith("session_") and name.endswith(".json")
        )

    except FileNotFoundError:
        names = []

    except OSError as e:
        raise SessionError(f"cannot read {sessions_dir}: {e}") from e

    rows = []

    for name in names:
        try:
            with open(os.path.join(sessions_dir, name), encoding="utf-8") as f:
                data = json.load(f)

            messages = data["messages"]

            first = next(
                (message["content"] for message in messages
                 if message["role"] == "user"),
                "",
            )

            first = first.replace("\n", " ")
            first = first if len(first) <= 40 else first[:37] + "..."
            first = data.get("title") or first

            turns = sum(
                1 for message in messages if message["role"] == "user"
            )

            rows.append((
                data["id"],
                data.get("updated_at", "?"),
                data.get("model", "?"),
                turns,
                first,
                data.get("persona"),
                data.get("prompt_version"),
            ))

        except (OSError, ValueError, KeyError, TypeError):
            continue

    return rows