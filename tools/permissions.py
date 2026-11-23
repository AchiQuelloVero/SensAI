import json
import os

from core.config import PERMISSIONS_PATH, WORKSPACE_DIR

LEVELS = ["none", "read", "write"]

NEEDS = {
    "read_file": "read",
    "read_text_file": "read",
    "read_media_file": "read",
    "read_multiple_files": "read",
    "list_directory": "read",
    "list_directory_with_sizes": "read",
    "directory_tree": "read",
    "search_files": "read",
    "get_file_info": "read",
    "write_file": "write",
    "edit_file": "write",
    "create_directory": "write",
    "move_file": "write",
}


class PermissionsError(Exception):
    pass


def load_permissions(path=PERMISSIONS_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"default": "write", "rules": {}}
    except (OSError, ValueError) as e:
        raise PermissionsError(f"cannot read permissions file {path}: {e}")

    if not isinstance(data, dict):
        raise PermissionsError(f"permissions file must contain a JSON object: {path}")

    default = data.get("default", "write")
    if default not in LEVELS:
        raise PermissionsError(f"invalid default access {default!r}, expected one of {LEVELS}")

    rules = {}
    for rule in data.get("rules", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("path"), str):
            raise PermissionsError(f"each rule needs a 'path' string: {rule!r}")
        if rule.get("access") not in LEVELS:
            raise PermissionsError(f"invalid access for {rule['path']!r}, expected one of {LEVELS}")
        rules[rule_key(rule["path"])] = rule["access"]

    return {"default": default, "rules": rules}


def rule_key(path):
    key = os.path.normpath(path).strip("/")
    return "" if key == "." else key


def save_permissions(permissions, path=PERMISSIONS_PATH):
    rules = ",\n".join(
        "    " + json.dumps({"path": key or ".", "access": access})
        for key, access in sorted(permissions["rules"].items())
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'{{\n  "default": {json.dumps(permissions["default"])},\n  "rules": [\n{rules}\n  ]\n}}\n')


LABELS = {"none": "no access", "read": "read only", "write": "read and write"}


def describe_permissions(permissions):
    lines = [f"- everywhere else: {LABELS[permissions['default']]}"]
    for key, access in sorted(permissions["rules"].items()):
        lines.append(f"- {key or '. (workspace root)'}: {LABELS[access]}")
    return "\n".join(lines)


def paths_of(args):
    paths = [args[key] for key in ("path", "source", "destination") if key in args]
    paths.extend(args.get("paths", []))
    return paths


# path relative to the workspace ("" for its root), or None if it points outside
def resolve(path, workspace=WORKSPACE_DIR):
    if not isinstance(path, str):
        return None
    root = os.path.realpath(workspace)
    full = os.path.realpath(os.path.join(root, os.path.expanduser(path)))
    if os.path.commonpath([root, full]) != root:
        return None
    rel = os.path.relpath(full, root)
    return "" if rel == "." else rel


def access_for(rel, permissions):
    rules = permissions["rules"]
    while True:
        if rel in rules:
            return rules[rel]
        if not rel:
            return permissions["default"]
        rel = os.path.dirname(rel)


# None if the call is allowed, otherwise the reason it is denied
def check(tool, args, permissions=None, workspace=WORKSPACE_DIR):
    need = NEEDS.get(tool)
    if need is None:
        return None
    if permissions is None:
        try:
            permissions = load_permissions()
        except PermissionsError as e:
            return f"{tool} denied: {e}"

    paths = paths_of(args)
    if not paths:
        return f"{tool} denied: no 'path' argument, so the access cannot be checked"
    for path in paths:
        rel = resolve(path, workspace)
        if rel is None:
            return f"{tool} denied: {path!r} is outside the workspace"
        access = access_for(rel, permissions)
        if LEVELS.index(access) < LEVELS.index(need):
            return f"{tool} denied: {need} access needed for {rel or '.'!r}, you have {access}"
    return None
