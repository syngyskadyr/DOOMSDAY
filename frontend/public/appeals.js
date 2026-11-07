import { api } from "./api.js";
import { resolveAppeal } from "./appeal-forms.js";
import { appealHref } from "./appeal-model.js";
import { answerLabel, dateLabel, dialogueHref, numberLabel, scoreLabel } from "./model.js";
import { appealStatusBadge, appealTimeline, assessmentExplanation } from "./result-notes.js";
import {
  badge, button, emptyState, errorNotice, h, intro, linkButton, panelHeading,
} from "./ui.js";

const statusLabels = { open: "На рассмотрении", resolved: "Рассмотренные", all: "Все" };

function appealList(items, status, selectedId) {
  const tabs = h("nav", { class: "tabs", "aria-label": "Статус апелляции" },
    Object.entries(statusLabels).map(([key, label]) => h("a", {
      href: appealHref({ status: key }), "aria-current": key === status ? "page" : null,
    }, label)));
  return h("section", { class: "panel appeals-list" },
    panelHeading("Очередь апелляций", "Обращений: " + items.length), tabs,
    items.length ? h("ul", { class: "appeal-queue" }, items.map((appeal) => h("li", {},
      h("a", {
        href: appealHref({ status, id: appeal.appeal_id }),
        "aria-current": appeal.appeal_id === selectedId ? "true" : null,
      }, h("div", { class: "timeline-heading" }, h("strong", {}, "Критерий " + appeal.criterion_id), appealStatusBadge(appeal.status)),
      h("p", { class: "queue-comment" }, appeal.comment), h("p", { class: "muted note" }, dateLabel(appeal.created_at))))))
      : emptyState("Здесь пока нет апелляций", "Обращения подаются из результата оценки по конкретному критерию.",
        linkButton("К диалогам", "#/dialogues"), "appeals"));
}

function comparedResult(title, result, criterion, jump) {
  const row = result.assessment.criteria.find(({ criterion_id }) => criterion_id === criterion.id);
  const points = result.calculation.rows.find(({ criterion_id }) => criterion_id === criterion.id);
  return h("article", { class: "compared-result" },
    h("h3", {}, title), badge(answerLabel(row.answer, criterion), "info"),
    criterion.kind !== "flag" && h("p", { class: "muted note" }, "Баллы критерия: " + numberLabel(points?.earned_points)),
    h("p", { class: "muted note" }, "Общий результат: " + scoreLabel(result.calculation.total_score)),
    assessmentExplanation(row, jump));
}

async function appealDetails(ctx, appeal) {
  const evaluation = await api.evaluation(appeal.evaluation_id, ctx.signal);
  const criterion = evaluation.checklist_snapshot.criteria.find(({ id }) => id === appeal.criterion_id);
  const row = evaluation.final.assessment.criteria.find(({ criterion_id }) => criterion_id === criterion.id);
  const target = (start, end) => dialogueHref(evaluation.conversation_id, { evaluation: evaluation.evaluation_id, start, end });
  const jump = (start, end) => { location.hash = target(start, end); };
  const feedback = h("div", { class: "form-feedback" });
  const examine = button("Рассмотреть", { glyph: "appeals", kind: "primary", onclick: async () => {
    examine.disabled = true;
    feedback.replaceChildren();
    try {
      const dialogue = await api.conversation(evaluation.conversation_id, ctx.signal);
      if (!ctx.signal.aborted) resolveAppeal(ctx, appeal, evaluation, dialogue, criterion, row);
    } catch (error) {
      if (!ctx.signal.aborted) feedback.replaceChildren(errorNotice(error));
    } finally {
      examine.disabled = false;
    }
  } });
  const view = h("section", { class: "panel appeal-detail", "aria-label": "Выбранная апелляция" },
    panelHeading(criterion.title, evaluation.conversation_id + " · чек-лист v" + evaluation.checklist_version,
      [appealStatusBadge(appeal.status), evaluation.synthetic && badge("Тестовый диалог", "info")]),
    h("div", { class: "appeal-detail-body" },
      h("div", { class: "appeal-request" },
        h("h3", {}, "Комментарий к обращению"), h("p", { class: "reason" }, appeal.comment),
        h("p", { class: "muted note" }, dateLabel(appeal.created_at))),
      h("div", { class: "actions" }, linkButton("Открыть диалог", target(), { glyph: "arrow" }), appeal.status === "open" && examine),
      feedback,
      h("div", { class: "compare-grid" },
        comparedResult("Исходная оценка", evaluation.original, criterion, jump),
        comparedResult("Итоговая оценка", evaluation.final, criterion, jump)),
      h("section", { class: "appeal-history" }, h("h3", {}, "История критерия"), appealTimeline(evaluation, criterion.id))));
  if (ctx.query.get("id")) ctx.onMount(() => {
    if (matchMedia("(max-width: 760px)").matches) {
      view.scrollIntoView({ block: "start" });
      view.tabIndex = -1;
      view.focus({ preventScroll: true });
    }
  });
  return view;
}

export async function appealsView(ctx) {
  const id = ctx.query.get("id");
  const requested = ctx.query.get("status");
  const status = Object.hasOwn(statusLabels, requested) ? requested : id ? "all" : "open";
  const { items } = await api.appeals(status, ctx.signal);
  const selected = id ? items.find((appeal) => appeal.appeal_id === id) : items[0];
  let details;
  if (selected) {
    try {
      details = await appealDetails(ctx, selected);
    } catch (error) {
      details = h("section", { class: "panel page-error" }, h("h2", {}, "Апелляция недоступна"), errorNotice(error),
        h("div", { class: "actions" }, button("Повторить", { glyph: "refresh", onclick: () => ctx.refresh() })));
    }
  } else if (id) {
    details = h("section", { class: "panel" }, emptyState("Апелляция не найдена в этой выборке",
      "Возможно, её статус изменился.", linkButton("Показать все", appealHref({ status: "all" })), "appeals"));
  }
  return h("div", { class: "view" },
    intro("Апелляции", "Пересматривайте спорные критерии и сохраняйте решения с объяснением.", { hero: true }),
    h("div", { class: "appeals-grid" }, appealList(items, status, selected?.appeal_id), details));
}
