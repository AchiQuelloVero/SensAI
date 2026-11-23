import { api } from "./api.js";
import { closeSidebar } from "./dom.js";

const profileDialog = document.getElementById("profile-dialog");
const pfPrefs = document.getElementById("pf-prefs");
const pfStatus = document.getElementById("pf-status");

function addPrefRow(key = "", value = "") {
  const row = document.createElement("div");
  row.className = "pf-pref";
  const k = Object.assign(document.createElement("input"), { type: "text", value: key, placeholder: "name" });
  const v = Object.assign(document.createElement("input"), { type: "text", value, placeholder: "value" });
  k.setAttribute("aria-label", "Preference name");
  v.setAttribute("aria-label", "Preference value");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon-btn";
  remove.setAttribute("aria-label", "Remove preference");
  remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  remove.addEventListener("click", () => row.remove());
  row.append(k, v, remove);
  pfPrefs.append(row);
  return k;
}

function showProfile(profile) {
  document.getElementById("pf-name").value = profile.name;
  document.getElementById("pf-instructions").value = profile.custom_instructions;
  pfPrefs.replaceChildren();
  for (const [key, value] of Object.entries(profile.preferences)) addPrefRow(key, value);
  document.getElementById("pf-preview").textContent = profile.injected || "(nothing, the profile is empty)";
}

async function openProfile() {
  closeSidebar();
  pfStatus.textContent = "";
  try {
    showProfile(await api("/profile"));
  } catch (err) {
    alert(err.message);
    return;
  }
  profileDialog.showModal();
}

export function initProfile() {
  document.getElementById("pf-add").addEventListener("click", () => addPrefRow().focus());

  document.getElementById("profile-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const save = document.getElementById("pf-save");
    const preferences = {};
    for (const row of pfPrefs.children) {
      const [k, v] = row.querySelectorAll("input");
      if (k.value.trim()) preferences[k.value.trim()] = v.value;
    }
    save.disabled = true;
    pfStatus.style.color = "";
    pfStatus.textContent = "";
    try {
      const profile = await api("/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: document.getElementById("pf-name").value,
          preferences,
          custom_instructions: document.getElementById("pf-instructions").value,
          apply_to_chat: document.getElementById("pf-apply").checked,
        }),
      });
      showProfile(profile);
      pfStatus.textContent = document.getElementById("pf-apply").checked
        ? "Saved. New chats and this chat use the updated profile."
        : "Saved. New chats use the updated profile.";
    } catch (err) {
      pfStatus.style.color = "var(--error)";
      pfStatus.textContent = err.message;
    } finally {
      save.disabled = false;
    }
  });

  document.getElementById("open-profile").addEventListener("click", openProfile);
  document.getElementById("profile-close").addEventListener("click", () => profileDialog.close());
}
