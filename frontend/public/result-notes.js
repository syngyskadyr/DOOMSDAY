import { appealHref } from "./appeal-model.js";
import { answerLabel, dateLabel, violationLabel } from "./model.js";
import { badge, button, h, linkButton } from "./ui.js";

export function assessmentExplanation(row, jump) {
  const evidence = (ids) => ids.length > 0 && h("div", { class: "evidence-links" },
    ids.map((id) => button(id, { kind: "evidence", glyph: "quote", onclick: () => jump(id) })));
  const range = row.checked_range;
  return h("div", { class: "assessment-explanation" },
    h("p", { class: "reason" }, row.reason),
    row.na_reason && h("p", { class: "reason detail" }, h("strong", {}, "Почему N/A "), row.na_reason),
    row.uncertainty && h("p", { class: "reason detail" }, h("strong", {}, "Требует внимания "), row.uncertainty),
    evidence(row.evidence_message_ids),
    range && button("Участок " + range.start_message_id + " — " + range.end_message_id, {
      kind: "evidence", glyph: "layers", onclick: () => jump(range.start_message_id, range.end_message_id),
    }),
    row.violations.length > 0 && h("ul", { class: "violation-list" }, row.violations.map((violation) =>
      h("li", {}, h("strong", {}, violationLabel(violation.type)), h("p", {}, violation.reason), evidence(violation.evidence_message_ids)))));
}

export const appealStatusBadge = (status) => status === "open"
  ? badge("На рассмотрении", "warning", true)
  : badge("Рассмотрена", "success", true);

export function appealTimeline(evaluation, criterionId) {
  const appeals = evaluation.appeals.filter((appeal) => !criterionId || criterionId === appeal.criterion_id);
  return h("ol", { class: "appeal-timeline" }, appeals.map((appeal) => {
    const criterion = evaluation.checklist_snapshot.criteria.find(({ id }) => id === appeal.criterion_id);
    const resolution = appeal.resolution;
    return h("li", {},
      h("div", { class: "timeline-heading" }, h("strong", {}, criterion?.title ?? appeal.criterion_id),
        appealStatusBadge(appeal.status)),
      h("p", { class: "muted note" }, dateLabel(appeal.created_at) + " · ответ при подаче: " + answerLabel(appeal.before.answer, criterion)),
      h("p", { class: "reason" }, appeal.comment),
      resolution && h("div", { class: "timeline-resolution" },
        h("strong", {}, resolution.decision === "confirm" ? "Оценка подтверждена" :
          "Оценка изменена · " + answerLabel(resolution.replacement.answer, criterion)),
        h("p", { class: "reason" }, resolution.reason),
        h("p", { class: "muted note" }, dateLabel(resolution.resolved_at) + " · ревизия " + resolution.resulting_revision)),
      !criterionId && linkButton("Открыть апелляцию", appealHref({ id: appeal.appeal_id, status: "all" }), { kind: "quiet small", glyph: "arrow" }));
  }));
}
