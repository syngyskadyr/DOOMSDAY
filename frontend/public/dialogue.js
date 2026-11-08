import { api } from "./api.js";
import { requestAppeal } from "./appeal-forms.js";
import { appealHref } from "./appeal-model.js";
import { checklistHref } from "./checklists.js";
import {
  answerLabel, dateLabel, dialogueHref, isPending, languageLabel, messagesInRange, numberLabel,
  roleLabel, scoreLabel, statuses,
} from "./model.js";
import { appealTimeline, assessmentExplanation } from "./result-notes.js";
import { evaluationGate } from "./readiness.js";
import {
  badge, button, channelTag, emptyState, errorNotice, field, flagBadge, h, icon, intro,
  linkButton, notice, panelHeading, select, statusBadge,
} from "./ui.js";

function audioSource(url) {
  if (!url) return null;
  try {
    const source = new URL(url, location.origin);
    if (["http:", "https:"].includes(source.protocol)) return source.href;
  } catch {
    // A malformed recording URL must not prevent reading the transcript.
  }
  return null;
}

function offsetLabel(seconds) {
  const minutes = Math.floor(seconds / 60);
  return minutes + ":" + String(Math.floor(seconds % 60)).padStart(2, "0");
}

function transcriptView(dialogue, ctx) {
  const source = audioSource(dialogue.audio_url);
  const audioStatus = h("div", { role: "status", class: "audio-status" });
  const audio = source && h("audio", {
    src: source, controls: true, preload: "none", "aria-label": "Запись звонка",
    onerror: () => { audioStatus.textContent = "Запись недоступна. Проверьте доступ к файлу или срок действия ссылки."; },
  });
  let requestedOffset = null;
  if (audio) {
    audio.addEventListener("loadedmetadata", () => {
      if (requestedOffset != null) {
        audio.currentTime = requestedOffset;
        requestedOffset = null;
      }
    });
    ctx.signal.addEventListener("abort", () => audio.pause(), { once: true });
  }
  function seek(seconds) {
    requestedOffset = seconds;
    if (audio.readyState > 0) {
      audio.currentTime = seconds;
      requestedOffset = null;
    }
    audio.focus();
    audio.play().catch(() => {
      audioStatus.textContent = "Не удалось начать воспроизведение. Попробуйте открыть запись в плеере.";
    });
  }

  const elements = new Map();
  const list = h("ol", { class: "transcript", tabindex: "0", "aria-label": "Реплики диалога" },
    dialogue.messages.map((message) => {
      const role = message.speaker.toLowerCase();
      const tone = ["operator", "client", "bot", "system"].includes(role) ? role : "other";
      const item = h("li", { class: "message " + tone, tabindex: "-1" },
        h("div", { class: "message-head" },
          h("span", { class: "message-role" }, roleLabel(message.speaker)),
          h("span", { class: "message-time" }, message.timestamp),
          h("span", { class: "message-id" }, message.message_id)),
        h("p", { class: "message-text", dir: "auto" }, message.text),
        audio && Number.isFinite(message.audio_offset_seconds) &&
          button("Запись с " + offsetLabel(message.audio_offset_seconds), {
            glyph: "play", kind: "quiet small", onclick: () => seek(message.audio_offset_seconds),
          }));
      elements.set(message.message_id, item);
      return item;
    }));
  const marker = h("div", { class: "highlight-status", role: "status", hidden: true });
  let highlighted = [];
  function clearHighlight() {
    for (const item of highlighted) item.classList.remove("highlighted");
    highlighted = [];
    marker.hidden = true;
  }
  function jump(start, end = start) {
    clearHighlight();
    const range = messagesInRange(dialogue.messages, start, end);
    if (!range.length) return;
    highlighted = range.map(({ message_id }) => elements.get(message_id));
    for (const item of highlighted) item.classList.add("highlighted");
    marker.replaceChildren(h("span", {}, start === end ? "Доказательство " + start : "Проверенный участок " + start + " — " + end),
      button("Снять выделение", { kind: "quiet small", onclick: clearHighlight }));
    marker.hidden = false;
    highlighted[0].scrollIntoView({ block: "nearest" });
    highlighted[0].focus({ preventScroll: true });
  }
  return {
    jump,
    view: h("section", { class: "panel transcript-panel" },
      panelHeading("Транскрипт", dialogue.messages.length + " сообщ. · исходный порядок"),
      audio && h("div", { class: "audio-panel" },
        h("span", { class: "inline-icon field-label" }, icon("phone", 15), "Запись звонка"),
        audio, audioStatus),
      !audio && dialogue.channel.toLowerCase() === "voice call" &&
        h("p", { class: "audio-unavailable muted" }, dialogue.audio_url ? "Ссылка на запись недоступна для воспроизведения." : "К диалогу не приложена запись."),
      marker, list),
  };
}

