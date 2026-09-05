/**
 * Thin client for the Kivi API.
 *
 * Requests go to a relative /api path; Vite proxies them to the backend on
 * 8000 in development (see vite.config.js), so nothing here needs to know the
 * server's address.
 */

const BASE = "/api";

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (cause) {
    throw new Error(
      "Could not reach the Kivi backend. Is it running? " +
        "Start it with: uvicorn backend.main:app --reload",
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* the body was not JSON; the status line is all we have */
    }
    throw new Error(detail);
  }

  if (response.status === 204) return null;
  return response.json();
}

const qs = (params) => {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      if (Array.isArray(value)) value.forEach((v) => search.append(key, v));
      else search.append(key, value);
    }
  });
  const string = search.toString();
  return string ? `?${string}` : "";
};

/**
 * Consume a server-sent-event stream from a POST.
 *
 * `EventSource` only does GET, and both of these endpoints take a body, so the
 * stream is read off `fetch` by hand. SSE framing is simple enough that this is
 * a dozen lines: events are separated by a blank line, and the two fields that
 * matter are `event:` and `data:`.
 *
 * The buffer split is the part worth being careful about - a chunk boundary
 * falls wherever the network puts it, so a frame routinely arrives in two
 * pieces. Everything up to the last blank line is complete; whatever follows it
 * stays in the buffer for the next chunk.
 */
async function stream(path, body, onEvent) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new Error(
      "Could not reach the Kivi backend. Is it running? " +
        "Start it with: uvicorn backend.main:app --reload",
    );
  }
  if (!response.ok || !response.body) {
    throw new Error(`${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  let failure = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let name = "message";
      const data = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (!data.length) continue;

      let payload;
      try {
        payload = JSON.parse(data.join("\n"));
      } catch {
        continue; // a frame we cannot read is not worth failing the stream over
      }

      if (name === "stage") onEvent?.(payload);
      else if (name === "done") result = payload;
      else if (name === "error") failure = payload?.detail || "the pipeline failed";
    }
  }

  if (failure) throw new Error(failure);
  return result;
}

export const api = {
  // ---- system ----------------------------------------------------------
  status: () => request("/system/status"),
  reset: () => request("/system/reset?confirm=true", { method: "POST" }),

  // ---- transcripts -----------------------------------------------------
  feed: (params) => request(`/transcripts/feed${qs(params)}`),
  // One real dictation, chosen by the backend for how much of the pipeline it
  // can demonstrate, walked from raw audio text to a stored vector.
  example: () => request("/transcripts/example"),
  transcript: (id) => request(`/transcripts/${id}`),
  applications: () => request("/transcripts/applications"),
  // `process: false` stores the dictation and returns immediately, so the UI
  // can show it in the feed before extraction - which takes seconds against a
  // real model - has run.
  addTranscript: (payload, options = {}) =>
    request(`/transcripts${qs({ process: options.process === false ? "false" : undefined })}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  // Deleting is reversible: the dictation is hidden and the memories it
  // produced are marked DELETED, never removed, so provenance survives.
  deleteTranscript: (id, reason) =>
    request(`/transcripts/${id}${qs({ reason })}`, { method: "DELETE" }),
  restoreTranscript: (id) =>
    request(`/transcripts/${id}/restore`, { method: "POST" }),

  // ---- memory ----------------------------------------------------------
  knowledge: () => request("/memories/knowledge"),
  memories: (params) => request(`/memories${qs(params)}`),
  memory: (id) => request(`/memories/${id}`),
  correctMemory: (id, patch) =>
    request(`/memories/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  forgetMemory: (id, reason) =>
    request(`/memories/${id}${qs({ reason })}`, { method: "DELETE" }),
  restoreMemory: (id) => request(`/memories/${id}/restore`, { method: "POST" }),
  process: (payload = {}) =>
    request("/memory/process", { method: "POST", body: JSON.stringify(payload) }),

  // ---- hey kivi --------------------------------------------------------
  ask: (question, topK) =>
    request("/hey-kivi/query", {
      method: "POST",
      body: JSON.stringify({ question, top_k: topK ?? null }),
    }),
  history: (limit = 50) => request(`/hey-kivi/history${qs({ limit })}`),
  queryDetail: (id) => request(`/hey-kivi/queries/${id}`),
  suggestions: () => request("/hey-kivi/suggestions"),

  // ---- evaluation ------------------------------------------------------
  evaluation: () => request("/evaluation/results"),

  // ---- analytics, scoped to the screen that shows it -------------------
  historyAnalytics: () => request("/analytics/history"),
  memoryAnalytics: () => request("/analytics/memory"),
  queryAnalytics: () => request("/analytics/queries"),

  // ---- the same two operations, narrated stage by stage -----------------
  // `onStage` is called as each stage of the real pipeline finishes, carrying
  // the values that stage actually computed. The resolved value is identical
  // to what the plain call above returns, so a caller can fall back to it.
  askStreaming: (question, onStage, topK) =>
    stream("/stream/ask", { question, top_k: topK }, onStage),

  dictateStreaming: (payload, onStage) => stream("/stream/dictate", payload, onStage),
};

/** Format an ISO timestamp as a short, readable clock time. */
export function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso).slice(11, 16);
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Format an ISO timestamp as "24 Aug, 10:30". */
export function formatStamp(iso) {
  if (!iso) return "unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso).slice(0, 16).replace("T", " ");
  return `${date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  })}, ${date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
}

export function formatNumber(value, digits = 0) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatPercent(value, digits = 0) {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}
