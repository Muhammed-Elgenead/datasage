import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { answer, RUN, SID } from "../test/fixtures";
import AnswerView from "./AnswerView";

test("shows the answer, its table, chart and the code used", () => {
  render(<AnswerView sessionId={SID} answer={answer} />);

  expect(screen.getByText("Which region leads?")).toBeInTheDocument();
  expect(screen.getByText("North leads with 100.")).toBeInTheDocument();

  const table = screen.getByRole("table");
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["region", "revenue"]);
  expect(within(table).getByText("250.5")).toBeInTheDocument();

  expect(screen.getByRole("img")).toHaveAttribute("src", `/api/sessions/${SID}/runs/${RUN}/chart_1.png`);

  const code = screen.getByText("Code used").closest("details")!;
  expect(code).not.toHaveAttribute("open"); // collapsed by default
  expect(within(code).getByText("· 2 attempts")).toBeInTheDocument();
  expect(within(code).getByText("df.groupby('region').sum()")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a failed answer explains why and shows the last error", () => {
  render(
    <AnswerView
      sessionId={SID}
      answer={{ ...answer, status: "code_failed", tables: [], charts: [], code_error: "KeyError: 'nope'" }}
    />,
  );

  expect(screen.getByRole("alert")).toHaveTextContent("The code kept failing");
  expect(screen.getByText("KeyError: 'nope'")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("an API failure shows its error without code", () => {
  render(
    <AnswerView
      sessionId={SID}
      answer={{ ...answer, status: "failed", answer: null, error: "Gemini rate limit reached", code: null, run_id: null, tables: [], charts: [] }}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Gemini rate limit reached");
  expect(screen.queryByText("Code used")).not.toBeInTheDocument();
});
