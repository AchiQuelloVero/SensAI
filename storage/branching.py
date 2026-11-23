class BranchError(Exception):
    pass


def _new_node(message, parent):
    return {"message": message, "parent": parent, "children": []}


def ensure_tree(session, messages):
    """Create the tree from scratch the first time a session is saved.
    Older session files (no tree) are left untouched by this."""
    if session.get("tree"):
        return

    if not messages:
        raise BranchError("cannot build a branch tree from an empty session")

    tree = {"0": _new_node(messages[0], parent=None)}
    session["tree"] = tree
    session["next_id"] = 1
    session["current_leaf"] = "0"


def path_ids(session, leaf_id=None):
    """Node ids from root to leaf_id (default: current_leaf), in order."""
    tree = session["tree"]
    leaf_id = leaf_id if leaf_id is not None else session["current_leaf"]
    if leaf_id not in tree:
        raise BranchError(f"unknown branch node: {leaf_id}")

    ids = []
    node_id = leaf_id
    while node_id is not None:
        ids.append(node_id)
        node_id = tree[node_id]["parent"]
    ids.reverse()
    return ids


def path_to_messages(session, leaf_id=None):
    tree = session["tree"]
    return [tree[node_id]["message"] for node_id in path_ids(session, leaf_id)]


def user_node_ids(session, leaf_id=None):
    tree = session["tree"]
    return [
        node_id for node_id in path_ids(session, leaf_id)
        if tree[node_id]["message"]["role"] == "user"
    ]


def sync_tail(session, messages):
    """Append whatever is new in `messages` (beyond the current branch) as
    new nodes, growing the tree from session['current_leaf']. Also handles
    the first-ever save for a session (builds the tree root)."""
    ensure_tree(session, messages)

    already = len(path_to_messages(session))
    new_tail = messages[already:]

    parent = session["current_leaf"]
    tree = session["tree"]

    for message in new_tail:
        node_id = str(session["next_id"])
        session["next_id"] += 1
        tree[node_id] = _new_node(message, parent)
        tree[parent]["children"].append(node_id)
        parent = node_id

    session["current_leaf"] = parent
    session["messages"] = path_to_messages(session)


def fork_before_user_turn(session, n):
    """Move the current branch pointer back to just before the n-th user
    message (1-based) on the current path. Returns the message list up to
    that point — the caller appends the edited text and keeps chatting,
    which naturally grows a new sibling branch on the next save."""
    if not session.get("tree"):
        raise BranchError("no messages to edit yet")

    user_ids = user_node_ids(session)
    if n < 1 or n > len(user_ids):
        raise BranchError(f"no message #{n} in the current branch (there are {len(user_ids)})")

    edit_node = user_ids[n - 1]
    parent = session["tree"][edit_node]["parent"]
    if parent is None:
        raise BranchError("cannot edit the very first message")

    session["current_leaf"] = parent
    return path_to_messages(session, parent)


def list_leaves(session):
    """Every branch tip: (leaf_id, is_current, short preview of its last user message)."""
    tree = session.get("tree")
    if not tree:
        return []
    leaves = [node_id for node_id, node in tree.items() if not node["children"]]

    rows = []
    for leaf_id in leaves:
        preview = ""
        for node_id in reversed(path_ids(session, leaf_id)):
            message = tree[node_id]["message"]
            if message["role"] == "user":
                preview = message["content"].replace("\n", " ")
                preview = preview if len(preview) <= 50 else preview[:47] + "..."
                break
        rows.append((leaf_id, leaf_id == session["current_leaf"], preview))

    return rows


def _last_leaf(tree, node_id):
    while tree[node_id]["children"]:
        node_id = tree[node_id]["children"][-1]
    return node_id


def turn_tree(session):
    """The conversation as a tree of user messages, in creation order. Each row:
    id, parent (nearest user message above it, or None), text, leaf (the branch tip
    to switch to: the newest one below it), on_path, current (it is the last user
    message of the current branch), version/versions (which of the alternative
    versions of the same message it is, when it was edited)."""
    tree = session.get("tree")
    if not tree:
        return []

    on_path = path_ids(session)
    on_path_set = set(on_path)
    last_user_on_path = next((i for i in reversed(on_path) if tree[i]["message"]["role"] == "user"), None)

    def nearest_user(node_id):
        node_id = tree[node_id]["parent"]
        while node_id is not None and tree[node_id]["message"]["role"] != "user":
            node_id = tree[node_id]["parent"]
        return node_id

    rows = []
    for node_id, node in tree.items():
        if node["message"]["role"] != "user":
            continue
        siblings = [c for c in tree[node["parent"]]["children"] if tree[c]["message"]["role"] == "user"]
        rows.append({
            "id": node_id,
            "parent": nearest_user(node_id),
            "text": node["message"]["content"],
            "leaf": _last_leaf(tree, node_id),
            "on_path": node_id in on_path_set,
            "current": node_id == last_user_on_path,
            "version": siblings.index(node_id) + 1,
            "versions": len(siblings),
        })
    return rows


def path_forks(session):
    """For the user messages of the current branch that were edited: turn number
    (1-based) -> which version this is and the branch tip of each version."""
    rows = {row["id"]: row for row in turn_tree(session)}
    tree = session.get("tree") or {}
    forks = {}
    turn = 0
    for node_id in path_ids(session) if tree else []:
        if node_id not in rows:
            continue
        turn += 1
        row = rows[node_id]
        if row["versions"] > 1:
            siblings = [c for c in tree[tree[node_id]["parent"]]["children"] if c in rows]
            forks[turn] = {
                "version": row["version"],
                "versions": row["versions"],
                "leaves": [rows[c]["leaf"] for c in siblings],
            }
    return forks


def set_root_message(session, message):
    """Replace the first message (the system prompt) shared by every branch."""
    tree = session.get("tree")
    if tree:
        tree[path_ids(session)[0]]["message"] = message


def switch_branch(session, leaf_id):
    tree = session.get("tree") or {}
    if leaf_id not in tree:
        raise BranchError(f"unknown branch: {leaf_id}")

    session["current_leaf"] = leaf_id
    return path_to_messages(session)
