import { api } from "./api.js";
import { appealsView } from "./appeals.js";
import { checklistView } from "./checklists.js";
import { dialogueView } from "./dialogue.js";
import { analyticsView, registryView } from "./overview.js";
import { badge, button, emptyState, errorNotice, h, icon, linkButton, loading, notice } from "./ui.js";

const routes = {
  dialogues: { label: "Диалоги", glyph: "conversations", view: registryView },
  checklists: { label: "Чек-листы", glyph: "checklist", view: checklistView },
  appeals: { label: "Апелляции", glyph: "appeals", view: appealsView },
  analytics: { label: "Аналитика", glyph: "analytics", view: analyticsView },
};

const navLinks = new Map(Object.entries(routes).map(([key, { label, glyph }]) => [
  key, h("a", { class: "nav-link", href: "#/" + key }, icon(glyph, 19), h("span", {}, label)),
]));
const main = h("main", { id: "content", tabindex: "-1" });
const sectionName = h("span", { class: "crumb-current" });
const connection = h("div", { class: "connection", role: "status" }, badge("Подключение…", "neutral", true));
const flash = h("div", { class: "flash" });
let active;
let flashTimeout;

function announce(message) {
  if (!message) return;
  clearTimeout(flashTimeout);
  flash.replaceChildren(notice(message, "success"));
  flashTimeout = setTimeout(() => flash.replaceChildren(), 6500);
}

const sidebar = h("aside", { class: "sidebar" },
  h("a", { class: "brand", href: "#/dialogues" }, h("img", { src: "./mark.svg", alt: "", width: "40", height: "40" }),
    h("span", {}, h("strong", {}, "DOOMSDAY"), h("small", {}, "QUALITY WORKSPACE"))),
  h("p", { class: "nav-label" }, "РАБОЧАЯ ОБЛАСТЬ"),
  h("nav", { "aria-label": "Основная навигация" }, [...navLinks.values()]),
  h("div", { class: "sidebar-footer" }, h("span", { class: "footer-mark" }, icon("activity", 17)),
    h("div", {}, h("strong", {}, "CQA Hackathon"), h("small", {}, "Freedom Broker"))));

document.getElementById("app").append(h("div", { class: "app-shell" }, sidebar,
  h("div", { class: "workspace" },
    h("header", { class: "topbar" },
      h("div", { class: "topbar-inner" },
        h("div", { class: "breadcrumbs" }, h("span", { class: "crumb-root" }, "Рабочая область"), icon("chevron", 14), sectionName),
        h("div", { class: "actions" }, connection, button("Обновить", { glyph: "refresh", onclick: () => render() })))),
    main, flash)));

document.querySelector(".skip-link").addEventListener("click", (event) => {
  event.preventDefault();
  main.focus();
  main.scrollIntoView({ block: "start" });
});

function routeFor(hash) {
  const [path, search] = hash.slice(1).split("?", 2);
  if (!path || path === "/") return { key: "dialogues", query: new URLSearchParams(search) };
  const [, key, encodedId, ...extra] = path.split("/");
  if (!path.startsWith("/") || !Object.hasOwn(routes, key) || extra.length) return {};
  const query = new URLSearchParams(search);
  if (!encodedId) return { key, query };
  if (key === "dialogues") {
    try { return { key, id: decodeURIComponent(encodedId), query }; } catch { return {}; }
  }
  return {};
}

async function render({ navigate = false } = {}) {
  active?.abort();
  active = new AbortController();
  const { signal } = active;
  const route = routeFor(location.hash);
  const { key, id } = route;
  const title = id ? "Карточка диалога" : routes[key]?.label ?? "Страница не найдена";
  sectionName.textContent = title;
  document.title = title + " · DOOMSDAY";
  for (const [name, link] of navLinks) {
    if (name === key) link.setAttribute("aria-current", id ? "location" : "page");
    else link.removeAttribute("aria-current");
  }
  if (navigate) window.scrollTo(0, 0);
  main.replaceChildren(loading());
  const health = api.health(signal).then((status) => {
    if (!signal.aborted) connection.replaceChildren(badge("API подключён", "success", true));
    return status;
  }).catch(() => {
    if (!signal.aborted) connection.replaceChildren(badge("API недоступен", "danger", true));
    return null;
  });
  const onMount = [];
  const ctx = {
    ...route, signal, health, announce,
    onMount: (callback) => onMount.push(callback),
    refresh: (message) => { announce(message); render(); },
  };
  try {
    const content = id ? await dialogueView(ctx) : key ? await routes[key].view(ctx) :
      h("section", { class: "panel" }, emptyState("Страница не найдена", "Откройте один из разделов рабочей области.",
        linkButton("К диалогам", "#/dialogues", { kind: "primary" })));
    if (signal.aborted) return;
    main.replaceChildren(content);
    if (navigate) (main.querySelector("h1") ?? main).focus({ preventScroll: true });
    for (const callback of onMount) callback();
  } catch (error) {
    if (signal.aborted) return;
    main.replaceChildren(h("section", { class: "panel page-error" }, h("h1", {}, "Не удалось загрузить данные"),
      errorNotice(error), h("div", { class: "actions" }, button("Повторить", { glyph: "refresh", onclick: () => render() }),
        id && linkButton("К диалогам", "#/dialogues"))));
  }
}

window.addEventListener("hashchange", () => render({ navigate: true }));
render();
