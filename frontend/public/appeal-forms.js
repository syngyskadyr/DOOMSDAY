import { api } from "./api.js";
import { appealHref, replacementPayload } from "./appeal-model.js";
import { answerLabel, roleLabel, violationLabel } from "./model.js";
import {
  badge, button, errorNotice, field, h, notice, openDialog, select, textarea,
} from "./ui.js";

function formFeedback(messages, error, refresh) {
  messages.replaceChildren(errorNotice(error));
  if (error.status === 409) {
    messages.append(button("Загрузить актуальную оценку", { glyph: "refresh", onclick: () => refresh() }));
  }
  messages.focus();
}

export function requestAppeal(ctx, evaluation, criterion, row) {
  const modal = openDialog("Подать апелляцию", { signal: ctx.signal });
  const comment = textarea("", { required: true, rows: 5, placeholder: "Укажите, с чем вы не согласны, и почему." });
  const feedback = h("div", { class: "form-feedback", tabindex: "-1" });
  const submit = button("Отправить апелляцию", { kind: "primary", type: "submit" });
  modal.body.append(h("form", {
    class: "form-stack",
    onsubmit: async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      submit.disabled = true;
      feedback.replaceChildren();
      try {
        if (!comment.value.trim()) throw new Error("Введите комментарий к апелляции.");
        const created = await api.createAppeal(evaluation.evaluation_id, {
          criterion_id: criterion.id, comment: comment.value.trim(), expected_revision: evaluation.revision,
        });
        if (!ctx.signal.aborted) {
          modal.close();
          ctx.announce("Апелляция отправлена на рассмотрение.");
          location.hash = appealHref({ id: created.appeal_id, status: "open" });
        }
      } catch (error) {
        if (modal.dialog.open) formFeedback(feedback, error, ctx.refresh);
      } finally {
        submit.disabled = false;
      }
    },
  },
  h("div", { class: "form-stack" }, h("h3", {}, criterion.title),
    h("div", { class: "actions" }, h("span", { class: "muted" }, "Текущий ответ"), badge(answerLabel(row.answer, criterion))),
    h("p", { class: "reason" }, row.reason)),
  field("Комментарий", comment), feedback,
  h("div", { class: "form-actions" }, button("Отмена", { onclick: modal.close }), submit)));
  modal.show();
  comment.focus();
}

function evidencePicker(title, messages, selected) {
  const summary = h("summary");
  function updateLabel() {
    summary.textContent = title + (selected.size ? " · " + selected.size : "");
  }
  updateLabel();
  return h("details", { class: "evidence-picker" }, summary,
    h("div", { class: "evidence-options" }, messages.map((message) =>
      h("label", { class: "evidence-option" },
        h("input", { type: "checkbox", checked: selected.has(message.message_id), onchange: (event) => {
          if (event.target.checked) selected.add(message.message_id);
          else selected.delete(message.message_id);
          updateLabel();
        } }),
        h("span", {}, h("strong", {}, message.message_id + " · " + roleLabel(message.speaker) + " · " + message.timestamp),
          h("span", { class: "evidence-preview", dir: "auto" }, message.text))))));
}

function editText(draft, key) {
  return textarea(draft[key], { rows: 2, oninput: (event) => { draft[key] = event.target.value; } });
}

function violationEditor(criterion, draft, messages) {
  const episodes = h("div", { class: "form-stack" });
  const add = button("Добавить эпизод", { glyph: "plus", kind: "secondary small", onclick: () => {
    draft.violations.push({ type: criterion.flag_types[0], reason: "", evidence: new Set() });
    render();
    episodes.querySelector(".violation-editor:last-child select")?.focus();
  } });
  function render() {
    episodes.replaceChildren(...draft.violations.map((episode, index) => h("div", { class: "field-box violation-editor" },
      h("div", { class: "editor-heading" }, h("h4", {}, "Эпизод " + (index + 1)),
        button("Убрать эпизод " + (index + 1), { glyph: "close", kind: "icon-button", onclick: () => {
          draft.violations.splice(index, 1);
          render();
          add.focus();
        } })),
      field("Тип нарушения", select(criterion.flag_types.map((type) => [type, violationLabel(type)]),
        episode.type, { onchange: (event) => { episode.type = event.target.value; } })),
      field("Описание эпизода", editText(episode, "reason")),
      evidencePicker("Реплики эпизода", messages, episode.evidence))));
  }
  render();
  return h("div", { class: "form-stack" }, h("strong", {}, "Эпизоды грубой ошибки"), episodes, add);
}

