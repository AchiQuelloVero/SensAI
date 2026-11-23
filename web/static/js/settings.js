import { showContext } from "./context.js";
import { api } from "./api.js";
import { loadSessions } from "./sessions.js";

const settingsBtn = document.getElementById("settings-btn");
const settingsPanel = document.getElementById("settings-panel");
const modelList = document.getElementById("model-list");
let modelNames = [];
let selectedModel = "";

const REMOTE_PREFIX = "openrouter/";

function modelInfo(name) {
  if (!name.startsWith(REMOTE_PREFIX)) return { title: name, detail: "Runs locally with Ollama", group: "Local" };
  const id = name.slice(REMOTE_PREFIX.length);
  const free = id.endsWith(":free");
  const [maker, ...rest] = id.replace(/:free$/, "").split("/");
  return { title: rest.join("/") || maker, detail: `${maker} · OpenRouter`, group: "OpenRouter", free };
}

function renderModels() {
  const rows = [];
  for (const group of ["Local", "OpenRouter"]) {
    const names = modelNames.filter((name) => modelInfo(name).group === group);
    if (!names.length) continue;
    rows.push(Object.assign(document.createElement("div"), { className: "model-group", textContent: group }));
    for (const name of names) {
      const info = modelInfo(name);
      const option = document.createElement("button");
      option.type = "button";
      option.className = "persona-option" + (name === selectedModel ? " current" : "");
      option.title = name;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", name === selectedModel);
      const top = Object.assign(document.createElement("span"), { className: "top" });
      top.append(Object.assign(document.createElement("span"), { className: "model-name", textContent: info.title }));
      if (info.free) top.append(Object.assign(document.createElement("span"), { className: "model-tag", textContent: "free" }));
      option.append(top, Object.assign(document.createElement("span"), { className: "role", textContent: info.detail }));
      option.addEventListener("click", () => {
        selectedModel = name;
        renderModels();
      });
      rows.push(option);
    }
  }
  if (!rows.length) rows.push(Object.assign(document.createElement("div"), { className: "model-empty", textContent: "No models found." }));
  modelList.replaceChildren(...rows);
}

const setAutoApprove = document.getElementById("set-auto-approve");
const setGuardrails = document.getElementById("set-guardrails");
const setOutputCheck = document.getElementById("set-output-check");
const setBlockedMessage = document.getElementById("set-blocked-message");
const piiGrid = document.getElementById("pii-grid");
const categoryBoxes = [...document.querySelectorAll("#guard-settings [data-category]")];
const PII_NAMES = { EMAIL: "Email", PHONE: "Phone", IBAN: "IBAN", CARD: "Card" };

function renderPii(pii) {
  piiGrid.replaceChildren();
  for (const [type, action] of Object.entries(pii)) {
    const select = document.createElement("select");
    select.dataset.pii = type;
    for (const value of ["allow", "mask", "block"]) select.append(new Option(value, value, false, value === action));
    const label = document.createElement("label");
    label.append(Object.assign(document.createElement("span"), { textContent: PII_NAMES[type] || type }), select);
    piiGrid.append(label);
  }
}

function syncGuardControls() {
  const off = !setGuardrails.checked;
  for (const el of [setOutputCheck, setBlockedMessage, ...categoryBoxes, ...piiGrid.querySelectorAll("select")]) el.disabled = off;
  document.getElementById("guard-settings").classList.toggle("off", off);
}
const setCacheEnabled = document.getElementById("set-cache-enabled");
const setCacheThreshold = document.getElementById("set-cache-threshold");
const cacheThresholdValue = document.getElementById("cache-threshold-value");
const setCacheVerify = document.getElementById("set-cache-verify");
const setCacheMidChat = document.getElementById("set-cache-mid-chat");
const cacheRagHint = document.getElementById("cache-rag-hint");

function syncCacheControls() {
  cacheThresholdValue.textContent = Number(setCacheThreshold.value).toFixed(2);
  for (const el of [setCacheThreshold, setCacheVerify, setCacheMidChat]) el.disabled = !setCacheEnabled.checked;
}

