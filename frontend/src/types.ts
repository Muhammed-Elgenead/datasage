// Shapes returned by the backend (see backend/app/api/sessions.py).

export interface Health {
  status: string;
  database: string;
  database_error: string | null;
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  nulls: number;
  unique: number;
  stats: Record<string, unknown>;
}

export interface Profile {
  row_count: number;
  column_count: number;
  columns: ColumnProfile[];
  sample: Record<string, unknown>[];
}

export interface Dataset {
  id: string;
  session_id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  profile: Profile;
  created_at: string;
}

export interface Table {
  title: string | null;
  columns: string[];
  rows: unknown[][];
  total_rows: number;
  truncated: boolean;
}

export interface Answer {
  id: string;
  question: string;
  status: string; // ok | code_failed | max_iterations | refusal | incomplete | failed
  answer: string | null;
  error: string | null;
  run_id: string | null;
  code: string | null;
  language: "python" | "sql" | null;
  code_error: string | null;
  tables: Table[];
  charts: string[];
  attempts: number;
  usage: Record<string, number>;
  created_at: string;
}

export interface SessionSummary {
  id: string;
  created_at: string;
  files: string[];
  first_question: string | null;
  questions: number;
}

export interface SessionDetail {
  id: string;
  created_at: string;
  datasets: Dataset[];
  questions: Answer[];
}

export type Mode = "auto" | "python" | "sql";

// Live progress while the agent works (POST /sessions/{id}/ask/stream).
export type ProgressEvent =
  | { type: "thinking"; step: number }
  | { type: "tool"; name: string }
  | { type: "code"; attempt: number; language: "python" | "sql"; code: string }
  | {
      type: "run";
      attempt: number;
      status: string;
      error: string | null;
      tables: number;
      charts: number;
    };
