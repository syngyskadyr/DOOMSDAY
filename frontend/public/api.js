export class ApiError extends Error {
  constructor(message, { status = 0, code, details } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request(path, { params, method = "GET", body, signal } = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== "" && value != null) query.set(key, value);
  }
  const url = "/api" + path + (query.size ? "?" + query : "");
  let response;
  let payload;
  try {
    response = await fetch(url, {
      method,
      signal,
      cache: "no-store",
      headers: body === undefined
        ? { Accept: "application/json" }
        : { Accept: "application/json", "Content-Type": "application/json" },
      body: typeof body === "string" ? body : JSON.stringify(body),
    });
    if (response.headers.get("content-type")?.includes("application/json")) {
      payload = await response.json();
    }
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(response ? "Некорректный ответ сервера." : "Нет связи с сервером.");
  }
  if (!response.ok) {
    throw new ApiError(payload?.error?.message || "Ошибка сервера (" + response.status + ").", {
      status: response.status,
      code: payload?.error?.code,
      details: payload?.error?.details,
    });
  }
  if (payload == null) throw new ApiError("Сервер вернул ответ в неожиданном формате.");
  return payload;
}

const segment = encodeURIComponent;
const post = (path, body) => request(path, { method: "POST", body });

export const api = {
  health: (signal) => request("/health", { signal }),
  modelHealth: (signal) => request("/model-health", { signal }),
  conversations: (params, signal) => request("/conversations", { params, signal }),
  conversation: (id, signal) => request("/conversations/" + segment(id), { signal }),
  importConversations: (json) => post("/conversations/import", json),
  checklists: (signal) => request("/checklists", { signal }),
  checklist: (version, signal) => request("/checklists/" + segment(version), { signal }),
  currentChecklist: (signal) => request("/checklists/current", { signal }),
  saveChecklist: (checklist) => post("/checklists", { base_version: checklist.version, checklist }),
  startEvaluation: (id, checklist_version) =>
    post("/conversations/" + segment(id) + "/evaluations", { checklist_version }),
  evaluation: (id, signal) => request("/evaluations/" + segment(id), { signal }),
  appeals: (status, signal) => request("/appeals", { params: { status }, signal }),
  createAppeal: (id, body) => post("/evaluations/" + segment(id) + "/appeals", body),
  resolveAppeal: (id, body) => post("/appeals/" + segment(id) + "/resolve", body),
};

// The API does not have a search or analytics endpoint. Fetch summary pages
// (never transcripts) for whole-registry ID search and exact aggregates.
export async function allConversations(signal) {
  const limit = 200;
  const rows = new Map();
  let offset = 0;
  while (true) {
    const { items, total } = await api.conversations({ limit, offset }, signal);
    for (const item of items) rows.set(item.conversation_id, item);
    offset += items.length;
    if (!items.length || offset >= total) return [...rows.values()];
  }
}
