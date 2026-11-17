# DataSage — Build Plan

Work one phase at a time. When a phase's **Done when** list is fully met, stop and report.

Status: ⬜ not started · 🟨 in progress · ✅ done

---

## Phase 1 — Scaffold + upload/profiling ✅

> Local dev runs without Compose: Docker Hub images are blocked in our region (see `docs/decisions.md`).
> Postgres 16 runs natively in WSL on E:. The `docker compose up` check moves to CI in Phase 7.

**Goal:** a runnable skeleton, plus the ability to upload a file and see a profile of it.

- Create the directory layout from `CLAUDE.md`.
- `backend/`: uv project, FastAPI app with `GET /health`, pydantic-settings config, ruff and pytest set up.
- Alembic set up against async SQLAlchemy. Models: `Session`, `Dataset` (file metadata and profile).
- `POST /sessions/{id}/datasets` uploads a CSV/XLSX file into that session's data folder. It checks file type and size and rejects anything else.
- Profiling: row and column counts, dtypes, null counts, sample rows, and basic stats, stored as JSON.
- `frontend/`: Vite + React + TS + Tailwind, with a page that shows the health status.
- `docker-compose.yml` (postgres:16, backend, frontend), `.env.example`, `.gitignore`, and `docs/decisions.md`.

**Done when:**
- [x] ~~`docker compose up` starts all services~~ → replaced locally by: `scripts/dev.ps1` starts Postgres (WSL), backend and frontend, and `/health` returns 200 with the DB ok through the Vite proxy. `docker compose config` is valid, and the backend image builds and runs as non-root in WSL Docker. The full `compose up` check moves to Phase 7 CI
- [x] uploading a CSV and an XLSX returns a correct profile, and bad files (wrong type, too large, empty, unparsable) are rejected with clear errors
- [x] `uv run pytest` passes (16 tests, including real-Postgres and migration-drift tests), `uv run ruff check` is clean, and the frontend tests and build pass
- [x] Docker data is on E:: there is no Docker Desktop, and Docker Engine lives inside the WSL distro at `E:\wsl\Ubuntu-24.04`. Project tooling wrote nothing to C:

**Phase 7 carry-over:** CI smoke test with `docker compose up`, plus a request-size cap at the proxy.

---

## Phase 2 — Sandbox service ✅

**Goal:** run untrusted pandas code safely and get back structured results.

- `sandbox-image/`: a slim Python 3.12 image with pandas, numpy, openpyxl, and matplotlib installed via uv, running as a non-root user.
- A runner inside the image: it reads the code, runs it, and writes `result.json` (stdout, stderr, error and traceback, tables as JSON, charts as PNG files) to the output folder.
- `backend/app/sandbox/`: a client that starts one container per run with every limit from the `CLAUDE.md` sandbox rules, then collects the outputs and always removes the container.
- Size caps on stdout, tables, and charts.

**Done when:**
- [x] tests show working code returns a table and a chart, and failing code returns the traceback
- [x] tests show the limits hold: no network, the timeout kills infinite loops, the memory limit kills memory bombs, other files can't be read, and the code runs as non-root. They also cover the read-only filesystem, cgroup CPU/memory/pids limits, no capabilities, capped output, and planted symlinks
- [x] no sandbox containers are left over after a run, including after a timeout

**Phase 7 carry-over:** the backend image needs a Docker CLI and daemon access to run sandboxes under Compose; a size-limited `/out` volume.

---

## Phase 3 — Agent loop with self-correction ✅

> Verified 2026-11-17 on `gemini-3.5-flash-lite` with a messy sales CSV (prices as `"$12.50"` text, mixed ISO and DD/MM/YYYY dates): a monthly-revenue line chart (5 runs, one `NameError` fixed by the agent itself, 80 s) and a per-product revenue question (exact match with ground truth, 15 s).
> Known issue for Phase 6: on mixed date formats the agent parses with `format="mixed", dayfirst=True`, which swaps day and month on some ISO dates, so the monthly numbers are off. The run reports `ok` because the code ran. Evals must check answers, not just statuses.

**Goal:** the agent answers questions by writing code, running it, and fixing its own errors.

- `agents/`: an Anthropic tool-use loop with a max-iterations guard and API retries.
- Tools: `run_python(code)` (sandbox) and `get_profile()`. Sandbox errors return to the model as `is_error` results with the traceback, so it can fix the code and retry.
- A retry limit for self-correction. The final answer has text, tables, charts, and the code that produced them.
- `POST /sessions/{id}/ask` runs the loop. Every message, tool call, and code run is stored.

