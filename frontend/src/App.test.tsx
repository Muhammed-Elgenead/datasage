import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";

afterEach(() => vi.unstubAllGlobals());

test("shows backend and database health", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", database: "ok", database_error: null })),
    ),
  );
  render(<App />);
  expect(await screen.findByText("Backend: ok · Database: ok")).toBeInTheDocument();
});

test("shows when the backend is unreachable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 502 })));
  render(<App />);
  expect(await screen.findByText("Backend unreachable: HTTP 502")).toBeInTheDocument();
});
