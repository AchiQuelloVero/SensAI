import { api } from "./api.js";
import { closeSidebar } from "./dom.js";
import { formatSeconds, BLOCK_LABELS, answerLabel } from "./messages.js";

const total = (counts) => Object.values(counts).reduce((a, b) => a + b, 0);

function section(box, title, rows) {
  if (!rows.length) return;
  box.append(Object.assign(document.createElement("div"), { className: "stats-section", textContent: title }));
  for (const [label, count] of rows.sort((a, b) => b[1] - a[1])) {
    const row = document.createElement("div");
    row.className = "decision-row";
    row.append(Object.assign(document.createElement("span"), { textContent: label }), Object.assign(document.createElement("span"), { textContent: count }));
    box.append(row);
  }
}

function guardrailStats(box, g) {
  if (!g || !(g.messages_checked || g.answers_checked)) return;
  const refused = total(g.messages_refused);
  const withdrawn = total(g.answers_withdrawn);
  const grid = document.createElement("div");
  grid.className = "stat-grid three";
  grid.append(
    statTile(refused.toLocaleString(), "Messages refused", { hint: `of ${g.messages_checked} checked` }),
    statTile(withdrawn.toLocaleString(), "Answers withdrawn", { hint: `of ${g.answers_checked} checked` }),
    statTile((total(g.masked_in_messages) + total(g.masked_in_answers)).toLocaleString(), "Data masked", {
      hint: `${total(g.masked_in_messages)} in messages, ${total(g.masked_in_answers)} in answers`,
    }),
  );
  box.append(Object.assign(document.createElement("div"), { className: "stats-section", textContent: "Guardrails" }), grid);
  const label = (key) => BLOCK_LABELS[key] || key;
  section(box, "Refused messages", Object.entries(g.messages_refused).map(([k, n]) => [label(k), n]));
  section(box, "Withdrawn answers", Object.entries(g.answers_withdrawn).map(([k, n]) => [answerLabel(k), n]));
  section(box, "Masked in messages", Object.entries(g.masked_in_messages));
  section(box, "Masked in answers", Object.entries(g.masked_in_answers));
}

const statsDialog = document.getElementById("stats-dialog");
const statsBody = document.getElementById("stats-body");

export function statTile(value, label, { bad = false, hint = "" } = {}) {
  const tile = document.createElement("div");
  tile.className = "stat";
  const v = document.createElement("div");
  v.className = "value" + (bad ? " bad" : "");
  v.textContent = value;
  const l = document.createElement("div");
  l.className = "label";
  l.textContent = label;
  tile.append(v, l);
  if (hint) {
    const h = document.createElement("div");
    h.className = "hint";
    h.textContent = hint;
    tile.append(h);
  }
  return tile;
}

export function buildStats(stats) {
  const box = document.createElement("div");
  if (!stats.total_records) {
    box.append(Object.assign(document.createElement("div"), { className: "stats-empty", textContent: "No calls logged yet." }));
    return box;
  }
  const grid = document.createElement("div");
  grid.className = "stat-grid";
  grid.append(
    statTile(stats.model_calls.toLocaleString(), "Model calls"),
    statTile(`${(stats.error_rate * 100).toFixed(0)}%`, "Error rate", {
      bad: stats.errors > 0,
      hint: `${stats.errors} of ${stats.model_calls} failed`,
    }),
    statTile(stats.ok_calls ? `${stats.avg_latency_s.toFixed(1)}s` : "–", "Avg latency"),
    statTile(stats.ok_calls ? stats.tokens_per_s.toFixed(1) : "–", "Tokens / second"),
    statTile(stats.total_tokens.toLocaleString(), "Response tokens"),
    statTile(stats.cache_hits ? formatSeconds(stats.cache_saved_s) : "–", "Saved by cache", {
      hint: `${stats.cache_hits} answer${stats.cache_hits === 1 ? "" : "s"} from cache`,
    }),
  );
  box.append(grid);

  if (stats.tool_decisions_total) {
    const title = document.createElement("div");
    title.className = "stats-section";
    title.textContent = `${stats.tool_decisions_total} tool decision${stats.tool_decisions_total === 1 ? "" : "s"}`;
    box.append(title);
    for (const [decision, count] of Object.entries(stats.tool_decisions).sort((a, b) => b[1] - a[1])) {
      const row = document.createElement("div");
      row.className = "decision-row";
      row.append(Object.assign(document.createElement("span"), { textContent: decision }), Object.assign(document.createElement("span"), { textContent: count }));
      box.append(row);
    }
  }
  if (stats.json_replies) {
    section(box, `${stats.json_replies} JSON repl${stats.json_replies === 1 ? "y" : "ies"}`, [
      ["valid on the first attempt", stats.json_replies - stats.json_retried],
      ["needed a retry", stats.json_retried - stats.json_failed],
      ["failed after every retry", stats.json_failed],
    ].filter(([, n]) => n));
  }
  guardrailStats(box, stats.guardrails);
  return box;
}

function renderStats(stats) {
  statsBody.replaceChildren(buildStats(stats));
}

async function openStats() {
  closeSidebar();
  try {
    renderStats(await api("/stats"));
  } catch (err) {
    alert(err.message);
    return;
  }
  statsDialog.showModal();
}

export function initStats() {
  document.getElementById("open-stats").addEventListener("click", openStats);
  document.getElementById("stats-close").addEventListener("click", () => statsDialog.close());
}