function resultBadge(row, criterion) {
  let tone = "success";
  if (row.answer == null) tone = "warning";
  else if (row.answer === "na") tone = "neutral";
  else if (criterion.kind === "flag") tone = row.answer === "yes" ? "danger" : "success";
  else if (row.answer === "no" || row.answer === 0) tone = "danger";
  else if (criterion.kind === "scale" && row.answer < criterion.scale_max) tone = "info";
  return badge(answerLabel(row.answer, criterion), tone);
}

function criterionCard(criterion, row, original, points, originalPoints, jump, action) {
  const weight = points?.base_weight ?? criterion.weight;
  const effective = points?.effective_weight;
  return h("article", { class: "criterion-result" },
    h("div", { class: "criterion-result-head" },
      h("div", { class: "criterion-name" }, h("span", { class: "criterion-id" }, criterion.id), h("h4", {}, criterion.title)),
      resultBadge(row, criterion)),
    h("div", { class: "criterion-metrics muted" },
      criterion.kind === "flag" ? h("span", {}, "Отдельный флаг, без веса") : [
        h("span", {}, "Вес " + numberLabel(weight)),
        effective != null && effective !== weight && h("span", {}, "Применённый вес " + numberLabel(effective)),
        h("span", {}, "Баллы " + numberLabel(points?.earned_points)),
      ]),
    assessmentExplanation(row, jump),
    original && h("details", { class: "original-result" },
      h("summary", {}, "Исходная оценка · " + answerLabel(original.answer, criterion) +
        (originalPoints?.earned_points != null ? " · баллы " + numberLabel(originalPoints.earned_points) : "")),
      assessmentExplanation(original, jump)),
    h("div", { class: "criterion-actions" }, action));
}

function resultPanel(ctx, evaluation, jump) {
  const { checklist_snapshot: checklist, final, original } = evaluation;
  const rows = new Map(final.assessment.criteria.map((row) => [row.criterion_id, row]));
  const points = new Map(final.calculation.rows.map((row) => [row.criterion_id, row]));
  const originals = new Map(original.assessment.criteria.map((row) => [row.criterion_id, row]));
  const originalPoints = new Map(original.calculation.rows.map((row) => [row.criterion_id, row]));
  const sections = new Map(final.calculation.sections.map((row) => [row.section_id, row]));
  const changed = new Set(evaluation.appeals.filter((item) => item.resolution?.decision === "change").map((item) => item.criterion_id));
  const open = new Map(evaluation.appeals.filter((item) => item.status === "open").map((item) => [item.criterion_id, item.appeal_id]));
  const appealAction = (criterion, row) => open.has(criterion.id)
    ? linkButton("Апелляция на рассмотрении", appealHref({ id: open.get(criterion.id), status: "open" }), { kind: "quiet small" })
    : button("Подать апелляцию", { kind: "quiet small", glyph: "appeals",
      onclick: () => requestAppeal(ctx, evaluation, criterion, row) });
  return h("section", { class: "panel assessment-panel" },
    panelHeading("Результат оценки", checklist.title + " · версия " + evaluation.checklist_version, statusBadge(evaluation.status)),
    h("p", { class: "muted result-provenance" },
      "Модель: " + (evaluation.metadata?.model || "не указана сервером") + " · " + dateLabel(evaluation.created_at) +
      " · критериев: " + checklist.criteria.length),
    h("div", { class: "assessment-summary" },
      h("div", {}, h("span", { class: "field-label" }, "Итоговый балл"), h("strong", { class: "total-score" }, scoreLabel(final.calculation.total_score))),
      h("div", {}, h("span", { class: "field-label" }, "Грубая ошибка"), flagBadge(final.critical_error)),
      changed.size > 0 && h("div", {}, h("span", { class: "field-label" }, "Исходный балл"), h("strong", {}, scoreLabel(original.calculation.total_score)))),
    final.calculation.no_applicable_criteria &&
      notice("У оценки нет применимых критериев с весом.", "info"),
    evaluation.status === "needs_review" &&
      notice("Некоторые критерии требуют ручной проверки. Смотрите причину в соответствующей строке."),
    h("div", { class: "result-groups" }, checklist.sections.map((section) => {
      const criteria = checklist.criteria.filter(({ section_id }) => section_id === section.id);
      if (!criteria.length) return null;
      const totals = sections.get(section.id);
      return h("section", { class: "result-group" },
        h("div", { class: "result-group-head" }, h("h3", {}, section.title),
          totals?.possible_points > 0 && h("span", { class: "muted" },
            numberLabel(totals.earned_points) + " / " + numberLabel(totals.possible_points) + " баллов")),
        criteria.map((criterion) => criterionCard(criterion, rows.get(criterion.id),
          changed.has(criterion.id) ? originals.get(criterion.id) : null, points.get(criterion.id),
          originalPoints.get(criterion.id), jump, appealAction(criterion, rows.get(criterion.id)))));
    })),
    evaluation.appeals.length > 0 && h("section", { class: "appeal-history dialogue-history" },
      h("h3", {}, "История апелляций"), appealTimeline(evaluation)));
}

