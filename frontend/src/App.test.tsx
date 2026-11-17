import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import { answer, detail, json, SID, sse } from "./test/fixtures";

afterEach(() => vi.unstubAllGlobals());

const summary = { id: SID, created_at: "2026-11-17T10:00:00Z", files: ["sales.csv"], first_question: "Which region leads?", questions: 1 };

test("opens the latest session with its files and past answers", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => (url === "/api/sessions" ? json([summary]) : json(detail()))),
  );
  render(<App />);

  expect(await screen.findByText("North leads with 100.")).toBeInTheDocument();
  expect(screen.getByText("sales.csv")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Which region leads\?/ })).toHaveAttribute("aria-current", "page");
});

test("asking streams progress, then shows the stored answer", async () => {
  const followUp = { ...answer, id: "q2", question: "Total revenue?", answer: "350 in total." };
  let asked = false;
  let releaseStream: () => void = () => {};
  const streamGate = new Promise<void>((r) => (releaseStream = r));
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/sessions") return json([summary]);
    if (url.endsWith("/ask/stream")) {
      asked = true;
      expect(JSON.parse(init!.body as string)).toEqual({ question: "Total revenue?", mode: "sql" });
      await streamGate;
      return sse(
        ["thinking", { type: "thinking", step: 1 }],
        ["code", { type: "code", attempt: 1, language: "python", code: "df.revenue.sum()" }],
        ["run", { type: "run", attempt: 1, status: "ok", error: null, tables: 0, charts: 0 }],
        ["answer", followUp],
      );
    }
    return json(detail(asked ? [answer, followUp] : [answer]));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByText("North leads with 100.");

  fireEvent.click(screen.getByRole("radio", { name: "SQL" })); // force SQL for this question
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Total revenue?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  // While the agent works, the question shows with a progress list.
  expect(await screen.findByRole("region", { name: "Working on your question" })).toHaveTextContent("Total revenue?");
  expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
  releaseStream();

  expect(await screen.findByText("350 in total.")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("region", { name: "Working on your question" })).not.toBeInTheDocument());
});

test("a stream error is shown", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/sessions") return json([summary]);
      if (url.endsWith("/ask/stream")) return sse(["error", { status: 503, detail: "The code sandbox is unavailable" }]);
      return json(detail());
    }),
  );
  render(<App />);
  await screen.findByText("North leads with 100.");

  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Anything?" } });
  fireEvent.submit(screen.getByLabelText("Your question").closest("form")!);

  expect(await screen.findByText("The code sandbox is unavailable")).toBeInTheDocument();
});

test("asking is disabled until a file is uploaded", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => (url === "/api/sessions" ? json([summary]) : json({ ...detail([]), datasets: [] }))),
  );
  render(<App />);

  expect(await screen.findByPlaceholderText("Upload a file first")).toBeDisabled();
  expect(screen.getByText("Drop a CSV or Excel file here")).toBeInTheDocument();
});

test("a backend that can't be reached is reported", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the backend");
});
