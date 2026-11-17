import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { dataset, json, SID } from "../test/fixtures";
import UploadZone from "./UploadZone";

afterEach(() => vi.unstubAllGlobals());

const file = () => new File(["region,revenue\nnorth,1\n"], "sales.csv", { type: "text/csv" });

test("a dropped file is uploaded to the session", async () => {
  const fetchMock = vi.fn().mockResolvedValue(json(dataset, 201));
  vi.stubGlobal("fetch", fetchMock);
  const onUploaded = vi.fn();
  render(<UploadZone sessionId={SID} onUploaded={onUploaded} />);

  fireEvent.drop(screen.getByText("Drop a CSV or Excel file here").parentElement!, {
    dataTransfer: { files: [file()] },
  });

  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(dataset));
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe(`/api/sessions/${SID}/datasets`);
  expect((init.body as FormData).get("file")).toBeInstanceOf(File);
});

test("choosing a file works too, and server errors are shown", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: "Only .csv and .xlsx files are supported" }, 415)));
  const onUploaded = vi.fn();
  render(<UploadZone sessionId={SID} onUploaded={onUploaded} />);

  fireEvent.change(screen.getByLabelText("Upload a CSV or Excel file"), { target: { files: [file()] } });

  expect(await screen.findByRole("alert")).toHaveTextContent("Only .csv and .xlsx files are supported");
  expect(onUploaded).not.toHaveBeenCalled();
});
