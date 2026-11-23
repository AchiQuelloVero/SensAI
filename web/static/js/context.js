const meter = document.getElementById("ctx-meter");
const fill = document.getElementById("ctx-fill");
const label = document.getElementById("ctx-label");
const short = document.getElementById("ctx-short");

// near the limit Ollama drops the oldest messages, so warn before that
const FULL_RATIO = 0.9;

export function showContext(context) {
  if (!context) return;
  const { used = 0, window: size = 0 } = context;
  meter.hidden = !size;
  if (!size) return;
  const ratio = Math.min(used / size, 1);
  fill.style.width = `${(ratio * 100).toFixed(1)}%`;
  meter.classList.toggle("full", ratio >= FULL_RATIO);
  label.textContent = `${used ? used.toLocaleString() : "–"} / ${size.toLocaleString()} tokens`;
  short.textContent = `${used ? compact(used) : "–"} / ${compact(size)}`;
  meter.setAttribute("aria-valuenow", used);
  meter.setAttribute("aria-valuemax", size);
  meter.title = ratio >= FULL_RATIO
    ? "The context window is almost full: the oldest messages will be dropped"
    : "Context used by this conversation / context window of the model";
}

function compact(n) {
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${+(n / 1e3).toFixed(1)}k`;
  return String(n);
}