export async function dialogueView(ctx) {
  const { id, query, signal } = ctx;
  const [dialogueResult, versionsResult] = await Promise.allSettled([
    api.conversation(id, signal), api.checklists(signal),
  ]);
  if (dialogueResult.status === "rejected") throw dialogueResult.reason;
  const dialogue = dialogueResult.value;
  const versions = versionsResult.status === "fulfilled" ? versionsResult.value : { items: [], current_version: "" };
  const selectedEvaluation = query.get("evaluation") || dialogue.latest_evaluation_id;
  let evaluation = null;
  let evaluationError = null;
  async function loadEvaluation() {
    const result = await api.evaluation(selectedEvaluation, signal);
    if (result.conversation_id !== id) throw new Error("Эта оценка относится к другому диалогу.");
    return result;
  }
  if (selectedEvaluation) {
    try { evaluation = await loadEvaluation(); } catch (error) { evaluationError = error; }
  }
  const transcript = transcriptView(dialogue, ctx);
  const jobPanel = h("div", { class: "view job-panel" });
  const jobStatus = h("p", { class: "sr-only", role: "status" });
  const launchMessages = h("div");
  const readinessMessage = h("div", { class: "model-readiness-note", role: "status" });
  const version = select(versions.items.map((item) => [item.version, "Версия " + item.version +
    (item.version === versions.current_version ? " · текущая" : "")]), versions.current_version);
  const launch = button("Оценить", { kind: "primary", glyph: "bolt", type: "submit" });
  let submitting = false;
  let polling = false;
  let pollTimer;
  let displayedStatus;

  function updateLaunch() {
    const active = dialogue.evaluation_history.some((item) => {
      const entry = item.evaluation_id === evaluation?.evaluation_id ? evaluation : item;
      return entry.checklist_version === version.value && isPending(entry.status);
    });
    const gate = evaluationGate(ctx.readiness.api, ctx.readiness.model, { submitting, active });
    launch.disabled = gate.disabled || !version.value;
    launch.title = gate.reason;
    launch.replaceChildren(icon("bolt", 16), submitting ? "Запуск оценки…" : "Оценить");
    readinessMessage.replaceChildren(notice(gate.reason, gate.disabled && !submitting && !active ? "warning" : "info"));
  }
  ctx.readiness.subscribe(updateLaunch);
  version.addEventListener("change", updateLaunch);
  signal.addEventListener("abort", () => clearTimeout(pollTimer), { once: true });

  async function poll() {
    if (signal.aborted || polling) return;
    polling = true;
    try {
      evaluation = await loadEvaluation();
      evaluationError = null;
      if (!signal.aborted) showResult();
    } catch (error) {
      if (signal.aborted) return;
      displayedStatus = undefined;
      jobPanel.replaceChildren(errorNotice(error), button("Проверить статус", { glyph: "refresh", onclick: poll }));
    } finally {
      polling = false;
    }
  }

  function showResult() {
    updateLaunch();
    const status = evaluation?.status ?? "not_evaluated";
    if (status === displayedStatus && isPending(status)) {
      pollTimer = setTimeout(poll, 1200);
      return;
    }
    displayedStatus = status;
    if (evaluation) jobStatus.textContent = statuses[status]?.label ?? "Неизвестный статус";
    if (evaluationError) {
      jobPanel.replaceChildren(h("section", { class: "panel failure-panel" },
        panelHeading("Не удалось загрузить оценку", "Транскрипт доступен. Повторная загрузка не запускает модель."),
        errorNotice(evaluationError), button("Загрузить оценку", { onclick: poll })));
    } else if (!evaluation) {
      jobPanel.replaceChildren(h("section", { class: "panel" },
        emptyState("Оценок пока нет", "Выберите версию чек-листа и нажмите «Оценить». Здесь появятся баллы, объяснения и доказательства.", null, "checklist")));
    } else if (isPending(evaluation.status)) {
      jobPanel.replaceChildren(h("section", { class: "panel job-state" },
        h("span", { class: "spinner" }), h("div", {},
          h("h2", {}, statuses[evaluation.status].label),
          h("p", { class: "muted" }, "Чек-лист версии " + evaluation.checklist_version + ". Статус обновляется автоматически."))));
      pollTimer = setTimeout(poll, 1200);
    } else if (evaluation.status === "failed") {
      jobPanel.replaceChildren(h("section", { class: "panel failure-panel" },
        panelHeading("Оценка завершилась ошибкой", "Чек-лист версии " + evaluation.checklist_version),
        errorNotice(evaluation.error || { message: "Оценщик не вернул результат." })));
    } else if (evaluation.final) {
      try {
        jobPanel.replaceChildren(resultPanel(ctx, evaluation, transcript.jump));
      } catch {
        jobPanel.replaceChildren(notice("Сервер вернул неполную оценку. Транскрипт доступен; обновите данные или проверьте результат на сервере.", "danger"));
      }
    } else {
      jobPanel.replaceChildren(notice("Результат оценки отсутствует в ответе сервера. Обновите данные или проверьте состояние на сервере.", "warning"));
    }
  }

  const startForm = h("form", {
    class: "evaluate-form",
    onsubmit: async (event) => {
      event.preventDefault();
      if (launch.disabled) return;
      submitting = true;
      updateLaunch();
      launchMessages.replaceChildren();
      try {
        const job = await api.startEvaluation(id, version.value);
        if (!signal.aborted) location.hash = dialogueHref(id, { evaluation: job.evaluation_id });
      } catch (error) {
        submitting = false;
        if (!signal.aborted) {
          updateLaunch();
          launchMessages.replaceChildren(errorNotice(error));
          if (error.status === 409) launchMessages.append(button("Обновить диалог", { glyph: "refresh", onclick: () => ctx.refresh() }));
        }
      }
    },
  }, field("Версия чек-листа", version), launch);

  const history = select(dialogue.evaluation_history.map((entry, index) => [
    entry.evaluation_id, dateLabel(entry.created_at) + " · v" + entry.checklist_version + (index === 0 ? " · последняя" : ""),
  ]), selectedEvaluation, {
    onchange: (event) => { location.hash = dialogueHref(id, { evaluation: event.target.value }); },
  });
  if (query.get("message")) ctx.onMount(() => transcript.jump(query.get("message"), query.get("end") || query.get("message")));
  showResult();
  return h("div", { class: "view" },
    linkButton("К реестру диалогов", "#/dialogues", { glyph: "back", kind: "quiet back-link" }),
    intro(dialogue.conversation_id, "Карточка диалога и история оценок", { eyebrow: "РАЗБОР ДИАЛОГА" }),
    h("div", { class: "dialogue-meta" }, channelTag(dialogue.channel),
      badge(languageLabel(dialogue.language)), dialogue.synthetic && badge("Тестовый диалог", "info")),
    h("div", { class: "dialogue-grid" }, transcript.view,
      h("div", { class: "view assessment-column" },
        h("section", { class: "panel launch-panel" },
          panelHeading("Оценивание", "Выберите версию правил для этого диалога",
            linkButton("К чек-листам", checklistHref({ dialogue: id }), { kind: "quiet small", glyph: "checklist" })),
          startForm, readinessMessage,
          versionsResult.status === "rejected" && errorNotice(versionsResult.reason),
          launchMessages, dialogue.evaluation_history.length > 0 && field("История оценок", history)),
        jobStatus, jobPanel)));
}
