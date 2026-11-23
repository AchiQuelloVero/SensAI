import { api } from "./api.js";
import { chat } from "./dom.js";
import { busy } from "./state.js";
import { renderSession, sendMessage } from "./chat.js";

export function startEdit(turn, bubble, editBtn) {
  if (busy || bubble.nextElementSibling?.classList.contains("edit-input")) return;
  const currentText = bubble.textContent;

  const width = bubble.getBoundingClientRect().width;

  const textarea = document.createElement("textarea");
  textarea.className = "edit-input";
  textarea.value = currentText;
  textarea.rows = 1;
  textarea.style.width = `${width}px`;

  const actions = document.createElement("div");
  actions.className = "edit-actions";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "settings-btn-secondary";
  cancelBtn.textContent = "Cancelar";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "settings-btn-primary";
  saveBtn.textContent = "Aceptar";
  actions.append(cancelBtn, saveBtn);

  const resize = () => { textarea.style.height = "auto"; textarea.style.height = textarea.scrollHeight + "px"; };
  textarea.addEventListener("input", resize);

  const finish = () => {
    bubble.hidden = false;
    editBtn.hidden = false;
    textarea.remove();
    actions.remove();
  };

  const submit = () => {
    const text = textarea.value.trim();
    finish();
    if (!text || text === currentText.trim()) return;
    forkAndSend(turn, text);
  };

  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    else if (e.key === "Escape") { e.preventDefault(); finish(); }
  });
  saveBtn.addEventListener("click", submit);
  cancelBtn.addEventListener("click", finish);

  bubble.hidden = true;
  editBtn.hidden = true;
  bubble.after(textarea);
  textarea.after(actions);
  resize();
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
}

// fork the conversation before the n-th user message, then send the new text on the new branch
export async function forkAndSend(turn, text) {
  try {
    const forked = await api("/edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n: turn, text }),
    });
    renderSession(forked);
    await sendMessage(text);
  } catch (err) {
    alert(err.message);
  }
}

// "‹ 2/3 ›" under an edited message: step between its versions, each one a branch
function versionSwitch(fork) {
  const box = document.createElement("div");
  box.className = "versions";
  const step = (label, text, target) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ver-btn";
    btn.textContent = text;
    btn.setAttribute("aria-label", label);
    btn.disabled = !target;
    if (target) btn.addEventListener("click", () => switchBranch(target));
    return btn;
  };
  box.append(
    step("Previous version of this message", "‹", fork.leaves[fork.version - 2]),
    Object.assign(document.createElement("span"), { textContent: `${fork.version}/${fork.versions}`, title: `Version ${fork.version} of ${fork.versions} of this message` }),
    step("Next version of this message", "›", fork.leaves[fork.version]),
  );
  return box;
}

// forks maps a user turn number (1-based) to the versions of that message, see the server's path_forks
export function applyForks(forks) {
  chat.querySelectorAll(".msg.user .user-tools").forEach((tools, i) => {
    tools.querySelector(".versions")?.remove();
    const fork = forks?.[i + 1];
    if (fork) tools.prepend(versionSwitch(fork));
  });
}

export async function loadBranches() {
  let rows = [];
  try { rows = await api("/branches"); } catch (e) {}
  const select = document.getElementById("branch-select");
  document.getElementById("branch-tree-btn").hidden = rows.length <= 1;
  if (rows.length <= 1) {
    select.hidden = true;
    select.replaceChildren();
    return;
  }
  select.replaceChildren();
  for (const row of rows) {
    const opt = document.createElement("option");
    opt.value = row.id;
    opt.textContent = row.preview || "(empty branch)";
    opt.selected = row.current;
    select.append(opt);
  }
  select.hidden = false;
}

export async function switchBranch(id) {
  if (busy) return;
  try {
    renderSession(await api(`/branches/${id}/switch`, { method: "POST" }));
  } catch (err) {
    alert(err.message);
  }
}

const branchDialog = document.getElementById("branch-dialog");
const branchBody = document.getElementById("branch-body");

// the user messages as a tree: a message with several versions was edited, and each version
// carries on from the same earlier reply. `nodes` come from GET /branches/tree
function renderBranchTree(nodes) {
  branchBody.replaceChildren();
  branchBody.append(Object.assign(document.createElement("p"), {
    className: "bt-hint",
    textContent: "Each line is one of your messages. Editing a message starts a new version of it, shown side by side. Click one to switch to that branch.",
  }));
  if (!nodes.length) {
    branchBody.append(Object.assign(document.createElement("div"), {
      className: "stats-empty",
      textContent: "No messages in this chat yet.",
    }));
    return;
  }
  const children = new Map();
  for (const node of nodes) {
    if (!children.has(node.parent)) children.set(node.parent, []);
    children.get(node.parent).push(node);
  }

  const build = (parent, depth) => {
    const list = document.createElement("ul");
    for (const node of children.get(parent) || []) {
      const item = document.createElement("li");
      if (node.version === 1 && node.versions > 1) {
        item.append(Object.assign(document.createElement("div"), {
          className: "bt-fork",
          textContent: `Message ${depth} was edited · ${node.versions} versions`,
        }));
      }
      const row = document.createElement("button");
      row.type = "button";
      row.className = "bt-row" + (node.on_path ? " on-path" : "");
      row.title = node.text.slice(0, 300);
      row.append(
        Object.assign(document.createElement("span"), { className: "bt-turn", textContent: `#${depth}` }),
        Object.assign(document.createElement("span"), { className: "bt-text", textContent: node.text.replace(/\s+/g, " ") }),
      );
      if (node.versions > 1) {
        row.append(Object.assign(document.createElement("span"), { className: "bt-tag", textContent: `v${node.version}/${node.versions}` }));
      }
      if (node.current) row.append(Object.assign(document.createElement("span"), { className: "bt-tag current", textContent: "current" }));
      row.addEventListener("click", () => {
        branchDialog.close();
        if (!node.on_path) switchBranch(node.leaf);
      });
      item.append(row);
      if (children.has(node.id)) item.append(build(node.id, depth + 1));
      list.append(item);
    }
    return list;
  };
  const tree = build(null, 1);
  tree.className = "bt";
  branchBody.append(tree);
}

export async function openBranchTree() {
  if (busy) return;
  try {
    renderBranchTree(await api("/branches/tree"));
  } catch (err) {
    alert(err.message);
    return;
  }
  branchDialog.showModal();
}

export function initBranches() {
  document.getElementById("branch-tree-btn").addEventListener("click", openBranchTree);
  document.getElementById("branch-close").addEventListener("click", () => branchDialog.close());
  document.getElementById("branch-select").addEventListener("change", (e) => switchBranch(e.target.value));
}
