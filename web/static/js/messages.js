import { chat, emptyEl, scrollDown } from "./dom.js";
import { renderMarkdown } from "./markdown.js";
import { startEdit } from "./branches.js";

// an assistant bubble streams in: re-render its text at most once per frame
export function appendStreamed(bot, text) {
  bot.raw += text;
  bot.frame ||= requestAnimationFrame(() => flushStreamed(bot));
}

export function flushStreamed(bot) {
  if (!bot.frame) return;
  cancelAnimationFrame(bot.frame);
  bot.frame = 0;
  bot.bubble.replaceChildren(renderMarkdown(bot.raw));
  scrollDown();
}

// the bubble stops being markdown, for errors, notices and plain command output
export function setPlain(bot, text) {
  cancelAnimationFrame(bot.frame);
  bot.frame = 0;
  bot.raw = "";
  bot.bubble.classList.remove("md");
  bot.bubble.textContent = text;
}

export function addBubble(wrap, text = "", markdown = false) {
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (markdown) {
    bubble.classList.add("md");
    bubble.append(renderMarkdown(text));
  } else {
    bubble.textContent = text;
  }
  wrap.append(bubble);
  return bubble;
}

// tool results are JSON text from the server; show them indented when they parse
function prettyJson(value) {
  if (typeof value === "string") {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch (e) { return value; }
  }
  return JSON.stringify(value, null, 2);
}

function toolBadge(kind, text, title = "") {
  return Object.assign(document.createElement("span"), { className: `tool-badge ${kind}`, textContent: text, title });
}

function toolSummary(call) {
  const summary = document.createElement("summary");
  if (call.round) summary.append(Object.assign(document.createElement("span"), { className: "tool-step", textContent: `step ${call.round}` }));
  summary.append(Object.assign(document.createElement("span"), { className: "tool-name", textContent: call.name }));
  if (call.server) summary.append(Object.assign(document.createElement("span"), { className: "tool-server", textContent: call.server, title: "MCP server" }));
  if (call.attempt > 1) {
    summary.append(Object.assign(document.createElement("span"), {
      className: "tool-attempt",
      textContent: `attempt ${call.attempt} of ${call.max_attempts}`,
    }));
  }
  return summary;
}

function toolSection(body, label, value) {
  body.append(
    Object.assign(document.createElement("div"), { className: "tool-label", textContent: label }),
    Object.assign(document.createElement("pre"), { textContent: prettyJson(value) }),
  );
}

// shown while the tool runs (or waits for approval), replaced by addToolBlock once it returns
export function addPendingTool(wrap, call) {
  const block = document.createElement("details");
  block.className = "tool running";
  const summary = toolSummary(call);
  summary.append(toolBadge("running", "running…"));
  const body = document.createElement("div");
  body.className = "tool-body";
  toolSection(body, "Arguments", call.arguments);
  block.append(summary, body);
  wrap.append(block);
  scrollDown();
  return block;
}

export function addToolBlock(wrap, call, pending = null) {
  let parsed = null;
  try { parsed = JSON.parse(call.result); } catch (e) {}
  // run_python's result: where the code ran and how it exited
  const run = parsed?.result && typeof parsed.result === "object" && "returncode" in parsed.result ? parsed.result : null;
  const failed = !!parsed && ("error" in parsed || (run !== null && run.returncode !== 0));

  const block = document.createElement("details");
  block.className = "tool";
  const summary = toolSummary(call);
  if (call.duration_s != null) summary.append(Object.assign(document.createElement("span"), { className: "tool-time", textContent: `${call.duration_s}s` }));
  if (run?.sandbox) {
    summary.append(run.sandbox === "docker"
      ? toolBadge("sandbox", "docker", "Ran in a Docker container: no network, no access to your files")
      : toolBadge("sandbox weak", run.sandbox, run.note || "Ran in a plain subprocess: not isolated from this machine"));
  }
  const status = ["refused", "denied", "skipped"].includes(call.decision) ? call.decision : failed ? "failed" : call.decision;
  if (status) summary.append(toolBadge(status, status));
  const body = document.createElement("div");
  body.className = "tool-body";
  toolSection(body, "Arguments", call.arguments);
  toolSection(body, "Result", call.result);
  block.append(summary, body);
  if (pending?.isConnected) pending.replaceWith(block);
  else wrap.append(block);
  scrollDown();
}

export function addMessage(role, text = "", turn = null) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const bubble = addBubble(wrap, text, role === "bot");
  if (role === "user" && turn !== null) {
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "edit-btn";
    editBtn.setAttribute("aria-label", "Edit message");
    editBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
    editBtn.addEventListener("click", () => startEdit(turn, bubble, editBtn));
    const tools = document.createElement("div");
    tools.className = "user-tools";
    tools.append(editBtn);
    wrap.append(tools);
  }
  emptyEl.remove();
  chat.append(wrap);
  scrollDown();
  return { wrap, bubble };
}

const SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 4.5-3.2 8.2-8 9-4.8-.8-8-4.5-8-9V6z"/></svg>';

export const BLOCK_LABELS = {
  off_topic: "Off topic",
  harmful: "Harmful request",
  asks_for_others_data: "Asks for someone else's data",
  others_data: "Shares someone else's data",
  pii: "Contains sensitive data",
  check_failed: "Safety check unavailable",
};

// "email ×2, phone" from the {EMAIL: 2, PHONE: 1} counts the server sends
export function piiSummary(pii) {
  return Object.entries(pii).map(([type, n]) => `${type.toLowerCase()}${n > 1 ? ` ×${n}` : ""}`).join(", ");
}

// a small guardrails line under a message; `detail` is an optional second line
export function addGuardNote(wrap, text, { blocked = false, detail = "" } = {}) {
  const note = document.createElement("div");
  note.className = "guard-note" + (blocked ? " blocked" : "");
  note.innerHTML = SHIELD;
  const body = document.createElement("span");
  body.textContent = text;
  if (detail) body.append(Object.assign(document.createElement("span"), { className: "reason", textContent: detail }));
  note.append(body);
  wrap.append(note);
  scrollDown();
  return note;
}

const VERDICT_MARKS = { contradicted: "✗", unsupported: "?", supported: "≠" };

// the claims of an answer that nothing supports, under it. `claims` are {claim, problem?, confidence?, verdict?}
export function addFactNote(wrap, claims, { total = null, action = "warn", again = false } = {}) {
  const count = total ? `${claims.length} of ${total} claims` : `${claims.length} claim${claims.length === 1 ? "" : "s"}`;
  const note = document.createElement("div");
  note.className = "guard-note fact-note";
  note.innerHTML = SHIELD;
  const body = document.createElement("span");
  const title = again ? `Still not supported after answering again: ${count}` : `Not supported by anything the assistant had: ${count}`;
  body.append(Object.assign(document.createElement("strong"), { textContent: title }));
  const list = document.createElement("ul");
  list.className = "fact-claims";
  for (const item of claims) {
    const row = document.createElement("li");
    row.append(Object.assign(document.createElement("span"), {
      className: "fact-mark", textContent: VERDICT_MARKS[item.verdict] || "?", title: item.verdict || "unsupported",
    }));
    row.append(document.createTextNode(item.claim));
    if (item.problem) {
      const score = item.confidence == null ? "" : ` (confidence ${Number(item.confidence).toFixed(2)})`;
      row.append(Object.assign(document.createElement("span"), { className: "reason", textContent: item.problem + score }));
    }
    list.append(row);
  }
  body.append(list);
  note.append(body);
  wrap.append(note);
  scrollDown();
  return note;
}

export const answerLabel = (category) => (category === "harmful" ? "Harmful content" : BLOCK_LABELS[category] || category);

export function markWithdrawn(wrap, { category, reason }) {
  wrap.classList.add("blocked");
  addGuardNote(wrap, `Answer withdrawn: ${answerLabel(category)}`, { blocked: true, detail: reason });
}

// the output check refused the streamed answer: everything shown for this turn is replaced
export function withdrawAnswer(bot, message, decision) {
  cancelAnimationFrame(bot.frame);
  bot.frame = 0;
  bot.raw = "";
  bot.wrap.replaceChildren();
  bot.bubble = addBubble(bot.wrap, message);
  markWithdrawn(bot.wrap, decision);
  if (decision.forgotten) addMeta(bot.wrap, "also removed from the saved answers", "cache-note");
}

export function addMeta(wrap, text, extraClass = "") {
  const meta = document.createElement("div");
  meta.className = extraClass ? `meta ${extraClass}` : "meta";
  meta.textContent = text;
  wrap.append(meta);
}

export function formatStats(s) {
  const prompt = s.prompt_tokens ?? 0;
  const response = s.response_tokens ?? 0;
  const parts = [
    `${prompt.toLocaleString()} prompt`,
    `${response.toLocaleString()} response`,
    `${(prompt + response).toLocaleString()} total`,
  ];
  if (s.eval_duration_s > 0 && response > 0) {
    parts.push(`${(response / s.eval_duration_s).toFixed(1)} tok/s`);
  }
  if (s.total_duration_s > 0) parts.push(`${s.total_duration_s.toFixed(1)}s`);
  return parts.join(" · ");
}

export function formatSeconds(seconds) {
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)} min ${total % 60} s`;
}

export function cacheMeta(data) {
  return `answered from cache in ${formatSeconds(data.stats.total_duration_s ?? 0)} (saved ${formatSeconds(data.saved_s ?? 0)})`;
}
