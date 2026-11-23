import { api } from "./api.js";
import { input, sessionsEl, closeSidebar, formatWhen } from "./dom.js";
import { busy } from "./state.js";
import { currentId, renderSession } from "./chat.js";
import { personaNames } from "./persona.js";

export function highlightActive() {
  sessionsEl.querySelectorAll(".session").forEach((b) => b.classList.toggle("active", b.dataset.id === currentId));
}

function sessionAction(label, iconPath, onClick, danger = false) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "session-action" + (danger ? " danger" : "");
  btn.title = label;
  btn.setAttribute("aria-label", label);
  btn.disabled = busy;
  btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${iconPath}</svg>`;
  btn.addEventListener("click", onClick);
  return btn;
}

// swap the chat's button for a text field; Enter saves, Escape cancels, an empty title restores the default
function startRename(row, button, item) {
  if (busy) return;
  const field = document.createElement("input");
  field.className = "session-rename";
  field.maxLength = 80;
  field.value = item.title || "";
  field.setAttribute("aria-label", "Chat title");
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (save && field.value.trim() !== (item.title || "")) {
      try {
        await api(`/sessions/${item.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: field.value }),
        });
      } catch (err) {
        alert(err.message);
      }
    }
    loadSessions();
  };
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") finish(false);
  });
  field.addEventListener("blur", () => finish(true));
  row.replaceChildren(field);
  field.focus();
  field.select();
}

async function deleteSession(item) {
  if (busy) return;
  if (!confirm(`Delete "${item.title || "(empty chat)"}"? This cannot be undone.`)) return;
  try {
    const { session } = await api(`/sessions/${item.id}`, { method: "DELETE" });
    if (session) renderSession(session); // it was the open chat, the server moved to a new one
  } catch (err) {
    alert(err.message);
  }
  loadSessions();
}

const shareDialog = document.getElementById("share-dialog");
let shareId = null;

function openShare(item) {
  shareId = item.id;
  document.getElementById("share-title").textContent = item.title || "Share & export";
  document.getElementById("share-link").hidden = true;
  document.getElementById("share-error").textContent = "";
  document.getElementById("share-publish").disabled = false;
  for (const format of ["json", "markdown", "pdf"]) {
    document.getElementById(`export-${format}`).href = `/sessions/${item.id}/export?format=${format}`;
  }
  shareDialog.showModal();
}

export async function loadSessions() {
  let list = [];
  try { list = await api("/sessions"); } catch (e) {}
  sessionsEl.replaceChildren();
  if (list.length === 0) {
    const none = document.createElement("div");
    none.className = "side-empty";
    none.textContent = "No saved chats yet";
    sessionsEl.append(none);
    return;
  }
  for (const item of list) {
    const row = document.createElement("div");
    row.className = "session-row";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "session";
    b.dataset.id = item.id;
    b.disabled = busy;
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = item.title || "(empty chat)";
    const sub = document.createElement("span");
    sub.className = "sub";
    const details = [formatWhen(item.updated_at), `${item.turns} ${item.turns === 1 ? "message" : "messages"}`];
    // opening a chat switches to its model, so say which one before it happens
    if (item.model) details.push(item.model.split("/").at(-1));
    if (item.persona) details.push(personaNames[item.persona] || item.persona);
    if (item.prompt_version) details.push(`prompt v${item.prompt_version}`);
    sub.textContent = details.join(" · ");
    b.append(title, sub);
    b.addEventListener("click", () => openSession(item.id));

    const actions = document.createElement("span");
    actions.className = "session-actions";
    actions.append(
      sessionAction("Share & export", '<path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7M16 6l-4-4-4 4M12 2v13"/>', () => openShare(item)),
      sessionAction("Rename chat", '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>', () => startRename(row, b, item)),
      sessionAction("Delete chat", '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6"/>', () => deleteSession(item), true),
    );
    row.append(b, actions);
    sessionsEl.append(row);
  }
  highlightActive();
}

async function openSession(id) {
  if (busy || id === currentId) return closeSidebar();
  try {
    renderSession(await api(`/sessions/${id}/resume`, { method: "POST" }));
  } catch (err) {
    alert(err.message);
  }
  closeSidebar();
  input.focus();
}

async function newChat() {
  if (busy) return;
  try {
    renderSession(await api("/sessions/new", { method: "POST" }));
  } catch (err) {
    alert(err.message);
  }
  closeSidebar();
  input.focus();
}

export function initSessions() {
  document.getElementById("share-publish").addEventListener("click", async (e) => {
    e.target.disabled = true;
    document.getElementById("share-error").textContent = "";
    try {
      const { url } = await api(`/sessions/${shareId}/publish`, { method: "POST" });
      const full = new URL(url, location.origin).href;
      document.getElementById("share-url").value = full;
      document.getElementById("share-open").href = full;
      document.getElementById("share-link").hidden = false;
      e.target.textContent = "Create new link";
    } catch (err) {
      document.getElementById("share-error").textContent = err.message;
    }
    e.target.disabled = false;
  });
  document.getElementById("share-copy").addEventListener("click", async (e) => {
    const field = document.getElementById("share-url");
    try { await navigator.clipboard.writeText(field.value); } catch { field.select(); document.execCommand("copy"); }
    e.target.textContent = "Copied";
    setTimeout(() => (e.target.textContent = "Copy"), 1500);
  });
  document.getElementById("share-close").addEventListener("click", () => shareDialog.close());
  shareDialog.addEventListener("close", () => (document.getElementById("share-publish").textContent = "Create link"));

  document.getElementById("menu").addEventListener("click", () => {
    if (matchMedia("(max-width: 760px)").matches) {
      document.body.classList.add("sidebar-open");
      return;
    }
    const hidden = document.documentElement.dataset.sidebar !== "hidden";
    document.documentElement.dataset.sidebar = hidden ? "hidden" : "shown";
    try { localStorage.setItem("sidebar", hidden ? "hidden" : "shown"); } catch (e) {}
  });
  document.getElementById("backdrop").addEventListener("click", closeSidebar);
  document.getElementById("new-chat").addEventListener("click", newChat);
}
