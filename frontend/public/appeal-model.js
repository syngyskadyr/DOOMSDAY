import { messagesInRange } from "./model.js";

export function appealHref({ id, status } = {}) {
  const query = new URLSearchParams();
  if (status) query.set("status", status);
  if (id) query.set("id", id);
  return "#/appeals" + (query.size ? "?" + query : "");
}

export function replacementPayload(criterion, draft, messages) {
  const messageIds = new Set(messages.map((message) => message.message_id));
  function require(condition, message) {
    if (!condition) throw new Error(message);
  }
  const answer = draft.answer;
  const needsReview = answer === null;
  require(needsReview || criterion.allowed_answers.includes(answer), "Выберите допустимый ответ.");
  const reason = draft.reason.trim();
  require(reason, "Введите объяснение итоговой оценки.");
  const naReason = answer === "na" ? draft.naReason.trim() : null;
  require(answer !== "na" || naReason, "Укажите причину N/A.");
  const uncertainty = needsReview ? draft.uncertainty.trim() : null;
  require(!needsReview || uncertainty, "Укажите, чего не хватает для оценки.");

  const checkedRange = draft.start || draft.end
    ? { start_message_id: draft.start, end_message_id: draft.end }
    : null;
  require(!checkedRange || messagesInRange(messages, draft.start, draft.end).length > 0,
    "Укажите обе границы проверенного участка в порядке реплик.");
  require(criterion.kind !== "binary" || answer !== "no" || checkedRange,
    "Для ответа «Нет» укажите начало и конец проверенного участка.");

  const usesViolations = criterion.kind === "flag" && (answer === "yes" || needsReview);
  const violations = usesViolations ? draft.violations.map((violation) => ({
    type: violation.type, reason: violation.reason.trim(), evidence_message_ids: [...violation.evidence],
  })) : [];
  require(criterion.kind !== "flag" || answer !== "yes" || violations.length > 0, "Добавьте хотя бы один эпизод нарушения.");
  for (const violation of violations) {
    require(criterion.flag_types.includes(violation.type) && violation.reason && violation.evidence_message_ids.length,
      "Для каждого эпизода выберите тип, добавьте объяснение и отметьте подтверждающие реплики.");
  }
  const episodeIds = violations.flatMap((violation) => violation.evidence_message_ids);
  const evidence = draft.evidence.size ? [...draft.evidence] : [...new Set(episodeIds)];
  require([...evidence, ...episodeIds].every((id) => messageIds.has(id)), "Выберите доказательства из этого диалога.");
  require(needsReview || evidence.length || checkedRange, "Отметьте доказательства или укажите проверенный участок.");
  return {
    criterion_id: criterion.id, status: needsReview ? "needs_review" : "evaluated",
    answer, reason, evidence_message_ids: evidence, checked_range: checkedRange,
    na_reason: naReason, uncertainty, violations,
  };
}
