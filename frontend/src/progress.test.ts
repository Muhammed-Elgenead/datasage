import { expect, test } from "vitest";

import { addProgress, type Step } from "./progress";
import type { ProgressEvent } from "./types";

test("builds the step list and updates code attempts in place", () => {
  const events: ProgressEvent[] = [
    { type: "thinking", step: 1 },
    { type: "tool", name: "get_profile" },
    { type: "code", attempt: 1, language: "sql", code: "bad" },
    { type: "run", attempt: 1, status: "error", error: "KeyError: 'x'", tables: 0, charts: 0 },
    { type: "code", attempt: 2, language: "python", code: "good" },
    { type: "run", attempt: 2, status: "ok", error: null, tables: 1, charts: 1 },
  ];
  const steps = events.reduce<Step[]>(addProgress, []);

  expect(steps).toEqual([
    { kind: "thinking", step: 1 },
    { kind: "profile" },
    { kind: "code", attempt: 1, language: "sql", code: "bad", status: "failed", error: "KeyError: 'x'" },
    { kind: "code", attempt: 2, language: "python", code: "good", status: "ok", error: null },
  ]);
});

test("timeouts and other non-ok runs count as failed", () => {
  const steps = addProgress(addProgress([], { type: "code", attempt: 1, language: "python", code: "x" }), {
    type: "run",
    attempt: 1,
    status: "timeout",
    error: "Stopped after 30 seconds.",
    tables: 0,
    charts: 0,
  });
  expect(steps[0]).toMatchObject({ status: "failed" });
});
