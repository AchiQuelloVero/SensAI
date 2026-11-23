import { api, readEvents } from "./api.js";
import { chat, scroller, form, input, send, emptyEl, scrollDown } from "./dom.js";
import { busy, setBusy } from "./state.js";
import {
  addMessage, addBubble, addToolBlock, addPendingTool, addMeta, addGuardNote,
  appendStreamed, flushStreamed, setPlain, piiSummary, formatStats, formatSeconds, cacheMeta, BLOCK_LABELS,
  markWithdrawn, withdrawAnswer, addFactNote,
} from "./messages.js";
import { showConfirm, closeConfirm } from "./confirm.js";
import { applyForks, loadBranches } from "./branches.js";
import { highlightActive, loadSessions } from "./sessions.js";
import { showPersona } from "./persona.js";
import { jsonMode, schemaSelect } from "./json-mode.js";
import { showCommandResult, runLocalCommand } from "./commands.js";
import { hideCmdMenu } from "./autocomplete.js";
import { showContext } from "./context.js";
import { renderMarkdown } from "./markdown.js";

export let currentId = null;
let controller = null;

const SUGGESTIONS = [
  ["Explain something", "Explain how neural networks learn, as if I were new to it"],
  ["Search the web", "Search the web for the latest news about open-source AI models"],
  ["Do some math", "What is 1234 multiplied by 5678, plus 91?"],
  ["Draft a message", "Help me write a polite email asking my teacher for a deadline extension"],
  ["Learn a skill", "Give me a 4-week plan to learn Python from scratch"],
  ["Brainstorm", "Suggest 5 creative project ideas for a weekend hackathon"],
];

export function renderSession(session) {
  currentId = session.id;
  showPersona(session.persona);
  showContext(session.context);
  chat.replaceChildren();
  if (session.messages.length === 0) chat.append(emptyEl);
  let userTurn = 0;
  let bot = null; // the assistant messages and tool calls of one turn share a single block
  for (const m of session.messages) {
    if (m.role === "user") {
      userTurn += 1;
      bot = null;
      addMessage("user", m.content, userTurn);
    } else if (m.role === "tool") {
      bot ??= addMessage("bot");
      addToolBlock(bot.wrap, m);
    } else if (bot) {
      addBubble(bot.wrap, m.content, true);
    } else {
      bot = addMessage("bot", m.content);
    }
    if (m.guardrail) markWithdrawn(bot.wrap, m.guardrail);
    if (m.check?.flagged?.length) addFactNote(bot.wrap, m.check.flagged.map((claim) => ({ claim })), { action: m.check.action });
  }
  applyForks(session.forks);
  scroller.scrollTop = scroller.scrollHeight;
  highlightActive();
  loadBranches();
}

