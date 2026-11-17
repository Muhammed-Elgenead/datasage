import type { Table } from "../types";

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export default function DataTable({ table }: { table: Table }) {
  return (
    <figure className="min-w-0">
      {table.title && <figcaption className="mb-1 text-sm font-medium text-slate-700">{table.title}</figcaption>}
      <div className="max-h-96 overflow-auto rounded-md border border-slate-200">
        <table className="min-w-full text-left text-sm">
          <thead className="sticky top-0 bg-slate-100 text-slate-700">
            <tr>
              {table.columns.map((c) => (
                <th key={c} scope="col" className="whitespace-nowrap px-3 py-1.5 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i} className="border-t border-slate-100 odd:bg-white even:bg-slate-50">
                {row.map((v, j) => (
                  <td key={j} className={`whitespace-nowrap px-3 py-1 ${typeof v === "number" ? "text-right tabular-nums" : ""}`}>
                    {cell(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.truncated && (
        <p className="mt-1 text-xs text-slate-500">
          Showing {table.rows.length} of {table.total_rows} rows.
        </p>
      )}
    </figure>
  );
}
