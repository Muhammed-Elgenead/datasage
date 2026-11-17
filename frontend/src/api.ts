import { SSEParser } from "./sse";
import type { Answer, Dataset, Mode, ProgressEvent, SessionDetail, SessionSummary } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return "The request was not valid.";
  } catch {
    // not JSON
  }
  return `HTTP ${res.status}`;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
  return (await res.json()) as T;
}

export const listSessions = () => request<SessionSummary[]>("/api/sessions");

export const getSession = (id: string) => request<SessionDetail>(`/api/sessions/${id}`);

export const createSession = () =>
  request<{ id: string; created_at: string }>("/api/sessions", { method: "POST" });

export function uploadDataset(sessionId: string, file: File): Promise<Dataset> {
  const form = new FormData();
  form.append("file", file);
  return request<Dataset>(`/api/sessions/${sessionId}/datasets`, { method: "POST", body: form });
}

export const chartUrl = (sessionId: string, runId: string, name: string) =>
  `/api/sessions/${sessionId}/runs/${runId}/${name}`;

/** Ask a question; progress events arrive through `onProgress`, the answer resolves the promise. */
export async function askStream(
  sessionId: string,
  question: string,
  onProgress: (event: ProgressEvent) => void,
  mode: Mode = "auto",
  signal?: AbortSignal,
): Promise<Answer> {
  const res = await fetch(`/api/sessions/${sessionId}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, mode }),
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, await errorDetail(res));

  const parser = new SSEParser();
  const decoder = new TextDecoder();
  const reader = res.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    const frames = done ? parser.flush() : parser.feed(decoder.decode(value, { stream: true }));
    for (const frame of frames) {
      const data = JSON.parse(frame.data);
      if (frame.event === "answer") return data as Answer;
      if (frame.event === "error") throw new ApiError(data.status, data.detail);
      onProgress(data as ProgressEvent);
    }
    if (done) throw new ApiError(0, "The connection closed before the answer arrived.");
  }
}
