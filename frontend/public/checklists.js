import { api } from "./api.js";
import { checklistPayload, newCriterion } from "./checklist-model.js";
import { dialogueHref, kindLabels, numberLabel } from "./model.js";
import {
  badge, button, errorNotice, field, h, icon, intro, linkButton, notice, panelHeading, select, textarea,
} from "./ui.js";

let working;
function warnUnsaved(event) {
  if (!working?.dirty) return;
  event.preventDefault();
  event.returnValue = "";
}

export function checklistHref({ version, dialogue } = {}) {
  const query = new URLSearchParams();
  if (version) query.set("version", version);
  if (dialogue) query.set("dialogue", dialogue);
  return "#/checklists" + (query.size ? "?" + query : "");
}

function editControl(draft, key, onEdit, { multiline = false, ...attrs } = {}) {
  const settings = {
    ...attrs, oninput: (event) => {
      draft[key] = event.target.value;
      onEdit();
    },
  };
  return multiline ? textarea(draft[key], settings) : h("input", { ...settings, value: draft[key] ?? "" });
}

function scaleEditor(criterion, onEdit, readOnly) {
  const levels = h("div", { class: "level-rows" });
  const add = button("Добавить ступень", { glyph: "plus", kind: "secondary small", onclick: () => {
    criterion.levels.push({ value: "", description: "" });
    onEdit();
    renderLevels();
    levels.querySelector(".level-row:last-child input")?.focus();
  } });
  function renderLevels() {
    levels.replaceChildren(...criterion.levels.map((level, index) => h("div", { class: "level-row" },
      field("Значение " + (index + 1), editControl(level, "value", onEdit, { type: "number", min: 0, step: "any" })),
      field("Описание ступени " + (index + 1), editControl(level, "description", onEdit)),
      !readOnly && button("Убрать ступень " + (index + 1), { glyph: "close", kind: "icon-button",
        onclick: () => {
          criterion.levels.splice(index, 1);
          onEdit();
          renderLevels();
          add.focus();
        },
      }))));
  }
  renderLevels();
  return h("div", { class: "field-box" },
    h("h4", {}, "Числовая шкала"),
    field("Максимум шкалы", editControl(criterion, "scale_max", onEdit, { type: "number", step: "any" })),
    levels, !readOnly && add);
}

function editorCard(criterion, checklist, { readOnly, onEdit, onRemove }) {
  const headline = h("span", { class: "editor-title" });
  const summaryMeta = h("span", { class: "editor-meta" });
  const conditional = h("div", { class: "form-stack" });
  const naSettings = h("div", { class: "form-stack" });

  function paintSummary() {
    headline.textContent = criterion.title.trim() || "Новый критерий";
    summaryMeta.replaceChildren(badge(kindLabels[criterion.kind]));
    if (criterion.kind !== "flag") {
      const weight = criterion.weight === "" ? "—" : numberLabel(Number(criterion.weight));
      summaryMeta.append(badge("Вес " + weight));
    }
    if (criterion.na_allowed) summaryMeta.append(badge("N/A", "info"));
  }

  function update() {
    paintSummary();
    onEdit();
  }

  function renderType() {
    conditional.replaceChildren();
    if (criterion.kind !== "flag") {
      conditional.append(field("Вес", editControl(criterion, "weight", update, { type: "number", min: 0, step: "any" }),
        "Неотрицательное число. Применённый вес рассчитывает сервер."));
    }
    if (criterion.kind === "scale") conditional.append(scaleEditor(criterion, update, readOnly));
    if (criterion.kind === "flag") {
      conditional.append(field("Типы нарушений", textarea(criterion.flag_types.join("\n"), {
        oninput: (event) => { criterion.flag_types = event.target.value.split(/\r?\n/); update(); },
      }), "По одному идентификатору на строку. Флаг не имеет веса."));
    }
  }

  function renderNa() {
    naSettings.replaceChildren();
    if (criterion.na_allowed) {
      naSettings.append(field("Правило N/A", editControl(criterion, "na_rule", update, { multiline: true }),
        "Опишите, когда этот критерий не применяется."));
    }
  }

  const kind = select(Object.entries(kindLabels), criterion.kind, { onchange: (event) => {
    criterion.kind = event.target.value;
    if (criterion.kind !== "flag" && criterion.weight == null) criterion.weight = 5;
    if (criterion.kind === "scale" && criterion.scale_max == null) {
      criterion.scale_max = 10;
      criterion.levels = [{ value: 0, description: "" }, { value: 10, description: "" }];
    }
    update();
    renderType();
  } });
  const section = select(checklist.sections.map(({ id, title }) => [id, title]), criterion.section_id,
    { onchange: (event) => { criterion.section_id = event.target.value; update(); } });
  const allowNa = h("label", { class: "checkbox-line" },
    h("input", { type: "checkbox", checked: criterion.na_allowed, onchange: (event) => {
      criterion.na_allowed = event.target.checked;
      update();
      renderNa();
    } }), "Разрешить N/A");
  paintSummary();
  renderType();
  renderNa();

  return h("details", { class: "panel editor-card" },
    h("summary", { class: "editor-summary" }, h("span", { class: "criterion-id" }, criterion.id), headline,
      summaryMeta, h("span", { class: "editor-chevron" }, icon("down", 17))),
    h("fieldset", { class: "editor-fields form-stack", disabled: readOnly },
      h("legend", { class: "sr-only" }, "Настройки критерия " + criterion.id),
      field("Название критерия", editControl(criterion, "title", update)),
      h("div", { class: "form-columns" }, field("Раздел", section), field("Тип ответа", kind)),
      field("Формулировка для оценщика", editControl(criterion, "instruction", update, { multiline: true })),
      conditional, allowNa, naSettings,
      onRemove && h("div", { class: "form-actions" }, button("Убрать новый критерий", { kind: "secondary", onclick: onRemove }))));
}

