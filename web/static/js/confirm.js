import { api } from "./api.js";

const confirmDialog = document.getElementById("confirm-dialog");
const confirmArgs = document.getElementById("confirm-args");
const confirmError = document.getElementById("confirm-error");
const confirmEdit = document.getElementById("confirm-edit");
const confirmYes = document.getElementById("confirm-yes");
let confirmCall = null;

export function showConfirm({ id, tool, arguments: args }) {
  confirmCall = { id, original: JSON.stringify(args, null, 2) };
  document.getElementById("confirm-tool").textContent = tool;
  confirmArgs.value = confirmCall.original;
  confirmArgs.readOnly = true;
  confirmError.textContent = "";
  confirmEdit.hidden = false;
  confirmYes.textContent = "Yes";
  if (!confirmDialog.open) confirmDialog.showModal();
  confirmYes.focus();
}

export function closeConfirm() {
  confirmCall = null;
  if (confirmDialog.open) confirmDialog.close();
}

async function answerConfirm(decision, args) {
  const call = confirmCall;
  if (!call) return;
  try {
    await api(`/confirm/${call.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, arguments: args }),
    });
    closeConfirm();
  } catch (err) {
    // the turn was stopped or ended before the answer arrived, nothing is waiting any more
    closeConfirm();
    alert(err.message);
  }
}

export function initConfirm() {
  confirmEdit.addEventListener("click", () => {
    confirmArgs.readOnly = false;
    confirmEdit.hidden = true;
    confirmYes.textContent = "Run with changes";
    confirmArgs.focus();
  });

  confirmYes.addEventListener("click", () => {
    if (confirmArgs.readOnly || confirmArgs.value === confirmCall.original) return answerConfirm("approve");
    let args;
    try { args = JSON.parse(confirmArgs.value); } catch (e) { args = null; }
    if (args === null || typeof args !== "object" || Array.isArray(args)) {
      confirmError.textContent = "Arguments must be a valid JSON object.";
      return;
    }
    answerConfirm("edit", args);
  });

  document.getElementById("confirm-no").addEventListener("click", () => answerConfirm("refuse"));

  // Escape counts as a refusal, a tool never runs without an explicit yes
  confirmDialog.addEventListener("cancel", (e) => {
    e.preventDefault();
    answerConfirm("refuse");
  });
}
