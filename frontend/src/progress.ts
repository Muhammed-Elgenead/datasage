import type { ProgressEvent } from "./types";

// One line in the live progress list. A code attempt starts as "running" and is updated
// in place when its result arrives.
export type Step =
  | { kind: "thinking"; step: number }
  | { kind: "profile" }
  | {
      kind: "code";
      attempt: number;
      language: "python" | "sql";
      code: string;
      status: "running" | "ok" | "failed";
      error: string | null;
    };

export function addProgress(steps: Step[], event: ProgressEvent): Step[] {
  switch (event.type) {
    case "thinking":
      return [...steps, { kind: "thinking", step: event.step }];
    case "tool":
      return event.name === "get_profile" ? [...steps, { kind: "profile" }] : steps;
    case "code":
      return [
        ...steps,
        {
          kind: "code",
          attempt: event.attempt,
          language: event.language,
          code: event.code,
          status: "running",
          error: null,
        },
      ];
    case "run":
      return steps.map((s) =>
        s.kind === "code" && s.attempt === event.attempt
          ? { ...s, status: event.status === "ok" ? "ok" : "failed", error: event.error }
          : s,
      );
  }
}