**Done when:**
- [x] tests with a mocked LLM cover: success on the first try, an error followed by a fix, reaching the retry limit, and reaching the max-iterations limit (plus refusal, max_tokens, parallel calls, unknown tools, API and sandbox failures, and storage)
- [x] a manual run against the real API answers a question that needs a chart, and one that recovers from an error

---

## Phase 4 — Frontend 🟨

**Goal:** a usable UI for the whole flow.

- Upload a file (drag and drop) and view its profile.
- A chat: ask a question, then see the answer with its tables, charts, and a collapsible "code used" panel.
- Show progress while the agent works (streaming steps over SSE), including error-and-fix retries.
- Session history.

**Done when:**
- [ ] the full flow works in the browser: upload, ask, then see the answer with its table, chart, and code (verified through the Vite proxy with real Gemini and the sandbox: upload, a live SSE stream, the stored answer with a correct table, the chart PNG, and history. A visual click-through is still to do)
- [x] frontend tests (Vitest) cover the main components, and the build has no type errors

---

## Phase 5 — SQL mode ✅

> Verified 2026-11-17 on `gemini-3.5-flash-lite`: forced SQL (orders per region, one query, 9 s) and auto mode (the agent chose pandas for text-encoded prices, 17 s). Both match the ground truth exactly.

**Goal:** ask the same questions with SQL as well as pandas.

- Load the session's data into DuckDB inside the sandbox (no database server, no network).
- A `run_sql(query)` tool. The agent chooses pandas or SQL, or the user can force one.
- The answer shows the SQL used. Error-and-fix works the same way as in pandas mode.

**Done when:**
- [x] tests with a mocked LLM cover SQL success, SQL errors and fixes, and the choice between pandas and SQL (auto offers both, forced modes offer one, and tools outside the mode are refused)
- [x] SQL runs inside the sandbox with the same limits as Python, and a test proves it (identical `docker run` arguments; real-container tests for no file, network or extension access, locked settings, and a runaway query stopped by the limits)

---

## Phase 6 — Evals ✅

> Two full runs on `gemini-3.5-flash-lite` (commit 3cf3a2f): 33/34 each. Run 1 failed sales-10, the mixed-date trap (retry limit). Run 2 fixed it but regressed employees-09: the agent printed the counts instead of showing a table. Expect about one case of difference between runs.

**Goal:** measure answer quality and catch regressions.

- `evals/`: a set of fixed datasets plus questions with known correct answers (numbers, tables), including messy data (text-encoded numbers, mixed date formats; see the Phase 3 known issue).
- A runner that sends each question through the real agent and scores it (exact or tolerance match, chart produced, number of self-correction retries, latency, tokens).
- Results saved to `docs/evals/` as a dated report.

**Done when:**
- [x] at least 30 questions across at least 3 datasets, with a scored report committed (34 cases, 4 datasets: `docs/evals/`)
- [x] the runner can be run with one command, and a regression is visible when compared with the previous report (`uv run --project backend python -m evals.run`; the second report lists employees-09 as a regression and sales-10 as fixed)

---

## Phase 7 — Deploy 🟨

> 2026-11-17: the public deployment is postponed (your decision). Done: the production image (`Dockerfile`), production Compose (Postgres, app, sandbox image, app → host Docker socket, same-path data mount), the CI workflow, the body-size cap, the per-IP question limit, cleanup of old sessions, and the README.
> Waiting: a GitHub repo (you're creating it) so CI can run on `main`. Locally, the backend tests pass (124), Compose config is valid, and the built frontend plus `/api` serve from one origin. The production image itself only builds in CI (Docker Hub is blocked here).
> Deferred with the deploy: choosing a host, a size-limited `/out` volume, gVisor, a reverse proxy with a body limit, and the demo GIF.

**Goal:** a public demo that anyone can try.

- A production Dockerfile (backend plus built frontend) and a prebuilt sandbox image.
- A host that can start sandbox containers safely (the choice and its tradeoffs recorded in `docs/decisions.md`).
- CI: ruff, pytest, the frontend build, and a `docker compose up` smoke test (carried over from Phase 1).
- Request-size cap at the reverse proxy (uploads are size-checked only after the body is received).
- Sandbox on the deploy host: Docker access for the backend, a size-limited `/out` volume, and stronger isolation if the host offers it (gVisor or similar).
- Upload limits, rate limits, and automatic cleanup of old sessions' files.
- A README with an architecture diagram, a demo GIF, and the eval results.

**Done when:**
- [x] CI is green on main (run #5, commit 55e3eba: backend with real sandbox containers, frontend, and the production image + Compose smoke test)
- [ ] the public URL works end to end, and the sandbox limits have been checked on the deployed host
