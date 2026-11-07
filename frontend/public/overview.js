import { allConversations, api } from "./api.js";
import {
  channelLabel, dialogueHref, emptyFilters, filterConversations, languageLabel, latestStatus,
  numberLabel, scoreLabel, statuses, summarize,
} from "./model.js";
import {
  badge, button, channelTag, emptyState, errorNotice, field, flagBadge, h, icon, intro,
  panelHeading, openDialog, scoreChip, select, statCard, statusBadge,
} from "./ui.js";

const PAGE_SIZE = 12;
const registry = { filters: emptyFilters(), page: 0 };
const analyticsFilters = emptyFilters();

function filterControls(items, filters, onChange, analytics = false) {
  const showSource = analytics || items.some((item) => item.synthetic);
  if (!showSource) filters.source = "";
  const values = (key, label) => [...new Set(items.map((item) => item[key]))]
    .sort((left, right) => label(left).localeCompare(label(right), "ru"))
    .map((value) => [value, label(value)]);
  const options = [
    ["language", "Язык", values("language", languageLabel)],
    ["channel", "Канал", values("channel", channelLabel)],
    ...(!analytics ? [["status", "Статус оценки", Object.entries(statuses).map(([key, { label }]) => [key, label])]] : []),
    ["critical", "Грубая ошибка", [["true", "Выявлена"], ["false", "Не выявлена"], ["unknown", "Нет данных"]]],
    ...(showSource ? [["source", "Данные", [["live", "Без тестовых"], ["test", "Только тестовые"]]]] : []),
  ];
  const controls = options.map(([key, label, choices]) => {
    const control = select([["", key === "critical" ? "Любой результат" : "Все"], ...choices], filters[key], {
      onchange: (event) => {
        filters[key] = event.target.value;
        onChange();
      },
    });
    if (!control.value) filters[key] = "";
    return [key, field(label, control)];
  });
  const search = h("input", {
    type: "search", placeholder: "Введите ID диалога", value: filters.search,
    oninput: (event) => {
      filters.search = event.target.value;
      onChange();
    },
  });
  const reset = () => {
    Object.assign(filters, emptyFilters());
    search.value = "";
    for (const [, label] of controls) label.querySelector("select").value = "";
    onChange();
  };
  const view = h("div", { class: "filters" + (analytics ? " analytics-filters" : showSource ? " with-source" : "") },
    !analytics && field("Поиск по ID", h("div", { class: "search-field" }, icon("search", 17), search)),
    controls.map(([, label]) => label), button("Сбросить", { kind: "quiet reset", onclick: reset }));
  return { view, reset };
}

function importDialog(ctx) {
  const modal = openDialog("Импорт диалогов", { signal: ctx.signal });
  const file = h("input", { type: "file", accept: ".json,application/json", required: true });
  const messages = h("div");
  const submit = button("Импортировать", { kind: "primary", type: "submit", glyph: "import" });
  modal.body.append(h("form", {
    class: "form-stack",
    onsubmit: async (event) => {
      event.preventDefault();
      submit.disabled = true;
      messages.replaceChildren();
      try {
        const result = await api.importConversations(await file.files[0].text());
        if (ctx.signal.aborted) return;
        modal.close();
        Object.assign(registry.filters, emptyFilters());
        registry.page = 0;
        ctx.refresh("Импортировано: " + result.imported + ". Уже были в реестре: " + result.skipped + ".");
      } catch (error) {
        messages.replaceChildren(errorNotice(error));
      } finally {
        submit.disabled = false;
      }
    },
  },
  h("p", { class: "muted" }, "Выберите JSON-выгрузку организаторов. Уже загруженные диалоги с тем же содержимым будут пропущены."),
  field("JSON-файл", file), messages,
  h("div", { class: "form-actions" }, button("Отмена", { onclick: modal.close }), submit)));
  modal.show();
}

