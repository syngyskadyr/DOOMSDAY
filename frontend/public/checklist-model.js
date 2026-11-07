export class CriterionConfigError extends Error {
  constructor(criterion, message) {
    super("Критерий " + criterion.id + ". " + message);
    this.criterionId = criterion.id;
  }
}

export function newCriterion(checklist) {
  const used = new Set(checklist.criteria.map(({ id }) => id));
  let index = 1;
  while (used.has("custom-" + index)) index += 1;
  return {
    id: "custom-" + index, section_id: checklist.sections[0].id, title: "", kind: "binary",
    weight: 5, instruction: "", allowed_answers: ["yes", "no"], na_allowed: false,
    na_rule: null, scale_max: null, levels: [], flag_types: [],
  };
}

function numeric(value) {
  if (value == null || String(value).trim() === "") return NaN;
  return Number(value);
}

function normalizeCriterion(draft, sectionIds) {
  function require(condition, message) {
    if (!condition) throw new CriterionConfigError(draft, message);
  }
  const title = draft.title.trim();
  const instruction = draft.instruction.trim();
  const naRule = draft.na_allowed ? (draft.na_rule ?? "").trim() : null;
  require(title && instruction, "Заполните название и формулировку.");
  require(sectionIds.has(draft.section_id), "Выберите раздел.");
  require(!draft.na_allowed || naRule, "Опишите, когда допустим ответ N/A.");

  let allowed = ["yes", "no"];
  let levels = [];
  let scaleMax = null;
  const weight = draft.kind === "flag" ? null : numeric(draft.weight);
  require(weight === null || (Number.isFinite(weight) && weight >= 0), "Введите неотрицательный вес.");

  if (draft.kind === "scale") {
    scaleMax = numeric(draft.scale_max);
    require(Number.isFinite(scaleMax) && scaleMax > 0, "Максимум шкалы должен быть больше нуля.");
    levels = draft.levels.map((level) => ({ ...level, value: numeric(level.value), description: level.description.trim() }));
    require(levels.every((level) => level.description && Number.isFinite(level.value)
      && level.value >= 0 && level.value <= scaleMax), "Для каждой ступени укажите число от нуля до максимума и описание.");
    allowed = levels.map(({ value }) => value);
    require(new Set(allowed).size === allowed.length, "Ступени шкалы должны быть уникальными.");
    require(allowed.includes(scaleMax), "Добавьте ступень, равную максимуму шкалы.");
    allowed.sort((left, right) => left - right);
  }

  const types = draft.kind === "flag" ? draft.flag_types.map((type) => type.trim()).filter(Boolean) : [];
  require(draft.kind !== "flag" || (types.length > 0 && new Set(types).size === types.length),
    "Укажите хотя бы один тип нарушения. Типы не должны повторяться.");
  if (draft.na_allowed) allowed.push("na");
  return { ...draft, title, instruction, weight, allowed_answers: allowed, na_rule: naRule,
    scale_max: scaleMax, levels, flag_types: types };
}

export function checklistPayload(draft) {
  const sectionIds = new Set(draft.sections.map(({ id }) => id));
  return { ...draft, criteria: draft.criteria.map((criterion) => normalizeCriterion(criterion, sectionIds)) };
}
