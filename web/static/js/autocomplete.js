import { api } from "./api.js";
import { input } from "./dom.js";
import { personaNames } from "./persona.js";
import { COMMANDS, PROMPT_VERBS, PERMISSION_VERBS, CACHE_VERBS, GUARDRAIL_VERBS, SCHEMA_VERBS } from "./commands.js";

const cmdMenu = document.getElementById("cmd-menu");
let cmdItems = [];
let cmdActive = 0;
let schemaNames = null;

async function argumentSuggestions(command, partial) {
  if (command === "/persona") {
    return Object.entries(personaNames)
      .filter(([key]) => key.startsWith(partial))
      .map(([key, name]) => ({ insert: `/persona ${key}`, name: key, desc: name }));
  }
  if (command === "/json") {
    if (schemaNames === null) {
      try { schemaNames = await api("/schemas"); } catch (e) { schemaNames = []; }
    }
    return schemaNames.filter((n) => n.startsWith(partial)).map((n) => ({ insert: `/json ${n} `, name: n, desc: "schema" }));
  }
  if (command === "/prompt") {
    return PROMPT_VERBS.filter(([verb]) => verb.startsWith(partial))
      .map(([verb, desc, more]) => ({ insert: `/prompt ${verb}${more ? " " : ""}`, name: verb, desc }));
  }
  if (command === "/permissions") {
    return PERMISSION_VERBS.filter(([verb]) => verb.startsWith(partial))
      .map(([verb, desc]) => ({ insert: `/permissions ${verb} `, name: verb, desc }));
  }
  if (command === "/cache") {
    return CACHE_VERBS.filter(([verb]) => verb.startsWith(partial))
      .map(([verb, desc, more]) => ({ insert: `/cache ${verb}${more ? " " : ""}`, name: verb, desc }));
  }
  if (command === "/schemas") {
    return SCHEMA_VERBS.filter(([verb]) => verb.startsWith(partial))
      .map(([verb, desc]) => ({ insert: `/schemas ${verb} `, name: verb, desc }));
  }
  if (command === "/guardrails") {
    return GUARDRAIL_VERBS.filter(([verb]) => verb.startsWith(partial))
      .map(([verb, desc, more]) => ({ insert: `/guardrails ${verb}${more ? " " : ""}`, name: verb, desc }));
  }
  if (command === "/branches" && "switch".startsWith(partial)) {
    return [{ insert: "/branches switch ", name: "switch", desc: "Go to another branch" }];
  }
  return [];
}

export async function updateCmdMenu() {
  const text = input.value;
  if (!text.startsWith("/") || text.includes("\n")) return hideCmdMenu();
  const space = text.indexOf(" ");
  let items;
  if (space === -1) {
    items = COMMANDS.filter((c) => c.name.startsWith(text))
      .map((c) => ({ insert: c.args ? `${c.name} ` : c.name, name: c.name, args: c.args, desc: c.desc }));
  } else {
    const partial = text.slice(space + 1);
    items = partial.includes(" ") ? [] : await argumentSuggestions(text.slice(0, space), partial);
    if (input.value !== text) return; // typed on while the schemas were loading
  }
  if (items.length === 0) return hideCmdMenu();
  cmdItems = items;
  cmdActive = 0;
  cmdMenu.replaceChildren(...items.map((item, i) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "cmd-item" + (i === 0 ? " active" : "");
    el.setAttribute("role", "option");
    el.append(
      Object.assign(document.createElement("span"), { className: "cmd-name", textContent: item.name }),
      Object.assign(document.createElement("span"), { className: "cmd-args", textContent: item.args || "" }),
      Object.assign(document.createElement("span"), { className: "cmd-desc", textContent: item.desc }),
    );
    // keep the focus in the textarea
    el.addEventListener("mousedown", (e) => e.preventDefault());
    el.addEventListener("click", () => pickCommand(i));
    return el;
  }));
  cmdMenu.hidden = false;
}

export function hideCmdMenu() {
  cmdMenu.hidden = true;
  cmdItems = [];
}

function moveCmdActive(step) {
  cmdActive = (cmdActive + step + cmdItems.length) % cmdItems.length;
  [...cmdMenu.children].forEach((el, i) => el.classList.toggle("active", i === cmdActive));
  cmdMenu.children[cmdActive].scrollIntoView({ block: "nearest" });
}

function pickCommand(i) {
  input.value = cmdItems[i].insert;
  input.focus();
  updateCmdMenu();
}

export function initAutocomplete() {
  input.addEventListener("input", updateCmdMenu);
  input.addEventListener("blur", () => setTimeout(hideCmdMenu, 100));
  input.addEventListener("keydown", (e) => {
    if (cmdMenu.hidden || e.isComposing) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      moveCmdActive(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && cmdItems[cmdActive].insert !== input.value.trim())) {
      e.preventDefault();
      e.stopImmediatePropagation(); // do not also send the message
      pickCommand(cmdActive);
    } else if (e.key === "Escape") {
      hideCmdMenu();
    }
  }, true);
}
