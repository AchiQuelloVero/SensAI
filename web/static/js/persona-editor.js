import { api } from "./api.js";
import { closeSidebar } from "./dom.js";
import { busy } from "./state.js";
import { showPersona } from "./persona.js";

const dialog = document.getElementById("pe-dialog");
const listView = document.getElementById("pe-list-view");
const list = document.getElementById("pe-list");
const form = document.getElementById("pe-form");
const errorBox = document.getElementById("pe-error");
const keyInput = document.getElementById("pe-key");
const deleteBtn = document.getElementById("pe-delete");
const note = document.getElementById("pe-note");
const FIELDS = ["name", "role", "tone", "scope", "instructions"];
const input = (field) => document.getElementById(`pe-${field}`);
let editing = null; // the persona row being edited, or null for a new one

function el(tag, className, textContent = "") {
  return Object.assign(document.createElement(tag), { className, textContent });
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function personaRow(p) {
  const row = el("button", "pe-row" + (p.current ? " current" : ""));
  row.type = "button";
  const text = el("span", "cache-text");
  const tags = [p.current && "in this chat", p.default && "default"].filter(Boolean);
  text.append(el("span", "cache-question", p.problem ? p.key : `${p.name}${tags.length ? `  ·  ${tags.join(", ")}` : ""}`));
  text.append(el("span", "cache-meta" + (p.problem ? " pe-broken" : ""), p.problem ? `broken: ${p.problem}` : `${p.key} · ${p.role}`));
  row.append(text);
  row.addEventListener("click", () => openForm(p));
  return row;
}

async function loadList(rows) {
  rows ??= await api("/personas");
  list.replaceChildren(...rows.map(personaRow));
}

function openForm(p = null) {
  editing = p;
  showError("");
  document.getElementById("pe-form-title").textContent = p ? `Edit ${p.problem ? p.key : p.name}` : "New persona";
  keyInput.value = p ? p.key : "";
  keyInput.disabled = !!p;
  for (const field of FIELDS) input(field).value = (p && p[field]) || "";
  deleteBtn.hidden = !p;
  const locked = p && (p.current || p.default);
  deleteBtn.disabled = !!locked;
  note.textContent = !p ? "" : p.current ? "Saving applies it to this chat right away." : p.default ? "New chats start with this one." : "";
  deleteBtn.title = !locked ? "" : p.current ? "It is answering in this chat: switch to another one first" : "New chats start with it: it can't be deleted";
  listView.hidden = true;
  form.hidden = false;
  (p ? input("name") : keyInput).focus();
}

function closeForm() {
  form.hidden = true;
  listView.hidden = false;
}

async function save(e) {
  e.preventDefault();
  if (busy) return;
  const persona = Object.fromEntries(FIELDS.map((field) => [field, input(field).value.trim()]));
  try {
    if (editing) {
      const result = await api(`/personas/${encodeURIComponent(editing.key)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(persona),
      });
      if (result.current) showPersona(result.current);
      await loadList(result.personas);
    } else {
      await loadList(await api("/personas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: keyInput.value.trim(), persona }),
      }));
    }
  } catch (err) {
    showError(err.message);
    return;
  }
  closeForm();
}

async function remove() {
  if (!editing || !confirm(`Delete the persona "${editing.name || editing.key}"?`)) return;
  try {
    await loadList(await api(`/personas/${encodeURIComponent(editing.key)}`, { method: "DELETE" }));
  } catch (err) {
    showError(err.message);
    return;
  }
  closeForm();
}

export async function openPersonaEditor() {
  closeSidebar();
  closeForm();
  try {
    await loadList();
  } catch (err) {
    alert(err.message);
    return;
  }
  dialog.showModal();
}

export function initPersonaEditor() {
  document.getElementById("open-personas").addEventListener("click", openPersonaEditor);
  document.getElementById("pe-close").addEventListener("click", () => dialog.close());
  document.getElementById("pe-new").addEventListener("click", () => openForm());
  document.getElementById("pe-cancel").addEventListener("click", closeForm);
  deleteBtn.addEventListener("click", remove);
  form.addEventListener("submit", save);
}
