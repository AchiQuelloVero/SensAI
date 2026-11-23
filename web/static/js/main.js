import { api } from "./api.js";
import { initTheme } from "./theme.js";
import { initSettings } from "./settings.js";
import { initConfirm } from "./confirm.js";
import { initPersona, personaNames } from "./persona.js";
import { initPrompts } from "./prompts.js";
import { initJsonMode } from "./json-mode.js";
import { initStats } from "./stats.js";
import { initCache } from "./cache.js";
import { initSchemas } from "./schemas.js";
import { initPersonaEditor } from "./persona-editor.js";
import { initProfile } from "./profile.js";
import { initTools } from "./tools.js";
import { initKb } from "./kb.js";
import { initPermissions } from "./permissions.js";
import { initBranches } from "./branches.js";
import { initSessions, loadSessions } from "./sessions.js";
import { initChat, renderSession } from "./chat.js";
import { initAutocomplete } from "./autocomplete.js";

initTheme();
initSettings();
initConfirm();
initPersona();
initPrompts();
initJsonMode();
initStats();
initCache();
initSchemas();
initPersonaEditor();
initProfile();
initTools();
initKb();
initPermissions();
initBranches();
initSessions();
initChat();
// after initChat: the menu's Enter handler must be able to stop the composer's Enter from sending
initAutocomplete();

(async () => {
  try { renderSession(await api("/session")); } catch (e) {}
  try { for (const p of await api("/personas")) personaNames[p.key] = p.name; } catch (e) {}
  loadSessions();
})();
