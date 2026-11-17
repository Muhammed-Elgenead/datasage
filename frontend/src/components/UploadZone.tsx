import { useRef, useState } from "react";

import { uploadDataset } from "../api";
import type { Dataset } from "../types";

interface Props {
  sessionId: string;
  onUploaded: (dataset: Dataset) => void;
  compact?: boolean;
}

export default function UploadZone({ sessionId, onUploaded, compact = false }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upload(file: File | undefined) {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    try {
      onUploaded(await uploadDataset(sessionId, file));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void upload(e.dataTransfer.files[0]);
        }}
        className={`flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed text-center transition-colors ${
          compact ? "px-4 py-3" : "px-6 py-12"
        } ${dragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-white"}`}
      >
        {!compact && <p className="text-lg font-medium text-slate-800">Drop a CSV or Excel file here</p>}
        <p className="text-sm text-slate-500">
          {busy ? "Uploading and profiling…" : compact ? "Add another file: drop it here or" : ".csv or .xlsx, up to 50 MB, or"}{" "}
          {!busy && (
            <button
              type="button"
              onClick={() => input.current?.click()}
              className="font-medium text-indigo-600 underline-offset-2 hover:underline"
            >
              choose a file
            </button>
          )}
        </p>
        <input
          ref={input}
          type="file"
          accept=".csv,.xlsx"
          aria-label="Upload a CSV or Excel file"
          className="sr-only"
          onChange={(e) => void upload(e.target.files?.[0])}
        />
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
