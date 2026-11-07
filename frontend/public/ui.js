import { channelLabel, numberLabel, statuses } from "./model.js";

const icons = {
  conversations: '<path d="M21 11.5a7.5 7.5 0 0 1-7.5 7.5H8l-5 3v-6a7.5 7.5 0 0 1 6.5-12h4a7.5 7.5 0 0 1 7.5 7.5Z"/><path d="M8 10h8m-8 4h5"/>',
  checklist: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2m-6 7 2 2 4-4m-6 8h6"/>',
  appeals: '<path d="M12 3v18M8 21h8M4 7l8-3 8 3M4 7l-3 7h6L4 7Zm16 0-3 7h6l-3-7Z"/>',
  analytics: '<path d="M4 4v16h17M8 15v-3m4 3V7m4 8v-5m4 5V5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2 6m2-13v7h-7"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/>',
  phone: '<path d="M7 3 4 5c-3 3 2 12 7 15 4 2 6 2 8 0l2-3-5-3-2 2c-2-1-5-4-6-6l2-2-3-5Z"/>',
  import: '<path d="M12 3v12m-4-4 4 4 4-4M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  back: '<path d="M19 12H5m6 6-6-6 6-6"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  left: '<path d="m15 6-6 6 6 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  warning: '<path d="M11 4 2 19a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1L13 4a1 1 0 0 0-2 0Z"/><path d="M12 9v4m0 3h.01"/>',
  activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 10-12h-8l1-8Z"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  quote: '<path d="M9 11H5c0-2 1-4 3-5m11 5h-4c0-2 1-4 3-5M4 11h5v6H4zm10 0h5v6h-5z"/>',
  play: '<path d="m8 5 12 7-12 7V5Z"/>',
};

export function icon(name, size = 18) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = icons[name];
  return svg;
}

export function h(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name.startsWith("on")) element.addEventListener(name.slice(2), value);
    else if (value !== false && value != null) element.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== false && child != null) element.append(child);
  }
  return element;
}

export function button(label, { glyph, kind = "secondary", type = "button", onclick, disabled } = {}) {
  return h("button", { class: "button " + kind, type, disabled, ...(onclick && { onclick }) },
    glyph && icon(glyph, 16), label);
}

export function linkButton(label, href, { glyph, kind = "secondary" } = {}) {
  return h("a", { class: "button " + kind, href }, glyph && icon(glyph, 16), label);
}

let fieldCount = 0;

export function field(label, input, hint) {
  const control = input.matches("input, select, textarea") ? input : input.querySelector("input, select, textarea");
  const id = control.id || "field-" + (++fieldCount);
  control.id = id;
  if (hint) control.setAttribute("aria-describedby", id + "-hint");
  return h("div", { class: "field" },
    h("label", { class: "field-label", for: id }, label), input,
    hint && h("small", { id: id + "-hint", class: "muted" }, hint));
}

export function select(choices, value, attrs = {}) {
  const control = h("select", attrs, choices.map(([key, label]) => h("option", { value: key }, label)));
  control.value = value;
  return control;
}

export function textarea(value = "", attrs = {}) {
  const control = h("textarea", { rows: 3, ...attrs });
  control.value = value ?? "";
  return control;
}

export function badge(text, tone = "neutral", dot = false) {
  return h("span", { class: "badge " + tone }, dot && h("span", { class: "dot" }), text);
}

export function statusBadge(status) {
  const { label, tone } = statuses[status] ?? { label: status, tone: "neutral" };
  return badge(label, tone, true);
}

export function flagBadge(flag) {
  if (flag === true) return badge("Выявлена", "danger", true);
  if (flag === false) return badge("Не выявлена", "success", true);
  return badge("Нет данных");
}

export function scoreChip(score) {
  return h("span", { class: "score-chip" }, numberLabel(score),
    Number.isFinite(score) && h("small", {}, "/ 100"));
}

export function channelTag(channel) {
  return h("span", { class: "inline-icon channel-tag" },
    icon(channel.toLowerCase() === "voice call" ? "phone" : "conversations", 15), channelLabel(channel));
}

export function intro(title, description, { eyebrow = "КОНТРОЛЬ КАЧЕСТВА", actions, hero = false } = {}) {
  return h("section", { class: "intro" + (hero ? " hero" : "") },
    hero && h("div", { class: "hero-pattern", "aria-hidden": "true" }),
    h("div", { class: "intro-copy" },
      h("p", { class: "eyebrow" }, hero && h("span", { class: "dot" }), eyebrow),
      h("h1", { tabindex: "-1" }, title), h("p", { class: "intro-description" }, description)),
    actions && h("div", { class: "actions intro-actions" }, actions));
}

export function panelHeading(title, detail, actions) {
  return h("div", { class: "panel-heading" },
    h("div", {}, h("h2", {}, title), detail && h("p", { class: "muted" }, detail)),
    actions && h("div", { class: "actions" }, actions));
}

export function statCard(label, value, caption, glyph, tone = "success") {
  return h("article", { class: "stat-card" },
    h("div", { class: "stat-top" }, h("h2", {}, label), h("span", { class: "stat-icon " + tone }, icon(glyph, 17))),
    h("strong", { class: "stat-number" }, value), h("p", {}, caption));
}

export function emptyState(title, detail, action, glyph = "conversations") {
  return h("div", { class: "empty-state" }, h("div", { class: "empty-icon" }, icon(glyph, 23)),
    h("h3", {}, title), h("p", {}, detail), action);
}

export function notice(message, tone = "warning", role = "status") {
  return h("div", { class: "notice " + tone, role },
    icon(tone === "success" ? "check" : tone === "info" ? "activity" : "warning", 18),
    h("div", {}, message));
}

export function errorNotice(error) {
  const messages = Array.isArray(error.details)
    ? error.details.filter((item) => item?.message).map((item) => item.message)
    : [];
  return notice([h("p", {}, error.message || "Не удалось выполнить действие."),
    messages.length > 0 && h("ul", {}, messages.map((message) => h("li", {}, message)))], "danger", "alert");
}

export function loading(label = "Загрузка данных…") {
  return h("div", { class: "loading", role: "status" }, h("span", { class: "spinner" }), label);
}

export function openDialog(title, { signal, wide = false } = {}) {
  const id = "dialog-title";
  const closeButton = button("Закрыть", { glyph: "close", kind: "icon-button" });
  const body = h("div", { class: "dialog-body" });
  const dialog = h("dialog", { class: "modal" + (wide ? " wide" : ""), "aria-labelledby": id },
    h("div", { class: "dialog-heading" }, h("h2", { id }, title), closeButton), body);
  const close = () => dialog.close();
  closeButton.addEventListener("click", close);
  dialog.addEventListener("close", () => {
    signal?.removeEventListener("abort", close);
    dialog.remove();
  }, { once: true });
  signal?.addEventListener("abort", close, { once: true });
  document.body.append(dialog);
  return { body, dialog, close, show: () => dialog.showModal() };
}