function conversationTable(items) {
  const titles = ["Диалог", "Канал", "Язык", "Статус оценки", "Балл", "Грубая ошибка", "Открыть"];
  const cell = (index, ...contents) => h("td", { role: "cell" },
    index < 6 && h("span", { class: "mobile-label", "aria-hidden": "true" }, titles[index]), contents);
  return h("div", { class: "table-scroll", tabindex: "0", role: "region", "aria-label": "Реестр диалогов" },
    h("table", { class: "registry-table", role: "table" },
      h("thead", { role: "rowgroup" }, h("tr", { role: "row" }, titles.map((title, index) =>
        h("th", { scope: "col", role: "columnheader" }, index === titles.length - 1 ? h("span", { class: "sr-only" }, title) : title)))),
      h("tbody", { role: "rowgroup" }, items.map((item) => {
        const href = dialogueHref(item.conversation_id);
        return h("tr", { role: "row" },
          h("th", { scope: "row", role: "rowheader" }, h("a", { class: "row-link", href }, item.conversation_id),
            h("div", { class: "row-meta" }, item.message_count + " сообщ.",
              item.synthetic && badge("Тестовый", "info"), item.has_open_appeals && badge("Апелляция", "warning"))),
          cell(1, channelTag(item.channel)),
          cell(2, h("span", { title: languageLabel(item.language), class: "language-code" }, item.language)),
          cell(3, statusBadge(latestStatus(item))),
          cell(4, scoreChip(item.latest_evaluation?.total_score)),
          cell(5, flagBadge(item.latest_evaluation?.critical_error)),
          cell(6, h("a", { href, class: "row-arrow", "aria-label": "Открыть диалог " + item.conversation_id }, icon("chevron", 17))));
      }))));
}

export async function registryView(ctx) {
  const items = await allConversations(ctx.signal);
  const summary = summarize(items);
  const openImport = () => importDialog(ctx);
  const body = h("div");
  const pagination = h("div", { class: "pagination" });
  const count = h("span", { class: "muted", role: "status" });
  const { view: filters, reset } = filterControls(items, registry.filters, () => {
    registry.page = 0;
    renderRows();
  });

  function renderRows(focusPage) {
    const matching = filterConversations(items, registry.filters);
    const pages = Math.max(1, Math.ceil(matching.length / PAGE_SIZE));
    registry.page = Math.min(registry.page, pages - 1);
    const start = registry.page * PAGE_SIZE;
    count.textContent = "Найдено: " + matching.length;
    if (!matching.length) {
      body.replaceChildren(items.length
        ? emptyState("Диалоги не найдены", "Измените запрос или сбросьте фильтры.", button("Сбросить фильтры", { onclick: reset }))
        : emptyState("Диалогов пока нет", "Загрузите JSON-выгрузку, чтобы открыть разговоры и запустить оценку.",
          button("Импортировать JSON", { kind: "primary", glyph: "import", onclick: openImport })));
      pagination.hidden = true;
      return;
    }
    const table = conversationTable(matching.slice(start, start + PAGE_SIZE));
    body.replaceChildren(table);
    pagination.hidden = false;
    const pageButton = (label, glyph, step) => button(label, {
      glyph, disabled: step < 0 ? registry.page === 0 : registry.page === pages - 1,
      onclick: () => { registry.page += step; renderRows(step); },
    });
    const previous = pageButton("Назад", "left", -1);
    const next = pageButton("Далее", "chevron", 1);
    pagination.replaceChildren(
      h("span", { class: "muted" }, (start + 1) + "–" + Math.min(start + PAGE_SIZE, matching.length) + " из " + matching.length),
      h("div", { class: "actions" }, previous,
        h("span", { class: "page-number", "aria-label": "Страница " + (registry.page + 1) + " из " + pages },
          (registry.page + 1) + " / " + pages), next));
    if (focusPage) {
      table.focus({ preventScroll: true });
      table.scrollIntoView({ block: "start" });
    }
  }

  const content = h("div", { class: "view" },
    intro("Диалоги операторов", "Открывайте разговоры, проверяйте оценки и переходите к репликам, на которых основан вывод.", {
      hero: true, actions: button("Импорт JSON", { glyph: "import", kind: "primary", onclick: openImport }),
    }),
    h("div", { class: "stat-grid" },
      statCard("Всего диалогов", numberLabel(summary.total), "Загружено в реестр", "conversations", "info"),
      statCard("Обработано", numberLabel(summary.processed), "Есть результат оценки", "check"),
      statCard("Требуют проверки", numberLabel(summary.counts.needs_review), "Нужно решение проверяющего", "clock", "warning"),
      statCard("Грубые ошибки", numberLabel(summary.critical), "Подтверждённый флаг", "warning", "danger")),
    h("section", { class: "panel registry" },
      panelHeading("Реестр диалогов", "Чаты и звонки службы поддержки", count), filters, body, pagination));
  renderRows();
  return content;
}

