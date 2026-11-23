import { api } from "./api.js";
import { closeSidebar } from "./dom.js";

const PERM_LEVELS = [["none", "None", "No access"], ["read", "Read", "Read only"], ["write", "Write", "Read and write"]];

function levelPicker(current, onPick) {
  const group = document.createElement("div");
  group.className = "perm-levels";
  group.setAttribute("role", "radiogroup");
  for (const [level, label, title] of PERM_LEVELS) {
    const btn = Object.assign(document.createElement("button"), { type: "button", textContent: label, title });
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", String(level === current));
    if (level === current) btn.className = `on ${level}`;
    btn.addEventListener("click", () => onPick(level, group));
    group.append(btn);
  }
  return group;
}

function markLevel(group, level) {
  group.querySelectorAll("button").forEach((btn, i) => {
    const on = PERM_LEVELS[i][0] === level;
    btn.className = on ? `on ${level}` : "";
    btn.setAttribute("aria-checked", String(on));
  });
}

// editable view of the permissions; every change is saved right away and redraws the view
export function permissionsEditor(data) {
  const el = document.createElement("div");
  el.className = "perm";
  const error = Object.assign(document.createElement("div"), { className: "perm-error" });

  async function change(body) {
    error.textContent = "";
    try {
      const fresh = await api("/permissions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      el.replaceWith(permissionsEditor(fresh));
    } catch (err) {
      error.textContent = err.message;
    }
  }

  const intro = document.createElement("p");
  intro.className = "perm-intro";
  intro.append("What the assistant may do with files in ", Object.assign(document.createElement("code"), { textContent: `${data.workspace}/` }), ". The most specific path wins.");

  const rows = document.createElement("div");
  rows.className = "perm-rows";
  const defaultRow = document.createElement("div");
  defaultRow.className = "perm-row";
  defaultRow.append(
    Object.assign(document.createElement("span"), { className: "perm-path default", textContent: "Everything else" }),
    levelPicker(data.default, (level) => change({ action: "default", access: level })),
    document.createElement("span"),
  );
  rows.append(defaultRow);

  for (const rule of data.rules) {
    const row = document.createElement("div");
    row.className = "perm-row";
    const remove = Object.assign(document.createElement("button"), { type: "button", className: "icon-btn", title: "Remove rule" });
    remove.setAttribute("aria-label", `Remove rule for ${rule.path}`);
    remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    remove.addEventListener("click", () => change({ action: "remove", path: rule.path }));
    row.append(
      Object.assign(document.createElement("span"), { className: "perm-path", textContent: rule.path === "." ? ". (root)" : rule.path }),
      levelPicker(rule.access, (level) => change({ action: "set", path: rule.path, access: level })),
      remove,
    );
    rows.append(row);
  }

  const add = document.createElement("form");
  add.className = "perm-add";
  const path = Object.assign(document.createElement("input"), { type: "text", placeholder: "folder or file, e.g. notes" });
  path.setAttribute("aria-label", "Path to add a rule for");
  let level = "read";
  const picker = levelPicker(level, (picked, group) => { level = picked; markLevel(group, picked); });
  const submit = Object.assign(document.createElement("button"), { type: "submit", className: "settings-btn-primary", textContent: "Add" });
  add.append(path, picker, submit);
  add.addEventListener("submit", (e) => {
    e.preventDefault();
    if (path.value.trim()) change({ action: "set", path: path.value.trim(), access: level });
  });

  el.append(intro, rows, add, error);
  return el;
}

const permDialog = document.getElementById("perm-dialog");
const permBody = document.getElementById("perm-body");

async function openPermissions() {
  closeSidebar();
  try {
    permBody.replaceChildren(permissionsEditor(await api("/permissions")));
  } catch (err) {
    alert(err.message);
    return;
  }
  permDialog.showModal();
}

export function initPermissions() {
  document.getElementById("open-permissions").addEventListener("click", openPermissions);
  document.getElementById("perm-close").addEventListener("click", () => permDialog.close());
}
