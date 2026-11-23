import json
import os
from datetime import datetime

from core.config import VERSIONS_DIR


class VersionError(Exception):
    pass


def _versions_path(prompt_path, versions_dir=VERSIONS_DIR):
    name = os.path.basename(prompt_path)
    return os.path.join(versions_dir, f"{name}.jsonl")


def _read_versions(prompt_path, versions_dir=VERSIONS_DIR):
    path = _versions_path(prompt_path, versions_dir)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    versions = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            versions.append(json.loads(line))
        except ValueError:
            continue
    return versions


def snapshot_if_changed(prompt_path, content):
    """Append a new version if content differs from the latest saved one.
    Returns the new version number, or the existing one if nothing changed."""
    versions = _read_versions(prompt_path)
    if versions and versions[-1]["content"] == content:
        return versions[-1]["version"]

    version = (versions[-1]["version"] + 1) if versions else 1
    record = {
        "version": version,
        "time": datetime.now().isoformat(timespec="seconds"),
        "content": content,
    }

    path = _versions_path(prompt_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        raise VersionError(f"cannot write prompt version to {path}: {e}")

    return version


def list_versions(prompt_path):
    return [(v["version"], v["time"]) for v in _read_versions(prompt_path)]


def get_version(prompt_path, version):
    for v in _read_versions(prompt_path):
        if v["version"] == version:
            return v["content"]
    raise VersionError(f"version {version} not found for {prompt_path}")


def rollback(prompt_path, version):
    content = get_version(prompt_path, version)  # raises if missing

    try:
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        raise VersionError(f"cannot write {prompt_path}: {e}")

    return snapshot_if_changed(prompt_path, content)
