import { api } from "./api.js";
import { input, scrollDown, formatWhen } from "./dom.js";
import { renderSession } from "./chat.js";
import { addMessage } from "./messages.js";
import { forkAndSend, switchBranch, openBranchTree } from "./branches.js";
import { loadSessions } from "./sessions.js";
import { showPersona, choosePersona } from "./persona.js";
import { openPromptPanel, buildComparison } from "./prompts.js";
import { buildStats, statTile } from "./stats.js";
import { cacheEntryMeta } from "./cache.js";
import { formatSeconds } from "./messages.js";
import { permissionsEditor } from "./permissions.js";
import { addJsonBlock } from "./json-mode.js";
import { updateCmdMenu } from "./autocomplete.js";

export const COMMANDS = [
  { name: "/help", args: "", desc: "Show this list of commands" },
  { name: "/stats", args: "", desc: "Call statistics from the log" },
  { name: "/persona", args: "[name | show | add | edit | delete …]", desc: "Switch persona, or manage them" },
  { name: "/prompt", args: "[versions | show n | rollback n | compare n1 n2 question]", desc: "Prompt versions" },
  { name: "/json", args: "<schema> <prompt>", desc: "Reply as JSON matching a schema" },
  { name: "/schemas", args: "[show | add | delete …]", desc: "See, add or delete JSON schemas" },
  { name: "/branches", args: "[switch id]", desc: "List or switch conversation branches" },
  { name: "/edit", args: "<n> <new text>", desc: "Fork a past message and resend it" },
  { name: "/permissions", args: "[set path level | remove path | default level]", desc: "See or change what files the assistant may touch" },
  { name: "/cache", args: "[on | off | list | clear | …]", desc: "Reuse answers to questions already asked" },
  { name: "/guardrails", args: "[on | off | pii | refuse | …]", desc: "See or change the safety rules" },
];
export const SCHEMA_VERBS = [
  ["show", "The whole schema: show <name>", true],
  ["add", "Add one: add <name> <file.json> or add <name> {...}", true],
  ["delete", "Remove one: delete <name>", true],
];
export const GUARDRAIL_VERBS = [
  ["on", "Check messages and answers", false],
  ["off", "Turn every check off", false],
  ["pii", "Rule for a data type: pii <type> allow|mask|block", true],
  ["refuse", "Refuse a kind of message: refuse <category> on|off", true],
  ["output", "Withdraw unsafe answers: output on|off", true],
  ["message", "Reply to a refused message: message <text>", true],
];
export const GUARD_LABELS = {
  off_topic: "Off topic for the persona",
  harmful: "Harmful",
  asks_for_others_data: "Asking for someone else's data",
};
export const CACHE_VERBS = [
  ["on", "Turn the cache on", false],
  ["off", "Turn the cache off", false],
  ["list", "Show the saved answers", false],
  ["delete", "Forget one saved answer: delete <id>", true],
  ["clear", "Forget every saved answer", false],
  ["threshold", "How similar a question must be: threshold <0-1>", true],
  ["verify", "Model confirms it's the same question: verify on|off", true],
  ["mid-chat", "Also look up questions mid-chat: mid-chat on|off", true],
];
export const PROMPT_VERBS = [
  ["versions", "List saved versions", false],
  ["show", "Show one version", true],
  ["rollback", "Restore a version", true],
  ["compare", "Ask two versions the same question", true],
];

export const PERMISSION_VERBS = [
  ["set", "Give a path none, read or write access", true],
  ["remove", "Delete the rule for a path", true],
  ["default", "Access for paths with no rule", true],
];

function card(title) {
  const el = document.createElement("div");
  el.className = "cmd-card";
  if (title) el.append(Object.assign(document.createElement("div"), { className: "cmd-title", textContent: title }));
  return el;
}

function cmdRow({ main, sub, end, current = false, onClick = null }) {
  const row = document.createElement(onClick ? "button" : "div");
  row.className = "cmd-row" + (current ? " current" : "");
  if (onClick) {
    row.type = "button";
    row.addEventListener("click", () => onClick(row));
  }
  const text = document.createElement("span");
  text.className = "main";
  text.textContent = main;
  if (sub) text.append(Object.assign(document.createElement("span"), { className: "sub", textContent: sub }));
  row.append(text);
  if (end) {
    const tail = document.createElement("span");
    tail.className = "end";
    tail.append(end);
    row.append(tail);
  }
  return row;
}

