import { api } from "./api.js";
import { closeSidebar } from "./dom.js";

const toolsDialog = document.getElementById("tools-dialog");
const toolsBody = document.getElementById("tools-body");

function toolEntry(tool) {
  const block = document.createElement("details");
  block.className = "tool";
  const summary = document.createElement("summary");
  summary.append(Object.assign(document.createElement("span"), { className: "tool-name", textContent: tool.original_name || tool.name }));
  if (tool.requires_confirmation) {
    summary.append(Object.assign(document.createElement("span"), {
      className: "tool-badge edited",
      textContent: "asks first",
      title: "You are asked to approve each call",
    }));
  }
  const body = document.createElement("div");
  body.className = "tool-body";
  if (tool.description) body.append(Object.assign(document.createElement("p"), { className: "tool-desc", textContent: tool.description }));
  const props = tool.parameters?.properties || {};
  const required = tool.parameters?.required || [];
  const names = Object.keys(props);
  body.append(Object.assign(document.createElement("div"), { className: "tool-label", textContent: names.length ? "Parameters" : "No parameters" }));
  for (const name of names) {
    const line = document.createElement("div");
    line.className = "tool-param";
    const label = document.createElement("code");
    label.textContent = `${name}${required.includes(name) ? "" : "?"}: ${props[name].type || "any"}`;
    line.append(label);
    if (props[name].description) line.append(` ${props[name].description}`);
    body.append(line);
  }
  if (tool.original_name && tool.original_name !== tool.name) {
    body.append(Object.assign(document.createElement("div"), { className: "tool-label", textContent: `Called by the model as ${tool.name}` }));
  }
  block.append(summary, body);
  return block;
}

function pretty(value) {
  if (typeof value === "string") {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  return JSON.stringify(value, null, 2);
}

function loggedCall(call) {
  const block = document.createElement("details");
  block.className = "tool";
  const summary = document.createElement("summary");
  summary.append(
    Object.assign(document.createElement("span"), { className: "tool-when", textContent: call.time?.replace("T", " ") || "" }),
    Object.assign(document.createElement("span"), { className: "tool-name", textContent: call.tool_call }),
  );
  if (call.server) summary.append(Object.assign(document.createElement("span"), { className: "tool-server", textContent: call.server }));
  if (call.duration_s != null) summary.append(Object.assign(document.createElement("span"), { className: "tool-time", textContent: `${call.duration_s}s` }));
  const status = ["refused", "denied", "skipped"].includes(call.decision) ? call.decision : call.ok === false ? "failed" : call.decision;
  if (status) summary.append(Object.assign(document.createElement("span"), { className: `tool-badge ${status}`, textContent: status }));
  const body = document.createElement("div");
  body.className = "tool-body";
  const cut = call.result_chars > call.result.length ? ` (first ${call.result.length.toLocaleString()} of ${call.result_chars.toLocaleString()} chars)` : "";
  for (const [label, value] of [["Arguments", call.arguments], [`Result${cut}`, call.result]]) {
    body.append(
      Object.assign(document.createElement("div"), { className: "tool-label", textContent: label }),
      Object.assign(document.createElement("pre"), { textContent: pretty(value) }),
    );
  }
  if (call.session) body.append(Object.assign(document.createElement("div"), { className: "tool-label", textContent: `Session ${call.session} · step ${call.round ?? "?"}` }));
  block.append(summary, body);
  return block;
}

function toolLog() {
  const section = document.createElement("div");
  section.append(Object.assign(document.createElement("div"), {
    className: "tools-heading",
    textContent: "Recent tool calls · from the call log",
  }));
  const list = document.createElement("div");
  list.className = "tools-list tool-log";
  section.append(list);
  api("/tools/calls?limit=30").then(({ calls }) => {
    if (calls.length) list.append(...calls.map(loggedCall));
    else list.append(Object.assign(document.createElement("div"), { className: "tool-log-empty", textContent: "No tool calls logged yet." }));
  }).catch((err) => {
    list.append(Object.assign(document.createElement("div"), { className: "tool-log-empty", textContent: err.message }));
  });
  return section;
}

function serverButton(label, path, title, method = "POST", ask) {
  const button = Object.assign(document.createElement("button"), { type: "button", textContent: label });
  if (title) button.title = title;
  button.addEventListener("click", async () => {
    if (ask && !confirm(ask)) return;
    for (const other of toolsBody.querySelectorAll(".srv-actions button")) other.disabled = true;
    button.textContent = "…";
    try {
      renderTools(await api(path, { method }));
    } catch (err) {
      alert(err.message);
      renderTools(await api("/tools"));
    }
  });
  return button;
}

function addServerForm() {
  const block = document.createElement("details");
  block.className = "srv-add";
  block.append(Object.assign(document.createElement("summary"), { textContent: "Add server" }));
  const form = document.createElement("div");
  form.className = "pf-form";
  const hint = Object.assign(document.createElement("p"), {
    className: "srv-add-hint",
    textContent: "Paste the server's config, as given in its README. A single server's settings also need a name.",
  });
  const name = Object.assign(document.createElement("input"), { type: "text", placeholder: "Name (only for a single server's settings)", spellcheck: false });
  const text = Object.assign(document.createElement("textarea"), {
    spellcheck: false,
    placeholder: '{\n  "mcpServers": {\n    "blender": { "command": "uvx", "args": ["blender-mcp"] }\n  }\n}',
  });
  const add = Object.assign(document.createElement("button"), { type: "button", className: "settings-btn-primary", textContent: "Add" });
  add.addEventListener("click", async () => {
    let config;
    try {
      config = JSON.parse(text.value);
    } catch {
      alert("This is not valid JSON.");
      return;
    }
    add.disabled = true;
    add.textContent = "Connecting…";
    try {
      renderTools(await api("/mcp/servers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config, id: name.value.trim() || null }),
      }));
    } catch (err) {
      alert(err.message);
      add.disabled = false;
      add.textContent = "Add";
    }
  });
  const actions = document.createElement("div");
  actions.className = "settings-actions";
  actions.append(add);
  form.append(hint, name, text, actions);
  block.append(form);
  return block;
}