const setFactEnabled = document.getElementById("set-fact-enabled");
const setFactThreshold = document.getElementById("set-fact-threshold");
const factThresholdValue = document.getElementById("fact-threshold-value");
const setFactAction = document.getElementById("set-fact-action");

function syncFactControls() {
  factThresholdValue.textContent = Number(setFactThreshold.value).toFixed(2);
  for (const el of [setFactThreshold, setFactAction]) el.disabled = !setFactEnabled.checked;
}

async function openSettings() {
  try {
    const [cfg, models, guard, fact] = await Promise.all([
      api("/config"), api("/models"), api("/guardrails"), api("/hallucination"),
    ]);
    if (!models.includes(cfg.model)) models.unshift(cfg.model);
    modelNames = models;
    selectedModel = cfg.model;
    renderModels();
    setAutoApprove.checked = cfg.auto_approve;
    setGuardrails.checked = !cfg.no_guardrails;
    setOutputCheck.checked = guard.output_check;
    setBlockedMessage.value = guard.blocked_message;
    for (const box of categoryBoxes) box.checked = guard.blocked_categories.includes(box.dataset.category);
    renderPii(guard.pii);
    syncGuardControls();
    setCacheEnabled.checked = cfg.cache_enabled;
    setCacheThreshold.value = cfg.cache_threshold;
    setCacheVerify.checked = cfg.cache_verify;
    setCacheMidChat.checked = cfg.cache_mid_chat;
    cacheRagHint.hidden = !cfg.rag_enabled;
    syncCacheControls();
    setFactEnabled.checked = fact.enabled;
    setFactThreshold.value = fact.confidence_threshold;
    setFactAction.value = fact.action;
    syncFactControls();
  } catch (err) {
    alert(err.message);
    return;
  }
  settingsPanel.hidden = false;
  modelList.querySelector(".current")?.scrollIntoView({ block: "nearest" });
}

export function initSettings() {
  setCacheEnabled.addEventListener("change", syncCacheControls);
  setCacheThreshold.addEventListener("input", syncCacheControls);
  setGuardrails.addEventListener("change", syncGuardControls);
  setFactEnabled.addEventListener("change", syncFactControls);
  setFactThreshold.addEventListener("input", syncFactControls);

  settingsBtn.addEventListener("click", () => {
    if (settingsPanel.hidden) openSettings();
    else settingsPanel.hidden = true;
  });

  document.getElementById("settings-cancel").addEventListener("click", () => {
    settingsPanel.hidden = true;
  });

  document.getElementById("settings-save").addEventListener("click", async () => {
    try {
      await api("/guardrails", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          output_check: setOutputCheck.checked,
          blocked_message: setBlockedMessage.value.trim() || undefined,
          blocked_categories: categoryBoxes.filter((box) => box.checked).map((box) => box.dataset.category),
          pii: Object.fromEntries([...piiGrid.querySelectorAll("select")].map((s) => [s.dataset.pii, s.value])),
        }),
      });
      await api("/hallucination", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: setFactEnabled.checked,
          confidence_threshold: Number(setFactThreshold.value),
          action: setFactAction.value,
        }),
      });
      const cfg = await api("/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: selectedModel,
          auto_approve: setAutoApprove.checked,
          no_guardrails: !setGuardrails.checked,
          cache_enabled: setCacheEnabled.checked,
          cache_threshold: Number(setCacheThreshold.value),
          cache_verify: setCacheVerify.checked,
          cache_mid_chat: setCacheMidChat.checked,
        }),
      });
      showContext(cfg.context);
      loadSessions(); // the open chat's row shows its new model
      settingsPanel.hidden = true;
    } catch (err) {
      alert(err.message);
    }
  });

  document.addEventListener("click", (e) => {
    if (!settingsPanel.hidden && !e.composedPath().includes(document.getElementById("settings-btn").parentElement)) {
      settingsPanel.hidden = true;
    }
  });
}
