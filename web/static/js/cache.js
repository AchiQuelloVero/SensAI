import { api } from "./api.js";
import { closeSidebar, formatWhen } from "./dom.js";
import { formatSeconds } from "./messages.js";
import { renderMarkdown } from "./markdown.js";
import { statTile } from "./stats.js";

const cacheDialog = document.getElementById("cache-dialog");
const cacheBody = document.getElementById("cache-body");

function el(tag, className, textContent = "") {
  return Object.assign(document.createElement(tag), { className, textContent });
}

function formatDate(seconds) {
  return formatWhen(new Date(seconds * 1000).toISOString());
}

async function change(path, method = "DELETE") {
  try {
    render(await api(path, { method }));
  } catch (err) {
    alert(err.message);
  }
}

export function cacheEntryMeta(entry) {
  const hits = `${entry.hits} hit${entry.hits === 1 ? "" : "s"}`;
  return [entry.model, formatDate(entry.created), hits, `saved ${formatSeconds(entry.saved_s)}`].filter(Boolean).join(" · ");
}

function entryRow(entry) {
  const row = el("details", "cache-entry");
  const summary = el("summary", "cache-summary");
  const text = el("div", "cache-text");
  text.append(el("div", "cache-question", entry.question));
  text.append(el("div", "cache-meta", cacheEntryMeta(entry)));
  const remove = el("button", "icon-btn cache-delete");
  remove.type = "button";
  remove.title = "Delete this saved answer";
  remove.setAttribute("aria-label", "Delete this saved answer");
  remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  remove.addEventListener("click", (e) => {
    e.preventDefault();
    change(`/cache/entries/${entry.id}`);
  });
  summary.append(text, remove);
  const answer = el("div", "cache-answer md");
  answer.append(renderMarkdown(entry.answer));
  row.append(summary, answer);
  return row;
}

function render(data) {
  const box = document.createElement("div");
  const grid = el("div", "stat-grid three");
  grid.append(
    statTile(data.entries.length.toLocaleString(), "Saved answers"),
    statTile(data.hits.toLocaleString(), "Reused"),
    statTile(formatSeconds(data.saved_s), "Time saved"),
  );
  box.append(grid);

  if (!data.entries.length) {
    box.append(el("div", "stats-empty", "No saved answers yet. Turn the cache on in Settings and ask a question."));
  } else {
    const list = el("div", "cache-list");
    list.append(...data.entries.map(entryRow));
    const clear = el("button", "settings-btn-secondary cache-clear", "Clear all");
    clear.type = "button";
    clear.addEventListener("click", () => {
      if (confirm(`Delete all ${data.entries.length} saved answers?`)) change("/cache/entries");
    });
    box.append(list, clear);
  }
  cacheBody.replaceChildren(box);
}

async function openCache() {
  closeSidebar();
  try {
    render(await api("/cache"));
  } catch (err) {
    alert(err.message);
    return;
  }
  cacheDialog.showModal();
}

export function initCache() {
  document.getElementById("open-cache").addEventListener("click", openCache);
  document.getElementById("cache-close").addEventListener("click", () => cacheDialog.close());
}