function renderTools(data) {
  toolsBody.replaceChildren();

  const connected = data.servers.filter((srv) => srv.connected).length;
  const top = document.createElement("div");
  top.className = "tools-top";
  const reload = document.createElement("div");
  reload.className = "srv-actions";
  reload.append(serverButton("Reload config", "/mcp/reload", "Re-read mcp_servers.json: start new servers, stop removed ones, restart changed ones"));
  top.append(Object.assign(document.createElement("div"), {
    className: "tools-heading",
    textContent: data.servers.length ? `MCP servers · ${connected} of ${data.servers.length} connected` : "MCP servers",
  }), reload);
  toolsBody.append(top);
  if (!data.servers.length) toolsBody.append(Object.assign(document.createElement("div"), { className: "side-empty", textContent: "No MCP servers configured." }));
  for (const srv of data.servers) {
    const row = document.createElement("div");
    row.className = "srv-row";
    const main = document.createElement("div");
    main.className = "srv-main";
    main.append(
      Object.assign(document.createElement("span"), { className: "srv-dot " + (srv.connected ? "ok" : srv.disabled ? "" : "fail") }),
      srv.id,
      Object.assign(document.createElement("span"), {
        className: "srv-state",
        textContent: srv.connected ? `${srv.tools} ${srv.tools === 1 ? "tool" : "tools"}` : srv.disabled ? "disabled" : "not connected",
      }),
    );
    const actions = document.createElement("div");
    actions.className = "srv-actions";
    const base = `/mcp/${encodeURIComponent(srv.id)}`;
    if (!srv.disabled) actions.append(serverButton("Restart", `${base}/restart`));
    actions.append(srv.disabled ? serverButton("Enable", `${base}/enable`) : serverButton("Disable", `${base}/disable`));
    actions.append(serverButton("Remove", `/mcp/servers/${encodeURIComponent(srv.id)}`, "Delete it from mcp_servers.json", "DELETE", `Remove the ${srv.id} MCP server from the config?`));
    main.append(actions);
    row.append(main);
    if (srv.target) row.append(Object.assign(document.createElement("div"), { className: "srv-target", textContent: `${srv.transport} · ${srv.target}` }));
    if (srv.error) row.append(Object.assign(document.createElement("div"), { className: "srv-error", textContent: srv.error }));
    toolsBody.append(row);
  }
  toolsBody.append(addServerForm(), toolLog());

  const groups = [["Built-in tools", data.tools.filter((t) => !t.server)]];
  for (const srv of data.servers) groups.push([`${srv.id} tools`, data.tools.filter((t) => t.server === srv.id)]);
  for (const [title, tools] of groups) {
    if (!tools.length) continue;
    toolsBody.append(Object.assign(document.createElement("div"), { className: "tools-heading", textContent: title }));
    const list = document.createElement("div");
    list.className = "tools-list";
    list.append(...tools.map(toolEntry));
    toolsBody.append(list);
  }
}

async function openTools() {
  closeSidebar();
  try {
    renderTools(await api("/tools"));
  } catch (err) {
    alert(err.message);
    return;
  }
  toolsDialog.showModal();
}

export function initTools() {
  document.getElementById("open-tools").addEventListener("click", openTools);
  document.getElementById("tools-close").addEventListener("click", () => toolsDialog.close());
}
