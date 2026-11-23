import { api } from "./api.js";
import { closeSidebar } from "./dom.js";
import { jsonMode, loadSchemaOptions } from "./json-mode.js";

const dialog = document.getElementById("schema-dialog");
const list = document.getElementById("schema-list");
const form = document.getElementById("schema-form");
const nameInput = document.getElementById("schema-name");
const textInput = document.getElementById("schema-text");
const errorBox = document.getElementById("schema-error");

const EXAMPLE = {
  type: "object",
  properties: {
    title: { type: "string" },
    decisions: { type: "array", items: { type: "string" } },
    next_meeting: { type: ["string", "null"], description: "YYYY-MM-DD, or null" },
  },
  required: ["title", "decisions", "next_meeting"],
  additionalProperties: false,
};

function el(tag, className, textContent = "") {
  return Object.assign(document.createElement(tag), { className, textContent });
}

function fields(schema) {
  const required = new Set(schema.required || []);
  return Object.keys(schema.properties || {}).map((key) => (required.has(key) ? key : `${key}?`)).join(", ");
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

async function remove(name) {
  if (!confirm(`Delete the schema "${name}"?`)) return;
  try {
    await api(`/schemas/${encodeURIComponent(name)}`, { method: "DELETE" });
  } catch (err) {
    alert(err.message);
  }
  await load();
}

function schemaRow(name, schema) {
  const row = el("details", "cache-entry");
  const summary = el("summary", "cache-summary");
  const text = el("div", "cache-text");
  text.append(el("div", "cache-question", name), el("div", "cache-meta", fields(schema)));
  const del = el("button", "icon-btn cache-delete");
  del.type = "button";
  del.title = `Delete ${name}`;
  del.setAttribute("aria-label", `Delete ${name}`);
  del.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  del.addEventListener("click", (e) => {
    e.preventDefault();
    remove(name);
  });
  summary.append(text, del);
  row.append(summary, el("pre", "schema-json", JSON.stringify(schema, null, 2)));
  return row;
}

async function load() {
  const names = await api("/schemas");
  const schemas = await Promise.all(names.map((name) => api(`/schemas/${encodeURIComponent(name)}`).catch(() => ({}))));
  list.replaceChildren(...names.map((name, i) => schemaRow(name, schemas[i])));
  if (jsonMode()) loadSchemaOptions().catch(() => {});
  if (!names.length) list.append(el("div", "stats-empty", "No schemas yet: add one below."));
}

async function openSchemas() {
  closeSidebar();
  showError("");
  try {
    await load();
  } catch (err) {
    alert(err.message);
    return;
  }
  dialog.showModal();
}

async function addSchema(e) {
  e.preventDefault();
  let schema;
  try {
    schema = JSON.parse(textInput.value);
  } catch (err) {
    showError(`The schema is not valid JSON: ${err.message}`);
    return;
  }
  try {
    await api("/schemas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: nameInput.value.trim(), schema }),
    });
  } catch (err) {
    showError(err.message);
    return;
  }
  showError("");
  nameInput.value = "";
  textInput.value = "";
  await load();
}

export function initSchemas() {
  document.getElementById("open-schemas").addEventListener("click", openSchemas);
  document.getElementById("schema-close").addEventListener("click", () => dialog.close());
  document.getElementById("schema-example").addEventListener("click", () => {
    textInput.value = JSON.stringify(EXAMPLE, null, 2);
    if (!nameInput.value) nameInput.value = "meeting_notes";
    showError("");
  });
  form.addEventListener("submit", addSchema);
}
