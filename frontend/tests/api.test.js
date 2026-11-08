import assert from "node:assert/strict";
import { test } from "node:test";
import { api, allConversations, ApiError } from "../public/api.js";

test("network failures never fall back to saved or mock data and never retry a POST", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new TypeError("offline"); });
  await assert.rejects(api.startEvaluation("real-id", "1"), error => error instanceof ApiError && /Нет связи/.test(error.message));
  assert.equal(calls, 1);
});

test("HTML proxy responses and malformed list envelopes surface as API errors", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response("<html>bad gateway</html>", { headers: { "Content-Type": "text/html" } }));
  await assert.rejects(api.health(), ApiError);
  t.mock.method(globalThis, "fetch", async () => Response.json({ items: {}, total: 65 }));
  await assert.rejects(allConversations(), /формат списка/);
});

test("aborting navigation remains cancellation instead of a network error", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (_url, { signal }) => {
    controller.abort();
    signal.throwIfAborted();
  });
  await assert.rejects(api.conversation("real-id", controller.signal), { name: "AbortError" });
});
