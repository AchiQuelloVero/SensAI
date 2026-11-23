import { api } from "./api.js";
import { input, scrollDown } from "./dom.js";
import { makeCopyButton } from "./markdown.js";

export const jsonToggle = document.getElementById("json-toggle");
export const schemaSelect = document.getElementById("schema-select");
const DEFAULT_PLACEHOLDER = input.placeholder;

export function jsonMode() { return jsonToggle.getAttribute("aria-pressed") === "true"; }

export async function loadSchemaOptions() {
  const schemas = await api("/schemas");
  if (schemas.length === 0) throw new Error("No schemas yet: add one in JSON schemas, in the sidebar");
  const previous = schemaSelect.value;
  schemaSelect.replaceChildren(...schemas.map((name) => new Option(name, name)));
  if (schemas.includes(previous)) schemaSelect.value = previous;
}

async function setJsonMode(on) {
  if (on) {
    try {
      await loadSchemaOptions();
    } catch (err) {
      alert(err.message);
      return;
    }
  }
  jsonToggle.setAttribute("aria-pressed", on);
  jsonToggle.classList.toggle("on", on);
  schemaSelect.hidden = !on;
  input.placeholder = on ? "Describe what to turn into JSON…" : DEFAULT_PLACEHOLDER;
  input.focus();
}

// pretty-printed JSON as DOM nodes with keys and literals coloured
function renderJson(value) {
  const text = JSON.stringify(value, null, 2);
  const fragment = document.createDocumentFragment();
  const token = /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
  let last = 0, match;
  const span = (cls, str) => {
    const el = document.createElement("span");
    if (cls) el.className = cls;
    el.textContent = str;
    return el;
  };
  while ((match = token.exec(text))) {
    if (match.index > last) fragment.append(text.slice(last, match.index));
    if (match[1] && match[2]) fragment.append(span("j-key", match[1]), match[2]);
    else if (match[1]) fragment.append(match[1]);
    else fragment.append(span("j-lit", match[0]));
    last = token.lastIndex;
  }
  fragment.append(text.slice(last));
  return fragment;
}

function downloadButton(schema, data) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-btn";
  button.textContent = "Download";
  button.addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: `${schema}.json` });
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  return button;
}

function attemptsNote(cmd) {
  const note = document.createElement("div");
  note.className = "meta json-attempts" + (cmd.retries.length ? " retried" : "");
  const time = cmd.duration_s != null ? ` · ${cmd.duration_s.toFixed(1)} s` : "";
  const line = `valid JSON on attempt ${cmd.attempts} of ${cmd.max_attempts}${time}`;
  if (!cmd.retries.length) {
    note.textContent = line;
    return note;
  }
  const details = document.createElement("details");
  details.append(Object.assign(document.createElement("summary"), {
    textContent: `${line} · ${cmd.retries.length} retr${cmd.retries.length === 1 ? "y" : "ies"}`,
  }));
  const list = document.createElement("ul");
  for (const r of cmd.retries) {
    const item = document.createElement("li");
    item.textContent = `attempt ${r.attempt} failed: ${r.problems.join("; ")}`;
    list.append(item);
  }
  details.append(list);
  note.append(details);
  return note;
}

export function addJsonBlock(wrap, cmd) {
  const block = document.createElement("div");
  block.className = "json-block";
  const head = document.createElement("div");
  head.className = "json-head";
  const label = document.createElement("span");
  label.textContent = `JSON · ${cmd.schema}`;
  const buttons = document.createElement("span");
  buttons.className = "json-buttons";
  buttons.append(makeCopyButton(() => JSON.stringify(cmd.data, null, 2)), downloadButton(cmd.schema, cmd.data));
  head.append(label, buttons);
  const pre = document.createElement("pre");
  pre.append(renderJson(cmd.data));
  block.append(head, pre);
  wrap.append(block);
  if (cmd.attempts) wrap.append(attemptsNote(cmd));
  scrollDown();
}

export function initJsonMode() {
  jsonToggle.addEventListener("click", () => setJsonMode(!jsonMode()));
}
