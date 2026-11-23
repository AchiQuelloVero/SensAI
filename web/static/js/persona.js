import { api } from "./api.js";
import { busy } from "./state.js";
import { loadSessions } from "./sessions.js";
import { openPersonaEditor } from "./persona-editor.js";

export const personaBtn = document.getElementById("persona-btn");
export const personaMenu = document.getElementById("persona-menu");
export const personaNames = {};

export function showPersona(persona) {
  personaBtn.hidden = !persona;
  if (!persona) return;
  document.getElementById("persona-name").textContent = persona.problem ? `${persona.name} ⚠` : persona.name;
  personaBtn.title = persona.problem ? `This chat's persona can't be loaded: ${persona.problem}. Answers use the default persona.` : "Switch persona";
  personaNames[persona.key] = persona.name;
}

async function openPersonaMenu() {
  let list;
  try {
    list = await api("/personas");
  } catch (err) {
    alert(err.message);
    return;
  }
  for (const p of list) if (!p.problem) personaNames[p.key] = p.name;
  const manage = document.createElement("button");
  manage.type = "button";
  manage.className = "persona-option persona-manage";
  manage.textContent = "Manage personas…";
  manage.addEventListener("click", () => { personaMenu.hidden = true; openPersonaEditor(); });
  personaMenu.replaceChildren(...list.filter((p) => !p.problem).map((p) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "persona-option" + (p.current ? " current" : "");
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", p.current);
    const top = document.createElement("span");
    top.className = "top";
    top.textContent = p.name;
    const role = document.createElement("span");
    role.className = "role";
    role.textContent = p.role;
    option.append(top, role);
    option.addEventListener("click", () => choosePersona(p));
    return option;
  }), manage);
  personaMenu.hidden = false;
}

export async function choosePersona(p) {
  personaMenu.hidden = true;
  if (p.current || busy) return false;
  try {
    showPersona(await api("/persona", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: p.key }),
    }));
    loadSessions();
    return true;
  } catch (err) {
    alert(err.message);
    return false;
  }
}

export function initPersona() {
  personaBtn.addEventListener("click", () => {
    if (personaMenu.hidden) openPersonaMenu();
    else personaMenu.hidden = true;
  });

  document.addEventListener("click", (e) => {
    if (!personaMenu.hidden && !e.composedPath().includes(personaBtn.parentElement)) personaMenu.hidden = true;
  });
}
