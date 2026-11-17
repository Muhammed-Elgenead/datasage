import { useCallback, useEffect, useRef, useState } from "react";

import { askStream, createSession, getSession, listSessions } from "./api";
import AnswerView from "./components/AnswerView";
import ProfileView from "./components/ProfileView";
import ProgressView from "./components/ProgressView";
import Sidebar from "./components/Sidebar";
import UploadZone from "./components/UploadZone";
import { addProgress, type Step } from "./progress";
import type { SessionDetail, SessionSummary } from "./types";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [current, setCurrent] = useState<SessionDetail | null>(null);
  const [pending, setPending] = useState<{ question: string; steps: Step[] } | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const refreshList = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch (e) {
      setError(`Could not load sessions: ${message(e)}`);
    }
  }, []);

  const open = useCallback(async (id: string) => {
    try {
      setCurrent(await getSession(id));
    } catch (e) {
      setError(`Could not open the session: ${message(e)}`);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const list = await listSessions();
        setSessions(list);
        if (list[0]) await open(list[0].id);
      } catch (e) {
        setError(`Could not reach the backend: ${message(e)}`);
      }
    })();
  }, [open]);

  useEffect(() => {
    end.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [current?.questions.length, pending?.steps.length]);

  async function newSession() {
    setError(null);
    try {
      const s = await createSession();
      setCurrent({ id: s.id, created_at: s.created_at, datasets: [], questions: [] });
      await refreshList();
    } catch (e) {
      setError(`Could not start a session: ${message(e)}`);
    }
  }

  async function ask() {
    const question = draft.trim();
    if (!current || !question || pending) return;
    const sessionId = current.id;
    setDraft("");
    setError(null);
    setPending({ question, steps: [] });
    try {
      await askStream(sessionId, question, (event) =>
        setPending((p) => (p ? { ...p, steps: addProgress(p.steps, event) } : p)),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      // The answer (or the failed attempt) is stored; reload so the page shows what's saved.
      await open(sessionId);
      setPending(null);
      void refreshList();
    }
  }

  const hasData = (current?.datasets.length ?? 0) > 0;

  return (
    <div className="flex h-screen bg-slate-50 font-sans text-slate-900">
      <aside className="hidden w-64 shrink-0 border-r border-slate-200 bg-white p-3 md:block">
        <h1 className="mb-3 px-1 text-lg font-semibold">
          Data<span className="text-indigo-600">Sage</span>
        </h1>
        <Sidebar sessions={sessions} currentId={current?.id ?? null} onSelect={(id) => {
            setError(null);
            void open(id);
          }} onNew={() => void newSession()} />
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2 md:hidden">
          <h1 className="font-semibold">
            Data<span className="text-indigo-600">Sage</span>
          </h1>
          <button type="button" onClick={() => void newSession()} className="text-sm font-medium text-indigo-600">
            + New
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
            {error && (
              <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
                {error}
              </p>
            )}

            {!current && (
              <div className="py-16 text-center">
                <h2 className="text-2xl font-semibold">Ask questions about your data</h2>
                <p className="mt-2 text-slate-600">
                  Upload a CSV or Excel file and ask in plain English. The agent writes and runs pandas code, then
                  shows the answer with tables, charts and the code it used.
                </p>
                <button
                  type="button"
                  onClick={() => void newSession()}
                  className="mt-6 rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700"
                >
                  Start a new analysis
                </button>
              </div>
            )}

            {current && (
              <>
                <section aria-label="Files" className="space-y-2">
                  {current.datasets.map((d) => (
                    <ProfileView key={d.id} dataset={d} />
                  ))}
                  <UploadZone
                    sessionId={current.id}
                    compact={hasData}
                    onUploaded={(d) => {
                      setCurrent((c) => (c && c.id === d.session_id ? { ...c, datasets: [...c.datasets, d] } : c));
                      void refreshList();
                    }}
                  />
                </section>

                {current.questions.map((a) => (
                  <AnswerView key={a.id} sessionId={current.id} answer={a} />
                ))}
                {pending && <ProgressView question={pending.question} steps={pending.steps} />}
                <div ref={end} />
              </>
            )}
          </div>
        </div>

        {current && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void ask();
            }}
            className="border-t border-slate-200 bg-white"
          >
            <div className="mx-auto flex max-w-4xl gap-2 px-4 py-3">
              <label htmlFor="question" className="sr-only">
                Your question
              </label>
              <textarea
                id="question"
                rows={1}
                value={draft}
                disabled={!hasData || pending !== null}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void ask();
                  }
                }}
                placeholder={hasData ? "Ask a question about your data…" : "Upload a file first"}
                className="min-h-10 flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 focus:border-indigo-500 focus:outline-none disabled:bg-slate-100"
              />
              <button
                type="submit"
                disabled={!hasData || pending !== null || !draft.trim()}
                className="rounded-lg bg-indigo-600 px-4 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {pending ? "Working…" : "Ask"}
              </button>
            </div>
          </form>
        )}
      </main>
    </div>
  );
}
