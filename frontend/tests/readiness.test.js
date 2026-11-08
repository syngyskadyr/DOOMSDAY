import assert from "node:assert/strict";
import { test } from "node:test";
import { api, ApiError } from "../public/api.js";
import { apiAvailable, evaluationGate, modelPresentation } from "../public/readiness.js";

const configuredAPI = { status: "ok", api_version: "1", evaluator_status: "ready" };
const ready = { model_status: "ready", generation_tested: false };

test("a configured API alone never enables evaluation", () => {
  assert.equal(evaluationGate(configuredAPI, null).disabled, true);
  for (const model_status of ["unknown", "unavailable", "unconfigured", "unexpected"]) {
    assert.equal(evaluationGate(configuredAPI, { model_status }).disabled, true);
    assert.equal(modelPresentation({ model_status }).ready, false);
  }
  assert.equal(evaluationGate(null, ready).disabled, true);
  assert.equal(evaluationGate(configuredAPI, ready).disabled, false);
});

test("HTTP 200 with a malformed health contract never confirms API readiness", async (t) => {
  for (const payload of [null, {}, { unexpected: true }, { status: "ok" }, { status: "ok", api_version: 1 }, { status: "ok", api_version: "2" }]) {
    assert.equal(apiAvailable(payload), false);
    assert.equal(evaluationGate(payload, ready).disabled, true);
  }
  t.mock.method(globalThis, "fetch", async () => Response.json({ unexpected: true }));
  assert.equal(apiAvailable(await api.health()), false);
  assert.equal(apiAvailable(configuredAPI), true);
});

test("model status is explicit and conservatively rejects an unexpected contract", () => {
  assert.match(modelPresentation(null).label, /неизвестна/);
  assert.match(modelPresentation({ model_status: "unconfigured" }).label, /не настроена/);
  assert.match(modelPresentation({ model_status: "unavailable" }).label, /недоступна/);
  assert.equal(modelPresentation({ model_status: "ready" }).ready, false);
  assert.equal(modelPresentation(ready).tone, "success");
});

test("launching, active evaluation and unavailable-model messages stay distinct", () => {
  assert.deepEqual(evaluationGate(configuredAPI, ready, { submitting: true }), { disabled: true, reason: "Запуск оценки…" });
  assert.match(evaluationGate(configuredAPI, ready, { active: true }).reason, /уже выполняется/);
  assert.equal(evaluationGate(configuredAPI, { model_status: "unavailable", message: "Сервер выключен." }).reason, "Сервер выключен.");
});

test("readiness uses a GET with no generation body and supports cancellation", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/model-health");
    assert.equal(options.method, "GET");
    assert.equal(options.body, undefined);
    assert.equal(options.signal.aborted, false);
    controller.abort();
    assert.equal(options.signal.aborted, true);
    return Response.json(ready);
  });
  assert.deepEqual(await api.modelHealth(controller.signal), ready);
});

test("probe HTTP errors preserve errors rather than producing a ready status", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ error: { message: "Нет связи с сервером модели." } }, { status: 503 }));
  await assert.rejects(api.modelHealth(), (error) => error instanceof ApiError && error.status === 503);
});