function replacementEditor(criterion, row, messages) {
  const draft = {
    answer: row.answer, reason: row.reason, naReason: row.na_reason ?? "", uncertainty: row.uncertainty ?? "",
    evidence: new Set(row.evidence_message_ids),
    start: row.checked_range?.start_message_id ?? "", end: row.checked_range?.end_message_id ?? "",
    violations: row.violations.map((violation) => ({
      type: violation.type, reason: violation.reason, evidence: new Set(violation.evidence_message_ids),
    })),
  };
  const choices = criterion.allowed_answers.map((answer, index) => [String(index), answerLabel(answer, criterion)]);
  const dynamic = h("div", { class: "form-stack" });
  const messageChoices = [["", "Не выбрано"], ...messages.map((message) =>
    [message.message_id, message.message_id + " · " + roleLabel(message.speaker) + " · " + message.timestamp])];
  function renderAnswerFields() {
    dynamic.replaceChildren();
    if (draft.answer === "na") dynamic.append(field("Причина N/A", editText(draft, "naReason"), criterion.na_rule));
    if (draft.answer === null) dynamic.append(field("Чего не хватает для оценки", editText(draft, "uncertainty")));
    if (criterion.kind === "flag" && (draft.answer === "yes" || draft.answer === null)) {
      dynamic.append(violationEditor(criterion, draft, messages));
    }
  }
  const answer = select([...choices, ["review", "Требует проверки"]],
    draft.answer === null ? "review" : String(criterion.allowed_answers.indexOf(draft.answer)),
    { onchange: (event) => {
      draft.answer = event.target.value === "review" ? null : criterion.allowed_answers[Number(event.target.value)];
      renderAnswerFields();
    } });
  const rangeSelect = (key) => select(messageChoices, draft[key], {
    onchange: (event) => { draft[key] = event.target.value; },
  });
  renderAnswerFields();
  return {
    view: h("div", { class: "field-box" },
      h("h3", {}, "Итоговая оценка критерия"),
      field("Ответ", answer),
      field("Объяснение итоговой оценки", editText(draft, "reason")),
      dynamic,
      evidencePicker("Доказательства", messages, draft.evidence),
      h("div", { class: "form-columns" },
        field("Начало проверенного участка", rangeSelect("start")),
        field("Конец проверенного участка", rangeSelect("end"))),
      h("p", { class: "muted note" }, "Если действие пропущено, укажите участок, где оно должно было произойти.")),
    payload: () => replacementPayload(criterion, draft, messages),
  };
}

export function resolveAppeal(ctx, appeal, evaluation, dialogue, criterion, row) {
  const modal = openDialog("Рассмотреть апелляцию", { signal: ctx.signal, wide: true });
  const feedback = h("div", { class: "form-feedback", tabindex: "-1" });
  const reason = textarea("", { rows: 3, required: true, placeholder: "Объясните решение по апелляции." });
  const change = replacementEditor(criterion, row, dialogue.messages);
  change.view.hidden = true;
  const decision = select([["confirm", "Подтвердить оценку"], ["change", "Изменить оценку"]], "confirm",
    { onchange: (event) => { change.view.hidden = event.target.value !== "change"; } });
  const submit = button("Сохранить решение", { kind: "primary", glyph: "check", type: "submit" });
  modal.body.append(h("form", {
    class: "form-stack",
    onsubmit: async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      submit.disabled = true;
      feedback.replaceChildren();
      try {
        const reviewReason = reason.value.trim();
        if (!reviewReason) throw new Error("Введите причину решения.");
        await api.resolveAppeal(appeal.appeal_id, {
          decision: decision.value, reason: reviewReason,
          replacement: decision.value === "change" ? change.payload() : null,
          expected_revision: evaluation.revision,
        });
        if (ctx.signal.aborted) return;
        modal.close();
        const href = appealHref({ id: appeal.appeal_id, status: "all" });
        if (location.hash === href) ctx.refresh("Решение сохранено.");
        else {
          ctx.announce("Решение сохранено.");
          location.hash = href;
        }
      } catch (error) {
        if (modal.dialog.open) formFeedback(feedback, error, ctx.refresh);
      } finally {
        submit.disabled = false;
      }
    },
  }, notice(criterion.title + " · текущий ответ: " + answerLabel(row.answer, criterion), "info"),
  field("Решение проверяющего", decision), field("Причина решения", reason), change.view, feedback,
  h("div", { class: "form-actions" }, button("Отмена", { onclick: modal.close }), submit)));
  modal.show();
}