const scoreIntervals = ["0–<20", "20–<40", "40–<60", "60–<80", "80–100"];

function bars(entries, maximum) {
  return h("ul", { class: "chart-bars" }, entries.map(([label, value]) => h("li", {},
    h("span", { class: "bar-label" }, label),
    h("span", { class: "bar-track", "aria-hidden": "true" },
      h("span", { class: "bar-fill", style: "width:" + (100 * value / Math.max(maximum, 1)) + "%" })),
    h("strong", {}, numberLabel(value)))));
}

export async function analyticsView(ctx) {
  const items = await allConversations(ctx.signal);
  const results = h("div", { class: "view" });
  const { view: filters } = filterControls(items, analyticsFilters, render, true);

  function render() {
    const selected = filterConversations(items, analyticsFilters);
    const summary = summarize(selected);
    const openCritical = () => {
      Object.assign(registry.filters, emptyFilters(), {
        critical: "true", language: analyticsFilters.language, channel: analyticsFilters.channel, source: analyticsFilters.source,
      });
      registry.page = 0;
      location.hash = "#/dialogues";
    };
    results.replaceChildren(
      h("div", { class: "stat-grid" },
        statCard("В выборке", numberLabel(summary.total), "Диалоги после фильтров", "conversations", "info"),
        statCard("Обработано", numberLabel(summary.processed), "Есть результат оценки", "check"),
        statCard("Средний балл", scoreLabel(summary.average), "С числовым итогом: " + summary.scored, "activity"),
        statCard("Грубые ошибки", numberLabel(summary.critical), "Подтверждённый флаг", "warning", "danger")),
      h("div", { class: "analytics-grid" },
        h("section", { class: "panel chart-panel" },
          panelHeading("Распределение баллов", "Только оценки с числовым итогом"),
          summary.scored
            ? bars(scoreIntervals.map((label, index) => [label, summary.bins[index]]), Math.max(...summary.bins))
            : emptyState("Нет числовых результатов", "Здесь появятся баллы завершённых оценок.", null, "analytics")),
        h("section", { class: "panel chart-panel" },
          panelHeading("Статусы диалогов", "Последняя оценка каждого разговора"),
          bars(Object.entries(statuses).map(([key, { label }]) => [label, summary.counts[key]]),
            Math.max(...Object.values(summary.counts))))),
      h("section", { class: "panel analytics-foot" }, panelHeading("Грубые ошибки",
        "Последняя оценка с подтверждённым флагом, независимо от числового балла.",
        button("Показать диалоги", { glyph: "arrow", onclick: openCritical, disabled: !summary.critical }))),
      summary.synthetic > 0 && h("p", { class: "muted note" },
        "В выборке есть тестовые диалоги (" + summary.synthetic + "). Их можно исключить фильтром «Данные»."));
  }

  render();
  return h("div", { class: "view" },
    intro("Аналитика качества", "Сравнивайте баллы и находите диалоги, которые требуют внимания.", { hero: true }),
    h("section", { class: "panel" }, panelHeading("Выборка", "Показатели рассчитаны по последним оценкам."), filters), results);
}
