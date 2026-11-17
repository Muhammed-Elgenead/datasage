import type { SessionSummary } from "../types";

interface Props {
  sessions: SessionSummary[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

function title(s: SessionSummary): string {
  return s.first_question ?? (s.files.length ? s.files.join(", ") : "Empty session");
}

export default function Sidebar({ sessions, currentId, onSelect, onNew }: Props) {
  return (
    <nav aria-label="Sessions" className="flex h-full flex-col gap-3">
      <button
        type="button"
        onClick={onNew}
        className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700"
      >
        + New analysis
      </button>
      <ul className="-mx-1 flex-1 space-y-0.5 overflow-y-auto">
        {sessions.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              aria-current={s.id === currentId ? "page" : undefined}
              className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${
                s.id === currentId ? "bg-slate-200 text-slate-900" : "text-slate-700 hover:bg-slate-100"
              }`}
            >
              <span className="block truncate">{title(s)}</span>
              <span className="block truncate text-xs text-slate-500">
                {new Date(s.created_at).toLocaleString()}
                {s.questions > 0 && ` · ${s.questions} question${s.questions > 1 ? "s" : ""}`}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