export async function sendMessage(text, schema = null) {
  // only messages saved to the session keep an edit button, so this matches the server's numbering
  const turn = chat.querySelectorAll(".msg.user .edit-btn").length + 1;
  const user = addMessage("user", text, turn);
  if (schema) addMeta(user.wrap, `JSON mode · ${schema}`);
  let saved = false;
  let checking = null;
  let factChecking = null;
  let factNote = null;
  let regenNote = null;
  let pending = null;
  const bot = addMessage("bot");
  bot.raw = "";
  bot.frame = 0;
  bot.bubble.classList.add("typing");
  controller = new AbortController();
  setBusy(true);

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, json_schema: schema }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail;
      throw new Error(detail || `Server error (${res.status})`);
    }

    for await (const { event, data } of readEvents(res.body)) {
      if (event === "token") {
        appendStreamed(bot, data.text);
      } else if (event === "confirm") {
        showConfirm(data);
      } else if (event === "tool_start") {
        flushStreamed(bot);
        bot.bubble.classList.remove("typing");
        pending = addPendingTool(bot.wrap, data);
      } else if (event === "tool_call") {
        // text after the call goes into a new bubble below the block
        flushStreamed(bot);
        bot.bubble.classList.remove("typing");
        addToolBlock(bot.wrap, data, pending);
        pending = null;
        bot.bubble = addBubble(bot.wrap, "", true);
        bot.raw = "";
        bot.bubble.classList.add("typing");
      } else if (event === "masked") {
        addGuardNote(user.wrap, `Masked before sending: ${piiSummary(data.pii)}`);
      } else if (event === "output_masked") {
        addGuardNote(bot.wrap, `Personal data was hidden in this answer: ${piiSummary(data.pii)}`);
      } else if (event === "output_checking") {
        flushStreamed(bot);
        bot.bubble.classList.remove("typing");
        checking = addGuardNote(bot.wrap, "Checking the answer is safe…");
      } else if (event === "output_blocked") {
        checking?.remove();
        withdrawAnswer(bot, data.message, data);
      } else if (event === "fact_checking") {
        flushStreamed(bot);
        bot.bubble.classList.remove("typing");
        factChecking = addGuardNote(bot.wrap, data.again ? "Checking the new answer…" : "Fact-checking the answer…");
      } else if (event === "fact_check") {
        factChecking?.remove();
        factChecking = null;
        regenNote?.remove();
        regenNote = null;
        factNote?.remove();
        factNote = data.flagged.length
          ? addFactNote(bot.wrap, data.flagged, { total: data.claims, action: data.action, again: data.again })
          : null;
      } else if (event === "fact_unchecked") {
        regenNote?.remove();
        factChecking?.remove();
        factChecking = null;
        addGuardNote(bot.wrap, `Answer not fact-checked: ${data.reason}`);
      } else if (event === "fact_refused") {
        // the answer is replaced; the flagged claims stay listed below to say why
        bot.wrap.classList.add("blocked");
        cancelAnimationFrame(bot.frame);
        bot.frame = 0;
        setPlain(bot, data.message);
      } else if (event === "fact_regenerating") {
        // the flagged answer is replaced by the new one, written into the same bubble
        bot.raw = "";
        bot.bubble.replaceChildren();
        bot.bubble.classList.add("typing");
        regenNote = addGuardNote(bot.wrap, "Answering again with the fact-checker's feedback…");
      } else if (event === "fact_kept") {
        cancelAnimationFrame(bot.frame);
        bot.frame = 0;
        bot.raw = data.message;
        bot.bubble.replaceChildren(renderMarkdown(data.message));
        regenNote?.remove();
        bot.bubble.classList.remove("typing");
        addGuardNote(bot.wrap, `Could not answer again, keeping the first answer: ${data.reason}`);
      } else if (event === "blocked") {
        bot.wrap.classList.add("blocked");
        setPlain(bot, data.message);
        const pii = Object.keys(data.pii || {}).length ? ` (${piiSummary(data.pii)})` : "";
        addGuardNote(bot.wrap, `Blocked: ${BLOCK_LABELS[data.category] || data.category}${pii}`, {
          blocked: true,
          detail: data.reason,
        });
      } else if (event === "error") {
        throw new Error(data.message);
      } else if (event === "done") {
        flushStreamed(bot);
        checking?.remove();
        factChecking?.remove();
        showContext(data.context);
        if (!data.command) {
          saved = true;
          const check = data.output_check;
          const safe = check?.action === "allow" ? ` · checked safe in ${formatSeconds(check.duration_s)}` : "";
          const fact = data.fact_check;
          const facts = fact && !fact.flagged ? ` · ${fact.claims} claim${fact.claims === 1 ? "" : "s"} fact-checked in ${formatSeconds(fact.duration_s)}` : "";
          addMeta(bot.wrap, (data.cached ? cacheMeta(data) : formatStats(data.stats)) + safe + facts);
          if (data.cache_note) addMeta(bot.wrap, `cache: ${data.cache_note}`, "cache-note");
        } else {
          setPlain(bot, bot.raw); // command output is not markdown
          showCommandResult(bot, data.command);
        }
        scrollDown();
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      saved = true; // the server keeps a stopped message, marked as interrupted
      setPlain(bot, "(stopped)");
    } else {
      bot.wrap.classList.add("error");
      setPlain(bot, err.message);
    }
  } finally {
    // blocked messages, slash commands and errors never reach the session
    if (!saved) user.wrap.querySelector(".user-tools")?.remove();
    controller = null;
    flushStreamed(bot);
    closeConfirm();
    bot.wrap.querySelectorAll(".typing").forEach((el) => el.classList.remove("typing"));
    setBusy(false);
    loadSessions();
    loadBranches();
    api("/session").then((session) => applyForks(session.forks)).catch(() => {});
    input.focus();
  }
}

export function initChat() {
  for (const [title, prompt] of SUGGESTIONS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = title;
    chip.title = prompt;
    chip.addEventListener("click", () => { if (!busy) sendMessage(prompt); });
    document.getElementById("suggestions").append(chip);
  }

  send.addEventListener("click", (e) => {
    if (busy) {
      e.preventDefault();
      controller?.abort();
    }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    input.style.height = "auto";
    hideCmdMenu();
    if (runLocalCommand(text)) return;
    sendMessage(text, jsonMode() ? schemaSelect.value : null);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = input.scrollHeight + "px";
  });
}
