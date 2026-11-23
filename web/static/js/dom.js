export const chat = document.getElementById("chat");
export const scroller = document.getElementById("scroller");
export const form = document.getElementById("form");
export const input = document.getElementById("input");
export const send = document.getElementById("send");
export const emptyEl = document.getElementById("empty");
export const sessionsEl = document.getElementById("sessions");

export function scrollDown() { scroller.scrollTop = scroller.scrollHeight; }

export function closeSidebar() { document.body.classList.remove("sidebar-open"); }

export function formatWhen(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { day: "numeric", month: "short" });
}
