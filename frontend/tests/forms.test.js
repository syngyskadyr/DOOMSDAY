import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { api } from "../public/api.js";
import { replacementPayload } from "../public/appeal-model.js";
import { checklistPayload, CriterionConfigError, newCriterion } from "../public/checklist-model.js";

const baseline = JSON.parse(readFileSync(new URL("../../codex_context/checklist.json", import.meta.url), "utf8"));
const messages = ["m001", "m002", "m003"].map((message_id) => ({ message_id }));
const criterion = (id) => baseline.criteria.find((item) => item.id === id);
const draftRow = (updates = {}) => ({
  answer: "yes", reason: "Объяснение", naReason: "", uncertainty: "", evidence: new Set(["m002"]),
  start: "", end: "", violations: [], ...updates,
});

test("the initial checklist round-trips and a custom scale stays numeric", () => {
  const draft = structuredClone(baseline);
  assert.deepEqual(checklistPayload(draft), baseline);
  const added = {
    ...newCriterion(draft), title: "Новый критерий", instruction: "Правило проверки",
    kind: "scale", weight: "2.5", scale_max: "10", na_allowed: true, na_rule: "Запрос отсутствует",
    levels: [{ value: "10", description: "Полностью" }, { value: "0", description: "Не выполнено" }],
  };
  draft.criteria.push(added);
  const result = checklistPayload(draft);
  assert.equal(result.version, baseline.version);
  assert.equal(result.criteria.at(-1).weight, 2.5);
  assert.deepEqual(result.criteria.at(-1).allowed_answers, [0, 10, "na"]);
  assert.equal(result.criteria.at(-1).levels[1].value, 0);
  assert.equal(added.levels[1].value, "0");
  assert.notEqual(newCriterion(draft).id, added.id);
});

test("invalid weights and scale steps identify the criterion to edit", () => {
  const draft = structuredClone(baseline);
  draft.criteria[0].weight = "";
  assert.throws(() => checklistPayload(draft), (error) => error instanceof CriterionConfigError && error.criterionId === "01");
  draft.criteria[0].weight = "0";
  const scale = draft.criteria.find((item) => item.id === "04");
  scale.levels = scale.levels.filter(({ value }) => value !== scale.scale_max);
  assert.throws(() => checklistPayload(draft), /Добавьте ступень, равную максимуму/);
});

test("switching a type clears stale scale, flag and N/A fields before saving", () => {
  const draft = structuredClone(baseline);
  const item = draft.criteria.find((entry) => entry.id === "04");
  item.kind = "flag";
  item.flag_types = ["   insult  ", "", "confidential_disclosure"];
  item.na_allowed = false;
  const flag = checklistPayload(draft).criteria.find((entry) => entry.id === "04");
  assert.equal(flag.weight, null);
  assert.equal(flag.scale_max, null);
  assert.equal(flag.na_rule, null);
  assert.deepEqual(flag.levels, []);
  assert.deepEqual(flag.flag_types, ["insult", "confidential_disclosure"]);
  assert.deepEqual(flag.allowed_answers, ["yes", "no"]);
});

test("an omitted action requires an ordered checked range even when evidence exists", () => {
  const current = criterion("07");
  assert.throws(() => replacementPayload(current, draftRow({ answer: "no" }), messages), /укажите начало и конец/);
  assert.throws(() => replacementPayload(current, draftRow({ answer: "no", start: "m003", end: "m001" }), messages),
    /в порядке реплик/);
  const row = replacementPayload(current, draftRow({ answer: "no", start: "m002", end: "m003" }), messages);
  assert.deepEqual(row.checked_range, { start_message_id: "m002", end_message_id: "m003" });
  assert.equal(row.status, "evaluated");
});

test("replacement distinguishes zero, N/A, and an unknown answer", () => {
  const current = criterion("04");
  assert.equal(replacementPayload(current, draftRow({ answer: 0 }), messages).answer, 0);
  assert.throws(() => replacementPayload(current, draftRow({ answer: "na" }), messages), /причину N\/A/);
  assert.equal(replacementPayload(current, draftRow({ answer: "na", naReason: "Нет запроса" }), messages).na_reason, "Нет запроса");
  const row = replacementPayload(current, draftRow({ answer: null, uncertainty: "Нет записи", evidence: new Set() }), messages);
  assert.equal(row.status, "needs_review");
  assert.equal(row.answer, null);
  assert.equal(row.na_reason, null);
});

test("a critical flag can cite multiple episodes and keeps valid evidence references", () => {
  const current = criterion("08");
  assert.throws(() => replacementPayload(current, draftRow(), messages), /эпизод нарушения/);
  const episode = { type: "insult", reason: "Объяснение", evidence: new Set(["m003"]) };
  const draft = draftRow({ violations: [episode], evidence: new Set() });
  const row = replacementPayload(current, draft, messages);
  assert.deepEqual(row.evidence_message_ids, ["m003"]);
  assert.deepEqual(row.violations, [{ type: "insult", reason: "Объяснение", evidence_message_ids: ["m003"] }]);
  episode.evidence.add("m004");
  assert.throws(() => replacementPayload(current, draft, messages), /из этого диалога/);
});

test("save and confirm use the live revision fields and no replacement on confirm", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push([url, JSON.parse(options.body)]);
    return Response.json({});
  });
  await api.saveChecklist(baseline);
  await api.resolveAppeal("appeal-1", { decision: "confirm", reason: "Проверено", replacement: null, expected_revision: 2 });
  assert.deepEqual(calls, [
    ["/api/checklists", { base_version: baseline.version, checklist: baseline }],
    ["/api/appeals/appeal-1/resolve", { decision: "confirm", reason: "Проверено", replacement: null, expected_revision: 2 }],
  ]);
});
