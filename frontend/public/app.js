import { api } from "./api.js";
import { appealsView } from "./appeals.js";
import { checklistView } from "./checklists.js";
import { dialogueView } from "./dialogue.js";
import { analyticsView, dashboardView, registryView } from "./overview.js";
import { apiAvailable, modelPresentation } from "./readiness.js";
import { badge, button, emptyState, errorNotice, h, icon, linkButton, loading, notice } from "./ui.js";

const routes = {
  dashboard: { label: "Обзор", glyph: "activity", view: dashboardView },
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
const connection = h("div", { class: "connection", role: "status", "aria-label": "Доступность API и модели" });
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
  h("a", { class: "brand", href: "#/dashboard" }, h("img", { src: "./mark.svg", alt: "", width: "40", height: "40" }),
    h("span", {}, h("strong", {}, "DOOMSDAY"), h("small", {}, "КОНТРОЛЬ КАЧЕСТВА"))),
  h("p", { class: "nav-label" }, "РАБОЧАЯ ОБЛАСТЬ"),
  h("nav", { "aria-label": "Основная навигация" }, [...navLinks.values()]),
  h("div", { class: "sidebar-footer" }, h("span", { class: "footer-mark" }, icon("activity", 17)),
    h("div", {}, h("strong", {}, "DOOMSDAY"), h("small", {}, "Рабочее пространство"))));

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
  if (!path || path === "/") return { key: "dashboard", query: new URLSearchParams(search) };
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
  let timer;
  const listeners = new Set();
  const readiness = { api: null, model: null, subscribe: (callback) => listeners.add(callback) };
  signal.addEventListener("abort", () => { clearTimeout(timer); listeners.clear(); }, { once: true });
  connection.replaceChildren(badge("API: проверка…", "neutral", true), badge("Модель: проверка…", "neutral", true));
  async function checkReadiness() {
    const [apiResult, modelResult] = await Promise.allSettled([api.health(signal), api.modelHealth(signal)]);
    if (signal.aborted) return;
    readiness.api = apiResult.status === "fulfilled" ? apiResult.value : null;
    readiness.model = modelResult.status === "fulfilled" ? modelResult.value : null;
    const model = modelPresentation(readiness.model);
    const modelBadge = badge(model.label, model.tone, true);
    modelBadge.title = (readiness.model?.message || "Готовность модели неизвестна.") +
      (readiness.model?.checked_at ? " Проверено: " + new Date(readiness.model.checked_at).toLocaleTimeString("ru-RU") + "." : "");
    const apiReady = apiAvailable(readiness.api);
    connection.replaceChildren(badge(apiReady ? "API: доступен" : "API: недоступен", apiReady ? "success" : "danger", true), modelBadge);
    for (const callback of listeners) callback();
    timer = setTimeout(checkReadiness, 15000);
  }
  const health = checkReadiness();
  const onMount = [];
  const ctx = {
    ...route, signal, health, readiness, announce,
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
