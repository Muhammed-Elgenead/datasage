import type { Answer, Dataset, SessionDetail } from "../types";

export const SID = "11111111-1111-1111-1111-111111111111";
export const RUN = "a".repeat(32);

export const dataset: Dataset = {
  id: "d1",
  session_id: SID,
  filename: "sales.csv",
  file_type: "csv",
  size_bytes: 2048,
  profile: {
    row_count: 2,
    column_count: 2,
    columns: [
      { name: "region", dtype: "str", nulls: 0, unique: 2, stats: {} },
      { name: "revenue", dtype: "int64", nulls: 1, unique: 2, stats: {} },
    ],
    sample: [
      { region: "north", revenue: 100 },
      { region: "south", revenue: null },
    ],
  },
  created_at: "2026-11-17T10:00:00Z",
};

export const answer: Answer = {
  id: "q1",
  question: "Which region leads?",
  status: "ok",
  answer: "North leads with 100.",
  error: null,
  run_id: RUN,
  code: "df.groupby('region').sum()",
  code_error: null,
  tables: [{ title: null, columns: ["region", "revenue"], rows: [["north", 100], ["south", 250.5]], total_rows: 2, truncated: false }],
  charts: ["chart_1.png"],
  attempts: 2,
  usage: {},
  created_at: "2026-11-17T10:01:00Z",
};

export const detail = (questions: Answer[] = [answer]): SessionDetail => ({
  id: SID,
  created_at: "2026-11-17T10:00:00Z",
  datasets: [dataset],
  questions,
});

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const sse = (...events: [string, unknown][]) =>
  new Response(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(""), {
    headers: { "Content-Type": "text/event-stream" },
  });