export async function checklistView(ctx) {
  const { query, signal } = ctx;
  const versions = await api.checklists(signal);
  const version = query.get("version") || versions.current_version;
  const returnTo = query.get("dialogue");
  const current = version === versions.current_version;
  const base = await api.checklist(version, signal);
  const originalIds = new Set(base.criteria.map(({ id }) => id));
  const session = current && working?.draft.version === version
    ? working : { draft: structuredClone(base), dirty: false };
  const { draft } = session;
  const cards = h("div", { class: "editor-cards" });
  const totals = h("div", { class: "actions" });
  const editStatus = h("span", { class: "muted note", role: "status" });
  const messages = h("div", { class: "editor-messages" });
  const elements = new Map();
  const save = button("Сохранить новую версию", { glyph: "check", kind: "primary", type: "submit" });
  const controls = h("fieldset", { class: "editor-controls" });
  let saving = false;
  const add = button("Добавить критерий", { glyph: "plus", onclick: () => {
    const criterion = newCriterion(draft);
    draft.criteria.push(criterion);
    const card = appendCard(criterion);
    card.open = true;
    card.querySelector("input").focus();
    onEdit();
  } });
  const versionSelect = select(versions.items.map((item) => [
    item.version, "Версия " + item.version + (item.version === versions.current_version ? " · текущая" : ""),
  ]), version, { onchange: (event) => {
    location.hash = checklistHref({ version: event.target.value, dialogue: returnTo });
  } });

  function updateTotals() {
    const weight = draft.criteria.reduce((sum, criterion) => {
      const value = Number(criterion.weight);
      return sum + (criterion.kind !== "flag" && Number.isFinite(value) ? value : 0);
    }, 0);
    totals.replaceChildren(badge("Критериев: " + draft.criteria.length, "success"),
      badge("Сумма весов: " + numberLabel(weight)));
    editStatus.textContent = session.dirty ? "Есть несохранённые изменения" : "";
    save.disabled = !session.dirty || saving;
  }

  function onEdit() {
    if (!session.dirty) {
      session.dirty = true;
      working = session;
      window.addEventListener("beforeunload", warnUnsaved);
    }
    updateTotals();
  }

  function appendCard(criterion) {
    const card = editorCard(criterion, draft, {
      readOnly: !current, onEdit,
      onRemove: !originalIds.has(criterion.id) && current ? () => {
        draft.criteria = draft.criteria.filter(({ id }) => id !== criterion.id);
        elements.delete(criterion.id);
        card.remove();
        add.focus();
        onEdit();
      } : null,
    });
    elements.set(criterion.id, card);
    cards.append(card);
    return card;
  }

  async function saveVersion(event) {
    event.preventDefault();
    if (!current || saving || !session.dirty) return;
    messages.replaceChildren();
    saving = true;
    controls.disabled = true;
    updateTotals();
    try {
      const saved = await api.saveChecklist(checklistPayload(draft));
      session.dirty = false;
      if (working === session) {
        working = null;
        window.removeEventListener("beforeunload", warnUnsaved);
      }
      if (!signal.aborted) {
        history.replaceState(null, "", checklistHref({ dialogue: returnTo }));
        ctx.refresh("Чек-лист сохранён. Версия " + saved.version + " доступна при запуске оценки.");
      }
    } catch (error) {
      if (!signal.aborted) {
        const card = elements.get(error.criterionId);
        if (card) card.open = true;
        messages.replaceChildren(errorNotice(error));
        if (error.status === 409) {
          messages.append(notice("Черновик остался открытым. Сверьте его с текущей версией в другой вкладке, прежде чем переносить правки.", "info"));
        }
        messages.focus();
      }
    } finally {
      saving = false;
      controls.disabled = false;
      updateTotals();
    }
  }

  messages.tabIndex = -1;
  for (const criterion of draft.criteria) appendCard(criterion);
  updateTotals();
  const toolbar = h("div", { class: "panel editor-toolbar" },
    h("div", { class: "editor-toolbar-main" },
      field("Версия чек-листа", versionSelect), totals, current && h("div", { class: "actions" }, add, save)),
    editStatus, messages);
  controls.append(toolbar, cards);
  return h("div", { class: "view" },
    intro("Чек-листы", "Настраивайте правила оценки и сохраняйте новые версии. Предыдущие оценки сохраняют свой снимок правил.", {
      hero: true, actions: returnTo && linkButton("К диалогу", dialogueHref(returnTo), { glyph: "back" }),
    }),
    h("section", { class: "panel" }, panelHeading(draft.title,
      current ? "Текущая версия " + version + " · изменения применятся к новым запускам" : "Архивная версия " + version,
      current ? badge("Активна", "success", true) :
        linkButton("Открыть текущую", checklistHref({ dialogue: returnTo }), { glyph: "arrow" }))),
    h("form", { class: "checklist-form", novalidate: true, onsubmit: saveVersion }, controls));
}
