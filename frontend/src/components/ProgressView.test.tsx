import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import ProgressView from "./ProgressView";

test("labels SQL and Python attempts and shows failures", () => {
  render(
    <ProgressView
      question="Top region?"
      steps={[
        { kind: "thinking", step: 1 },
        { kind: "profile" },
        { kind: "code", attempt: 1, language: "sql", code: "SELECT x", status: "failed", error: "Binder Error" },
        { kind: "code", attempt: 2, language: "sql", code: "SELECT region", status: "running", error: null },
        { kind: "code", attempt: 3, language: "python", code: "df.plot()", status: "ok", error: null },
      ]}
    />,
  );
  expect(screen.getByText("Reading the data profile", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("SQL failed (attempt 1), fixing it")).toBeInTheDocument();
  expect(screen.getByText("Binder Error")).toBeInTheDocument();
  expect(screen.getByText("Running SQL (attempt 2)")).toBeInTheDocument();
  expect(screen.getByText("Code ran (attempt 3)")).toBeInTheDocument();
});
