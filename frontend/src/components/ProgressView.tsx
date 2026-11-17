import type { Step } from "../progress";

function label(step: Step): string {
  switch (step.kind) {
    case "thinking":
      return step.step === 1 ? "Thinking about the question" : "Thinking";
    case "profile":
      return "Reading the data profile";
    case "code": {
      const what = step.language === "sql" ? "SQL" : "Code";
      if (step.status === "running") return `Running ${step.language === "sql" ? "SQL" : "code"} (attempt ${step.attempt})`;
      if (step.status === "ok") return `${what} ran (attempt ${step.attempt})`;
      return `${what} failed (attempt ${step.attempt}), fixing it`;
    }
  }
}

const ICON = { running: "⏳", ok: "✓", failed: "✗" } as const;

export default function ProgressView({ question, steps }: { question: string; steps: Step[] }) {
  return (
    <section className="space-y-3" aria-label="Working on your question">
      <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-white">
        {question}
      </p>
      <ol aria-live="polite" className="space-y-1.5 rounded-xl border border-dashed border-slate-300 bg-white/60 p-4 text-sm">
        {steps.length === 0 && <li className="text-slate-500">Starting…</li>}
        {steps.map((step, i) => (
          <li key={i} className="text-slate-700">
            {step.kind === "code" ? (
              <details>
                <summary className={`cursor-pointer ${step.status === "failed" ? "text-red-700" : ""}`}>
                  <span aria-hidden="true">{ICON[step.status]} </span>
                  {label(step)}
                </summary>
                <pre className="mt-1 overflow-x-auto rounded bg-slate-900 p-2 text-xs text-slate-100">
                  <code>{step.code}</code>
                </pre>
                {step.error && <p className="mt-1 font-mono text-xs text-red-700">{step.error}</p>}
              </details>
            ) : (
              <span className="text-slate-500">· {label(step)}</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
