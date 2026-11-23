import { api } from "./api.js";
import { closeSidebar, formatWhen } from "./dom.js";
import { busy } from "./state.js";

const promptDialog = document.getElementById("prompt-dialog");
const pvList = document.getElementById("pv-list");
const pvContent = document.getElementById("pv-content");
const pvRollback = document.getElementById("pv-rollback");
const pvStatus = document.getElementById("pv-status");
const pvA = document.getElementById("pv-a");
const pvB = document.getElementById("pv-b");
let pvSelected = null;

function pvSetStatus(text, isError = false) {
  pvStatus.textContent = text;
  pvStatus.style.color = isError ? "var(--error)" : "";
}

async function loadPromptVersions() {
  const { versions, chat: inChat } = await api("/prompt/versions");
  const latest = versions.length ? versions[versions.length - 1].version : null;
  pvList.replaceChildren();
  if (versions.length === 0) {
    const none = document.createElement("div");
    none.className = "side-empty";
    none.textContent = "No versions saved yet";
    pvList.append(none);
  }
  for (const v of [...versions].reverse()) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "pv-item" + (v.version === pvSelected ? " active" : "");
    item.dataset.version = v.version;
    item.textContent = `v${v.version}`;
    if (v.version === latest) item.append(Object.assign(document.createElement("span"), { className: "pv-tag", textContent: "latest" }));
    if (v.version === inChat) item.append(Object.assign(document.createElement("span"), { className: "pv-tag", textContent: "this chat" }));
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = formatWhen(v.time);
    item.append(sub);
    item.addEventListener("click", () => showPromptVersion(v.version));
    pvList.append(item);
  }
  const options = versions.map((v) => `v${v.version}`);
  pvA.replaceChildren(...versions.map((v, i) => new Option(options[i], v.version)));
  pvB.replaceChildren(...versions.map((v, i) => new Option(options[i], v.version)));
  if (versions.length > 1) {
    pvA.value = versions[versions.length - 2].version;
    pvB.value = latest;
  }
  return latest;
}

async function showPromptVersion(version) {
  pvSelected = version;
  pvList.querySelectorAll(".pv-item").forEach((el) => el.classList.toggle("active", Number(el.dataset.version) === version));
  pvSetStatus("");
  try {
    const data = await api(`/prompt/versions/${version}`);
    document.getElementById("pv-view-title").textContent = `Version ${version}`;
    pvContent.textContent = data.content;
    pvContent.hidden = false;
    pvRollback.hidden = false;
    pvRollback.disabled = busy;
  } catch (err) {
    pvSetStatus(err.message, true);
  }
}

export async function openPromptPanel(version = null) {
  closeSidebar();
  pvSelected = null;
  pvContent.hidden = true;
  pvRollback.hidden = true;
  document.getElementById("pv-view-title").textContent = "Select a version";
  document.getElementById("pv-results").replaceChildren();
  pvSetStatus("");
  try {
    const latest = await loadPromptVersions();
    promptDialog.showModal();
    if (version !== null || latest !== null) showPromptVersion(version ?? latest);
  } catch (err) {
    alert(err.message);
  }
}

export function buildComparison(comparisons) {
  return comparisons.map((c) => {
    const col = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = c.error ? `v${c.version}` : `v${c.version} · ${c.duration_s.toFixed(1)}s`;
    const reply = document.createElement("pre");
    reply.className = "pv-reply" + (c.error ? " error" : "");
    reply.textContent = c.error || c.reply;
    col.append(title, reply);
    return col;
  });
}

export function initPrompts() {
  pvRollback.addEventListener("click", async () => {
    if (pvSelected === null || busy) return;
    if (!confirm(`Roll back to v${pvSelected}? This rewrites system_prompt.txt and applies it to the current chat.`)) return;
    try {
      const { version } = await api("/prompt/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: pvSelected }),
      });
      await loadPromptVersions();
      await showPromptVersion(version);
      pvSetStatus(`Rolled back, the prompt is now v${version} and applies to this chat.`);
    } catch (err) {
      pvSetStatus(err.message, true);
    }
  });

  document.getElementById("pv-compare-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (busy) return;
    const btn = document.getElementById("pv-compare-btn");
    const results = document.getElementById("pv-results");
    btn.disabled = true;
    btn.textContent = "Comparing…";
    results.replaceChildren();
    pvSetStatus("");
    try {
      const { comparisons } = await api("/prompt/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          version_a: Number(pvA.value),
          version_b: Number(pvB.value),
          question: document.getElementById("pv-question").value,
        }),
      });
      results.append(...buildComparison(comparisons));
    } catch (err) {
      pvSetStatus(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Compare";
    }
  });

  document.getElementById("open-prompts").addEventListener("click", () => openPromptPanel());
  document.getElementById("prompt-close").addEventListener("click", () => promptDialog.close());
}