function renderHelp() {
  const el = card("Commands");
  const rows = document.createElement("div");
  rows.className = "cmd-rows";
  for (const c of COMMANDS) {
    rows.append(cmdRow({
      main: `${c.name} ${c.args}`.trim(),
      sub: c.desc,
      onClick: () => { input.value = c.args ? `${c.name} ` : c.name; input.focus(); updateCmdMenu(); },
    }));
  }
  el.append(rows);
  return el;
}

const COMMAND_CARDS = {
  stats: (cmd) => {
    const el = card("Stats");
    el.append(buildStats(cmd.stats));
    return el;
  },

  cache: (cmd) => {
    if (cmd.action === "delete") {
      const el = card();
      el.append(`Saved answer ${cmd.id} deleted.`);
      return el;
    }
    if (cmd.action === "list") {
      const el = card(`Saved answers (${cmd.entries.length})`);
      if (!cmd.entries.length) {
        el.append("No saved answers yet.");
        return el;
      }
      const rows = document.createElement("div");
      rows.className = "cmd-rows";
      for (const e of cmd.entries) {
        rows.append(cmdRow({ main: e.question, sub: cacheEntryMeta(e), end: `#${e.id}` }));
      }
      el.append(rows);
      return el;
    }
    const el = card("Semantic cache");
    const onOff = (value) => (value ? "on" : "off");
    const settings = document.createElement("div");
    settings.className = "cmd-sub";
    settings.textContent = `${cmd.enabled ? "On" : "Off"} · threshold ${cmd.threshold.toFixed(2)} · verify ${onOff(cmd.verify)} · mid-chat ${onOff(cmd.mid_chat)}`;
    const grid = document.createElement("div");
    grid.className = "stat-grid three";
    grid.append(
      statTile(cmd.entries.toLocaleString(), "Saved answers"),
      statTile(cmd.hits.toLocaleString(), "Reused"),
      statTile(formatSeconds(cmd.saved_s), "Time saved"),
    );
    el.append(settings, grid);
    if (cmd.note) el.append(Object.assign(document.createElement("div"), { className: "cmd-note", textContent: cmd.note }));
    return el;
  },

  schemas: (cmd) => {
    if (cmd.action === "list") {
      const el = card(`JSON schemas (${cmd.schemas.length})`);
      const rows = document.createElement("div");
      rows.className = "cmd-rows";
      for (const s of cmd.schemas) {
        rows.append(cmdRow({
          main: s.name,
          sub: s.fields ? s.fields.join(", ") : s.error,
          onClick: () => { input.value = `/json ${s.name} `; input.focus(); updateCmdMenu(); },
        }));
      }
      el.append(rows, Object.assign(document.createElement("div"), { className: "cmd-note", textContent: "? = optional. Click one to use it with /json." }));
      return el;
    }
    if (cmd.action === "show") {
      const el = card(`Schema · ${cmd.name}`);
      el.append(Object.assign(document.createElement("pre"), { textContent: cmd.output }));
      return el;
    }
    return null;
  },

  guardrails: (cmd) => {
    const g = cmd.guardrails;
    const el = card(`Guardrails · ${g.enabled ? "on" : "off"}`);
    const rows = document.createElement("div");
    rows.className = "cmd-rows";
    const refused = Object.entries(GUARD_LABELS).map(([key, label]) => `${label}: ${g.blocked_categories.includes(key) ? "refused" : "allowed"}`);
    rows.append(
      cmdRow({ main: "Personal data", sub: Object.entries(g.pii).map(([type, action]) => `${type.toLowerCase()} ${action}`).join(" · ") }),
      cmdRow({ main: "Messages", sub: refused.join(" · ") }),
      cmdRow({ main: "Answer check", sub: g.output_check ? "on: harmful or leaking answers are withdrawn" : "off" }),
      cmdRow({ main: "Refusal message", sub: g.blocked_message }),
    );
    el.append(rows);
    const note = cmd.changed ? "Saved, applies to the next message." : g.enabled ? "" : "Guardrails are off: /guardrails on checks messages again.";
    if (note) el.append(Object.assign(document.createElement("div"), { className: "cmd-note", textContent: note }));
    return el;
  },

  persona: (cmd) => {
    if (cmd.action === "switch") {
      showPersona({ key: cmd.persona, name: cmd.name });
      loadSessions();
      const el = card();
      el.append(`Now talking to ${cmd.name}. The conversation so far is kept.`);
      return el;
    }
    const el = card("Personas");
    const rows = document.createElement("div");
    rows.className = "cmd-rows";
    for (const p of cmd.personas.filter((q) => !q.problem)) {
      const end = document.createElement("span");
      if (p.current) end.append(" ✓");
      rows.append(cmdRow({
        main: p.name,
        sub: p.role,
        end,
        current: p.current,
        onClick: async () => {
          if (!(await choosePersona(p))) return;
          rows.querySelectorAll(".cmd-row").forEach((r) => r.classList.remove("current"));
          rows.children[cmd.personas.filter((q) => !q.problem).indexOf(p)].classList.add("current");
          cmd.personas.forEach((q) => { q.current = q === p; });
        },
      }));
    }
    el.append(rows);
    return el;
  },

  prompt: (cmd) => {
    if (cmd.action === "versions") {
      const el = card("Prompt versions");
      const rows = document.createElement("div");
      rows.className = "cmd-rows";
      const latest = cmd.versions.length ? cmd.versions[cmd.versions.length - 1].version : null;
      for (const v of [...cmd.versions].reverse()) {
        rows.append(cmdRow({
          main: `v${v.version}${v.version === latest ? " (latest)" : ""}`,
          end: formatWhen(v.time),
          onClick: () => openPromptPanel(v.version),
        }));
      }
      if (!cmd.versions.length) el.append("No versions saved yet.");
      else el.append(rows);
      return el;
    }
    if (cmd.action === "show") {
      const el = card(`Prompt v${cmd.version}`);
      el.append(Object.assign(document.createElement("pre"), { textContent: cmd.content }));
      return el;
    }
    if (cmd.action === "compare") {
      const el = card("Prompt comparison");
      const grid = document.createElement("div");
      grid.className = "pv-results";
      grid.append(...buildComparison(cmd.comparisons));
      el.append(grid);
      return el;
    }
    return null;
  },

  permissions: (cmd) => {
    const el = card("File access");
    el.append(permissionsEditor(cmd.permissions));
    return el;
  },

  branches: (cmd) => {
    if (cmd.action === "switch") {
      // the conversation on the page is the old branch, show the one the server switched to
      api("/session").then(renderSession).catch(() => {});
      return null;
    }
    const el = card("Branches");
    if (cmd.branches.length <= 1) {
      el.append("Only one branch so far. Edit a past message to create another.");
      return el;
    }
    const rows = document.createElement("div");
    rows.className = "cmd-rows";
    for (const b of cmd.branches) {
      rows.append(cmdRow({
        main: b.preview || "(empty branch)",
        end: b.current ? "current ✓" : "",
        current: b.current,
        onClick: b.current ? null : () => switchBranch(b.id),
      }));
    }
    el.append(rows);
    const open = Object.assign(document.createElement("button"), { type: "button", className: "settings-btn-secondary", textContent: "Show branch tree" });
    open.style.marginTop = "8px";
    open.addEventListener("click", openBranchTree);
    el.append(open);
    return el;
  },
};

// replace the plain-text output the server streamed with a structured view of the command's payload
export function showCommandResult(bot, cmd) {
  if (cmd.usage) {
    // aligned in columns for the terminal: keep them aligned here
    const el = card();
    el.append(Object.assign(document.createElement("pre"), { className: "cmd-usage", textContent: cmd.output }));
    bot.bubble.textContent = "";
    bot.wrap.append(el);
    scrollDown();
    return;
  }
  if (!cmd.ok) return; // errors stay as text
  if (cmd.command === "json") {
    bot.bubble.textContent = "";
    addJsonBlock(bot.wrap, cmd);
    return;
  }
  const build = COMMAND_CARDS[cmd.command];
  const el = build?.(cmd);
  if (el) {
    bot.bubble.textContent = "";
    bot.wrap.append(el);
    scrollDown();
  }
}

// handled in the page: they only touch what is already on screen
export function runLocalCommand(text) {
  if (text === "/help") {
    addMessage("bot").wrap.append(renderHelp());
    scrollDown();
    return true;
  }
  const edit = text.match(/^\/edit\s+(\d+)\s+([\s\S]+)$/);
  if (edit) {
    forkAndSend(Number(edit[1]), edit[2].trim());
    return true;
  }
  return false;
}
