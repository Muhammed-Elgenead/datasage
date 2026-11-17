import type { Dataset } from "../types";
import DataTable from "./DataTable";

function kb(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function ProfileView({ dataset }: { dataset: Dataset }) {
  const p = dataset.profile;
  const columns = p.columns.map((c) => c.name);
  return (
    <details className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <summary className="cursor-pointer select-none px-4 py-3">
        <span className="font-medium text-slate-800">{dataset.filename}</span>
        <span className="text-sm text-slate-500">
          {" "}
          · {p.row_count.toLocaleString()} rows × {p.column_count} columns · {kb(dataset.size_bytes)}
        </span>
      </summary>
      <div className="space-y-4 border-t border-slate-200 p-4">
        <DataTable
          table={{
            title: "Columns",
            columns: ["column", "type", "missing", "distinct"],
            rows: p.columns.map((c) => [c.name, c.dtype, c.nulls, c.unique]),
            total_rows: p.columns.length,
            truncated: false,
          }}
        />
        <DataTable
          table={{
            title: "Sample rows",
            columns,
            rows: p.sample.map((row) => columns.map((c) => row[c])),
            total_rows: p.sample.length,
            truncated: false,
          }}
        />
      </div>
    </details>
  );
}
