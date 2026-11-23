import { send, sessionsEl } from "./dom.js";
import { personaBtn, personaMenu } from "./persona.js";
import { jsonToggle, schemaSelect } from "./json-mode.js";

export let busy = false;

export function setBusy(value) {
  busy = value;
  send.classList.toggle("busy", value);
  send.setAttribute("aria-label", value ? "Stop" : "Send");
  document.getElementById("new-chat").disabled = value;
  document.getElementById("branch-select").disabled = value;
  document.getElementById("branch-tree-btn").disabled = value;
  document.getElementById("settings-btn").disabled = value;
  personaBtn.disabled = value;
  jsonToggle.disabled = value;
  schemaSelect.disabled = value;
  if (value) personaMenu.hidden = true;
  sessionsEl.querySelectorAll(".session, .session-action").forEach((b) => (b.disabled = value));
}
