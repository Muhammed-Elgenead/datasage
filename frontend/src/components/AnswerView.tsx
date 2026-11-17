import { chartUrl } from "../api";
import type { Answer } from "../types";
import DataTable from "./DataTable";

const STATUS_NOTE: Record<string, string> = {
  code_failed: "The code kept failing, so this answer is incomplete.",
  max_iterations: "The agent ran out of steps before finishing.",
  refusal: "The model declined to answer this question.",
  incomplete: "The answer was cut off.",
  failed: "Something went wrong while answering.",
};

export default function AnswerView({ sessionId, answer }: { sessionId: string; answer: Answer }) {
  const note = STATUS_NOTE[answer.status];
  return (
    <article className="space-y-3" aria-label={`Answer to: ${answer.question}`}>
      <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-white">
        {answer.question}
      </p>

      <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        {note && (
          <p role="alert" className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
            {note}
            {answer.error && <span className="block text-amber-700">{answer.error}</span>}
          </p>
        )}
        {answer.answer && <p className="whitespace-pre-wrap leading-relaxed text-slate-800">{answer.answer}</p>}

        {answer.run_id &&
          answer.charts.map((name) => (
            <img
              key={name}
              src={chartUrl(sessionId, answer.run_id!, name)}
              alt={`Chart for: ${answer.question}`}
              className="max-h-[28rem] max-w-full rounded-md border border-slate-200"
            />
          ))}

        {answer.tables.map((t, i) => (
          <DataTable key={i} table={t} />
        ))}

        {answer.code && (
          <details className="group rounded-md border border-slate-200">
            <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
              {answer.language === "sql" ? "SQL used" : "Code used"}
              {answer.attempts > 1 && (
                <span className="font-normal text-slate-500"> · {answer.attempts} attempts</span>
              )}
            </summary>
            <pre className="overflow-x-auto border-t border-slate-200 bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
              <code>{answer.code}</code>
            </pre>
            {answer.code_error && (
              <pre className="overflow-x-auto border-t border-red-200 bg-red-50 p-3 text-xs text-red-800">
                {answer.code_error}
              </pre>
            )}
          </details>
        )}
      </div>
    </article>
  );
}
