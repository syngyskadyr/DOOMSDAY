import assert from "node:assert/strict";
import { test } from "node:test";
import { allConversations, api, ApiError } from "../public/api.js";
import { dialogueHref, filterConversations, messagesInRange, summarize } from "../public/model.js";

const items = [
  { conversation_id: "chat-ru", channel: "agent chat", language: "RU", latest_evaluation: null },
  { conversation_id: "Call/EN", channel: "voice call", language: "EN",
    latest_evaluation: { status: "completed", total_score: 0, critical_error: true } },
  { conversation_id: "chat-kz", channel: "agent chat", language: "KZ", synthetic: true,
    latest_evaluation: { status: "needs_review", total_score: null, critical_error: null } },
  { conversation_id: "chat-en", channel: "agent chat", language: "EN",
    latest_evaluation: { status: "completed", total_score: 100, critical_error: false } },
];

test("registry intersects case-insensitive ID search, language, channel and flag", () => {
  assert.deepEqual(filterConversations(items, { search: "  CALL/ ", channel: "voice call", language: "EN", critical: "true" }), [items[1]]);
  assert.deepEqual(filterConversations(items, { critical: "false" }), [items[3]]);
  assert.deepEqual(filterConversations(items, { critical: "unknown", status: "needs_review" }), [items[2]]);
  assert.deepEqual(filterConversations(items, { source: "test" }), [items[2]]);
});

test("analytics distinguishes no score, score zero, and the independent critical flag", () => {
  const result = summarize(items);
  assert.equal(result.processed, 3);
  assert.equal(result.scored, 2);
  assert.equal(result.average, 50);
  assert.equal(result.critical, 1);
  assert.deepEqual(result.bins, [1, 0, 0, 0, 1]);
  assert.equal(summarize([items[0]]).average, null);
});

test("evidence range follows transcript order, including across m999", () => {
  const messages = ["m998", "m999", "m1000", "m1001"].map((message_id) => ({ message_id }));
  assert.deepEqual(messagesInRange(messages, "m999", "m1001"), messages.slice(1));
  assert.deepEqual(messagesInRange(messages, "m1000", "m999"), []);
  assert.deepEqual(messagesInRange(messages, "m001", "m1000"), []);
  assert.equal(dialogueHref("Call/EN #1", { evaluation: "a", start: "m002" }),
    "#/dialogues/Call%2FEN%20%231?evaluation=a&message=m002");
});

test("summary pagination advances by the number of rows received", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return Response.json({ total: 4, items: calls.length === 1 ? items.slice(0, 3) : items.slice(3) });
  });
  assert.deepEqual(await allConversations(), items);
  assert.deepEqual(calls, ["/api/conversations?limit=200&offset=0", "/api/conversations?limit=200&offset=3"]);
});

test("evaluation request preserves exact version and backend error envelope", async (t) => {
  t.mock.method(globalThis, "fetch", async (url, request) => {
    assert.equal(url, "/api/conversations/Call%2FEN/evaluations");
    assert.deepEqual(JSON.parse(request.body), { checklist_version: "2" });
    return Response.json({ error: { code: "EVALUATOR_UNAVAILABLE", message: "Настройте модель." } }, { status: 503 });
  });
  await assert.rejects(api.startEvaluation("Call/EN", "2"), (error) =>
    error instanceof ApiError && error.status === 503 && error.code === "EVALUATOR_UNAVAILABLE" && error.message === "Настройте модель.");
});
