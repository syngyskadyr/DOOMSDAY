export const statuses = {
  not_evaluated: { label: "Не оценён", tone: "neutral" },
  queued: { label: "В очереди", tone: "info" },
  running: { label: "Оценивается", tone: "info" },
  completed: { label: "Оценён", tone: "success" },
  needs_review: { label: "Требует проверки", tone: "warning" },
  failed: { label: "Ошибка оценки", tone: "danger" },
};

export const isPending = (status) => status === "queued" || status === "running";
export const isScored = (value) => Number.isFinite(value);
export const latestStatus = (item) => item.latest_evaluation?.status ?? "not_evaluated";

const channels = new Map([["agent chat", "Чат"], ["voice call", "Звонок"]]);
const languages = new Map([["RU", "Русский"], ["KZ", "Казахский"], ["KK", "Казахский"], ["EN", "Английский"]]);
const roles = new Map([["client", "Клиент"], ["operator", "Оператор"], ["bot", "Бот"], ["system", "Система"]]);
export const channelLabel = (value) => channels.get(value?.toLowerCase()) ?? value;
export const languageLabel = (value) => languages.get(value?.toUpperCase()) ?? value;
export const roleLabel = (value) => roles.get(value?.toLowerCase()) ?? (value || "Участник");

export const kindLabels = { binary: "Да / Нет", scale: "Шкала", flag: "Флаг нарушения" };
const violationLabels = new Map([
  ["insult", "Оскорбление"],
  ["off_company_paid_service", "Платная услуга в обход компании"],
  ["confidential_disclosure", "Раскрытие конфиденциальной информации"],
]);
export const violationLabel = (value) => violationLabels.get(value) ?? value;

const numberFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
export const numberLabel = (value) => isScored(value) ? numberFormatter.format(value) : "—";
export const scoreLabel = (value) => isScored(value) ? numberLabel(value) + " / 100" : "—";
const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});
export function dateLabel(value) {
  const date = new Date(value);
  return value && !Number.isNaN(+date) ? dateFormatter.format(date) : (value || "—");
}

export function answerLabel(answer, criterion) {
  if (answer === null || answer === undefined) return "Требует проверки";
  if (answer === "na") return "N/A";
  if (answer === "yes") return criterion?.kind === "flag" ? "Выявлена" : "Да";
  if (answer === "no") return criterion?.kind === "flag" ? "Не выявлена" : "Нет";
  return numberLabel(answer) + (criterion?.scale_max ? " / " + numberLabel(criterion.scale_max) : "");
}

export const emptyFilters = () => ({ search: "", language: "", channel: "", status: "", critical: "", source: "" });

export function filterConversations(items, filters) {
  const query = (filters.search ?? "").trim().toLowerCase();
  return items.filter((item) => {
    const flag = item.latest_evaluation?.critical_error;
    const critical = flag == null ? "unknown" : String(flag);
    return (!query || item.conversation_id.toLowerCase().includes(query))
      && (!filters.language || item.language === filters.language)
      && (!filters.channel || item.channel === filters.channel)
      && (!filters.status || latestStatus(item) === filters.status)
      && (!filters.critical || critical === filters.critical)
      && (!filters.source || (item.synthetic ? "test" : "live") === filters.source);
  });
}

export function summarize(items) {
  const counts = Object.fromEntries(Object.keys(statuses).map((status) => [status, 0]));
  const bins = [0, 0, 0, 0, 0];
  let critical = 0;
  let synthetic = 0;
  let scored = 0;
  let sum = 0;
  for (const item of items) {
    const evaluation = item.latest_evaluation;
    counts[latestStatus(item)] += 1;
    if (evaluation?.critical_error === true) critical += 1;
    if (item.synthetic) synthetic += 1;
    if (isScored(evaluation?.total_score)) {
      const score = evaluation.total_score;
      bins[Math.max(0, Math.min(4, Math.floor(score / 20)))] += 1;
      sum += score;
      scored += 1;
    }
  }
  return {
    total: items.length,
    processed: counts.completed + counts.needs_review,
    counts, bins, critical, synthetic, scored,
    average: scored ? sum / scored : null,
  };
}

export function messagesInRange(messages, startId, endId) {
  const start = messages.findIndex((message) => message.message_id === startId);
  const end = messages.findIndex((message) => message.message_id === endId);
  return start >= 0 && end >= start ? messages.slice(start, end + 1) : [];
}

export function dialogueHref(id, { evaluation, start, end } = {}) {
  const query = new URLSearchParams();
  if (evaluation) query.set("evaluation", evaluation);
  if (start) query.set("message", start);
  if (end) query.set("end", end);
  return "#/dialogues/" + encodeURIComponent(id) + (query.size ? "?" + query : "");
}
