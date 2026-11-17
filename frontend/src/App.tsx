import { useEffect, useState } from "react";

type Health = { status: string; database: string; database_error: string | null };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main className="mx-auto max-w-xl p-8 font-sans">
      <h1 className="text-2xl font-semibold">DataSage</h1>
      <p className="mt-4" role="status">
        {error
          ? `Backend unreachable: ${error}`
          : health
            ? `Backend: ${health.status} · Database: ${health.database}`
            : "Checking backend…"}
      </p>
    </main>
  );
}
