# Decisions

Newest at the bottom. Format: context → decision → alternatives considered.

---

## 2026-11-17 — Project rules and build plan

- **Context:** Portfolio project. An agent writes pandas code for a user's CSV/Excel file and runs it in a sandbox.
- **Decision:** Rules in `CLAUDE.md`, 7 phases in `PLAN.md`. LLM code only runs in a locked-down Docker container.
- **Alternatives:** Running code in-process with restricted builtins (not a real security boundary); hosted code interpreters (hide the sandbox we want to show).

## 2026-11-17 — Local dev without Docker Compose; Postgres 16 native in WSL

- **Context:** Same block as TaskPilot (see its `docs/decisions.md`): Docker Desktop's download and Docker Hub image layers are blocked in our region. GHCR pulls work. We don't route around the block.
- **Decision:** Reuse the existing WSL 2 distro `Ubuntu-24.04` (disk at `E:\wsl\Ubuntu-24.04`) and its native PostgreSQL 16. DataSage has its own role and databases: `datasage` (dev) and `datasage_test` (tests; DB tests are skipped when `TEST_DATABASE_URL` is unset). `scripts/db.ps1` keeps WSL alive, and `scripts/dev.ps1` starts the whole stack. `docker-compose.yml` stays as the reference runtime and is verified in CI (Phase 7). The backend image uses the GHCR `astral-sh/uv` base, so it builds locally, and it has been verified to build and run as non-root in WSL's Docker Engine.
- **Docker on E::** There is no Docker Desktop. Docker Engine runs inside the WSL distro, so `/var/lib/docker` lives in the distro's `.vhdx` on E:. The engine is disabled by default and started only when needed (`systemctl start docker`).
- **Alternatives:** Custom Postgres/Node images on GHCR bases (not official images, and more upkeep); Podman or Rancher Desktop (same block, and they install to C:).

## 2026-11-17 — Upload storage and validation

- **Context:** Uploaded files are untrusted, and later the sandbox will mount a session's folder.
- **Decision:**
  - Files live at `<DATA_DIR>/<session_id>/<dataset_id>.<csv|xlsx>`. The user's filename is stored only in the DB, for display, so path tricks like `../` can't reach the filesystem.
  - Only `.csv` and `.xlsx` are accepted (415 otherwise). Uploads over `MAX_UPLOAD_MB` get 413, empty files 422, and files pandas can't parse 422. On any failure the stored file is deleted.
  - `defusedxml` is installed, so openpyxl parses XLSX XML safely.
  - Parsing runs in a worker thread so it doesn't block the event loop.
  - The size check happens after the body is received. A hard request cap belongs at the reverse proxy (Phase 7).
- **Alternatives:** Keeping the original filenames on disk (path-traversal risk); storing files in Postgres (the sandbox needs a folder to mount).

## 2026-11-17 — Profile format

- **Context:** The profile is shown in the UI and will be given to the LLM in Phase 3.
- **Decision:** The profile holds `row_count`, `column_count`, `columns[]` (name, dtype, nulls, unique, and `stats` from `describe(include="all")`) and `sample` (the first `PROFILE_SAMPLE_ROWS` rows). Everything goes through pandas `to_json`, so NaN becomes null and dates become ISO strings, which keeps it JSONB-safe. It's stored as JSONB. CSV reading assumes commas and replaces undecodable bytes. XLSX reading uses the first sheet only.
- **Alternatives:** ydata-profiling (heavy, with a lot of HTML we don't need); delimiter sniffing (it misfires on single-column files; we'll revisit if users need it).

## 2026-11-17 — Anthropic SDK deferred to Phase 3

- **Context:** Phase 1 makes no LLM calls.
- **Decision:** Don't add `anthropic` until Phase 3 needs it.
- **Alternatives:** Adding it now (an unused dependency).

## 2026-11-17 — Sandbox runs through the Docker CLI (WSL locally)

- **Context:** The backend runs on Windows, but Docker Engine runs inside WSL. Code must run in a throwaway, locked-down container.
- **Decision:**
  - The backend shells out to the Docker CLI, as set by `SANDBOX_DOCKER`. Locally that is `wsl -d Ubuntu-24.04 -u root -e docker` (about 0.7 s per call). `SANDBOX_WSL_PATHS=true` turns `E:\…` bind-mount paths into `/mnt/e/…`.
  - `scripts/sandbox.ps1 build` starts Docker in WSL and builds `datasage-sandbox:latest` with the legacy builder (buildx isn't installed). `scripts/dev.ps1` starts Docker.
  - **One container per run:** `docker run --detach`, then `docker wait` with the timeout, then `docker rm --force` in a `finally` block, so it also runs after a timeout. Exit code 137 is checked with `docker inspect` to tell an OOM kill apart.
  - **Limits per run:** `--network none`; uid 10001; `--read-only` with a 64 MB `noexec` tmpfs at `/tmp`; `--cap-drop ALL`; `no-new-privileges`; memory equal to memory-swap (no swap); `--cpus`; `--pids-limit`; `--ulimit fsize`; `--pull never`. Mounts: the session folder read-only at `/data`, and the run's own folder `runs/<run_id>/` read-write at `/out`. Nothing else is mounted.
  - **`--log-driver none`, and the CLI never attaches**, so code can't flood the backend through stdout. Everything comes back through `/out/result.json`.
  - **Everything in `/out` is untrusted:** the result size is capped, it's validated with Pydantic, and stdout, tables and charts are capped again. Chart names must match `chart_N.png`, files must start with the PNG magic bytes, and only plain files are read (`lstat`). Symlinks and Windows reparse points are refused, because code can plant links to host paths.
  - **The runner** (`sandbox-image/run.py`) preloads `pd`, `np` and `plt`, plus `show(df)` and `DATA_DIR`. The last expression's value is returned, as in a notebook. Tracebacks show only the code's own frames, with source lines, so the model can fix its error.
- **Alternatives:**
  - Docker API over TCP from Windows: unauthenticated, so any local process would get root in WSL.
  - The docker-py SDK: it needs that same socket or TCP access.
  - Passing results over stdout: the code can write to fd 1 directly and flood the backend.
  - gVisor/Firecracker: stronger isolation, but not available in this WSL setup. We'll reconsider for the Phase 7 host.
- **Known limit:** `fsize` caps each file but not how many files, so `/out` can grow until the timeout. A size-limited volume is needed before the public deploy (Phase 7).

## 2026-11-17 — Agent loop with self-correction (Phase 3)

- **Context:** The agent writes pandas code, runs it in the sandbox, and fixes its own errors. We need to count failures, store every step, and later stream progress (Phase 4).
- **Decision:**
  - **A hand-written loop** (`app/agents/loop.py`) on `AsyncAnthropic.beta.messages.create`, with `anthropic` 2.9.0.
  - **Request settings:** model from `ANTHROPIC_MODEL` (default `claude-opus-5-5` in `.env.example`); `thinking: adaptive`; `output_config.effort` set explicitly (`ANTHROPIC_EFFORT`, `medium`), because Opus 5.5 defaults to `medium` and can't turn thinking off; top-level `cache_control: ephemeral`, since every iteration re-sends the history; server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`), turned off by `ANTHROPIC_FALLBACKS=`; no `tool_choice`, because forcing a tool is a 400 on this model.
  - **Two strict tools, sorted by name:** `get_profile()` returns each file's `/data/<stored_name>` path, original name and profile; `run_python(code)` uses the sandbox.
  - **Stop reasons are checked before any tool runs.** `max_tokens` ends as `incomplete` without running a possibly cut-off call. `refusal` becomes status `refusal`. `pause_turn` is re-sent.
  - **Self-correction:** a run whose status isn't `ok` (error, timeout, OOM, crash) returns as an `is_error` tool result with the traceback. After `AGENT_MAX_CODE_ERRORS` (3) failed runs per question, the loop stops as `code_failed`. `AGENT_MAX_ITERATIONS` (12) caps API calls.
  - **Sandbox runs are sequential**, even when the model calls tools in parallel, because each run is a container. All results go back in one user message.
  - **What the user sees:** the answer text, plus the tables, charts and code of the last *successful* run. If none succeeded, they see the last attempt and its error. The system prompt tells the model this, so its final run produces what supports the answer.
  - **The model sees** the first 30 rows of each table. Charts are reported only by name; the model doesn't see the images.
  - **Storage:** a `questions` row is created as `running`. When the loop finishes, even if it raised, the row gets its status, answer, error, usage and the full API transcript (`messages` JSONB: every message, tool call and thinking block, exactly as returned). Each sandbox run gets a `code_runs` row (code, status, validated result).
  - **API errors** map to 429 (rate limit), 504 (connection) or 502 (others). A down sandbox gives 503. A missing `ANTHROPIC_MODEL` gives 503, and the app still starts.
  - **Each question is its own conversation**; earlier questions in the session aren't sent.
- **Alternatives:**
  - The SDK Tool Runner: it handles the loop, but it's a beta helper, and counting failed runs, storing each step and streaming steps would all go through its hooks. A ~100-line loop is clearer.
  - Sending chart images back to the model: better self-checks of charts, but more tokens. Revisit with evals (Phase 6).
  - Carrying earlier questions as context: useful for follow-ups, but outside this phase's scope.

## 2026-11-17 — Provider adapters; Gemini as the default LLM

- **Context:** There's no Anthropic API key; there is a Gemini key. The Phase 3 loop was written directly against the Anthropic API.
- **Decision:**
  - The loop (`app/agents/loop.py`) is provider-neutral. It talks to an `LLMProvider` (`app/agents/providers.py`), which owns the native history format and normalizes each response into a `ModelTurn`: text, tool calls, and one of six stop kinds (end, tool_use, pause, refusal, max_tokens, other). Tools are defined once as `ToolSpec` (name, description, JSON Schema).
  - Adapters raise `LLMError` with the HTTP status `/ask` should return (429 rate limit, 504 connection, 502 otherwise), so errors are mapped once.
  - **Gemini** (`google-genai` 3.4) is the default (`LLM_PROVIDER=gemini`):
    - Automatic function calling is disabled, so only our loop runs tools.
    - Schemas go in `parameters_json_schema`.
    - Model turns are appended unchanged, which keeps thought signatures.
    - Results are sent as `{"output": …}` or `{"error": …}`, all in one user turn.
    - A missing call id gets a local id that is never sent back.
    - Finish reasons map to stop kinds the same way as in TaskPilot.
    - The stored transcript is `model_dump(mode="json")`, so signatures are base64-encoded and nothing is lost.
  - **Anthropic** keeps every Phase 3 request setting unchanged.
  - `create_provider` raises `LLMNotConfigured` when the chosen provider lacks its key or model. The app still starts, and `/ask` returns 503 with the reason.
- **Alternatives:** Gemini only (drops a tested path); LiteLLM or another multi-provider library (hides the loop and the native formats this project means to show).

## 2026-11-17 — Default Gemini model, and checks against silent data loss

- **Context:** Real-API runs on a messy CSV. With `gemini-3.6-flash`, a single question took more than 5 minutes; the model was careful but slow. `gemini-3.5-flash-lite` answered in 15–80 s. On the first flash-lite run, though, the code used `pd.to_datetime(..., errors="coerce")`, which silently dropped 160 of 240 rows. The status was still `ok` and the numbers were wrong.
- **Decision:**
  - `GEMINI_MODEL=gemini-3.5-flash-lite` (the user's choice).
  - The system prompt now tells the model to print how many values became null or were dropped after cleaning or parsing, and never to let `errors="coerce"` or `dropna` hide rows. With that change, no rows are lost. One subtler error remains: `format="mixed", dayfirst=True` swaps day and month on some ISO dates.
  - Answer quality on messy data is measured in Phase 6 with known answers, rather than fixed with more prompt rules now.
- **Alternatives:** keep `gemini-3.6-flash` (more careful, but too slow for an interactive app); add a rule for each data trap to the prompt (overfits to the test file; evals should drive such changes).

## 2026-11-17 — Frontend and live progress (Phase 4)

- **Context:** The UI needs upload, data profiles, a chat with tables, charts and code, live progress while the agent works, and session history.
- **Decision:**
  - **`POST /sessions/{id}/ask/stream`** sends Server-Sent Events over POST; the browser reads them with `fetch` and the SSE parser from TaskPilot. Events: `thinking` (one per model call), `tool` (`get_profile`), `code` (each attempt's code), and `run` (status plus the last line of the error), ending with one `answer` (the same shape as `/ask`) or `error` (`status`, `detail`). A comment keep-alive goes out every 15 s.
  - **The run lives in its own task, not tied to the connection.** If the browser leaves, the question still finishes and is saved. TaskPilot cancels instead, because its runs can wait on approvals.
  - **History:** `GET /sessions` returns the 50 newest sessions, each with its file names, first question and question count. `GET /sessions/{id}` returns the datasets (with profiles) and every question as an `AnswerOut`. After each answer or error, the UI reloads the session, so it always shows what was saved.
  - **Charts:** `GET /sessions/{id}/runs/{run_id}/{chart}` checks the run id (32 hex characters) and the chart name, reads only a plain file (no symlinks), and serves it only if it starts with the PNG magic bytes. Anything else gets 404.
  - **At startup,** questions still `running` are marked `failed` ("Interrupted: the server restarted…"). This assumes a single backend process, as noted in the code.
  - **Answers are plain text.** The prompt asks for no Markdown and the UI shows answers with `whitespace-pre-wrap`, so there's no Markdown renderer dependency.
- **Alternatives:**
  - `EventSource`: it supports GET only, and the question would have to go in the URL.
  - WebSockets: two-way messaging isn't needed.
  - Polling the questions table: slower and more code.
  - `react-markdown`: a dependency we don't need once answers are plain text.

## 2026-11-17 — SQL mode with DuckDB in the sandbox (Phase 5)

- **Context:** Users can ask the same questions answered with SQL. The SQL must run with exactly the same isolation as Python.
- **Decision:**
  - **DuckDB 2.0 inside the existing sandbox image**, with no database server. The backend writes `query.sql` and `tables.json` (table name → `/data/<file>`) into the run folder instead of `code.py`, so the `docker run` arguments are identical for both languages (a unit test compares them).
  - **Each file is loaded with pandas** (the same reading rules as profiling) and registered as a DuckDB table. Then `enable_external_access = false` and `lock_configuration = true` are set, so a query can see only those tables: no `read_csv`, `glob`, `COPY … TO`, `ATTACH`, or `INSTALL`, and it can't turn access back on. Real-container tests try each of these.
  - **Table names come from the original filename** (`Q3 Sales.xlsx` → `q3_sales`, with `t_` prefixed if it starts with a digit and `_2` added for duplicates). `get_profile` shows them as `sql_table`.
  - **A `run_sql(query)` tool** sits next to `run_python`. SQL errors come back as `ExceptionType: message`, with no Python frames. Failed SQL runs count toward the same retry limit.
  - **Choosing a language:** `AskIn.mode` is `auto`, `python` or `sql`. Auto offers both tools, and the prompt suggests SQL for clean filtering, grouping and joins, and pandas for messy cleaning and every chart. A forced mode offers only that tool, adds a one-line note to the system prompt, and refuses any other tool as an `is_error` result. Each stored run records its `language` (migration 0003), and the answer reports which language its `code` is in.
  - **The frontend** has an "Answer with: Auto / Python / SQL" choice above the question box, and labels each answer "SQL used" or "Code used" and each progress step "Running SQL" or "Running code".
  - **Startup** removes `datasage-sbx-*` containers left by a backend that died mid-run (found during this phase: one left over from a killed Phase 3 run). This assumes one backend per Docker host, as noted in the code.
- **Alternatives:**
  - DuckDB reading the files directly (`read_csv_auto`): it would need file access in SQL, the XLSX extension needs network to install, and the types could differ from the profile.
  - SQLite: weaker analytics SQL and no DataFrame registration.
  - A single "language" argument on one tool: two tools give the model clearer descriptions and let a forced mode simply remove one.

## 2026-11-17 — Evals (Phase 6)

- **Context:** Phase 3 showed that `ok` doesn't mean correct (silently dropped rows, swapped day and month). We need answers checked against known results, and regressions made visible.
- **Decision:**
  - **`evals/make_datasets.py`** generates 4 datasets with fixed seeds, plus `evals/cases.json` (34 cases). The correct answers are computed there with careful pandas, never by the agent. The datasets:
    - `sales`: text prices and mixed date formats, including the Phase 3 trap
    - `employees`: a clean XLSX file
    - `weather`: missing values
    - `shop`: two CSVs to join, with customers who never ordered
  - Four cases force SQL mode, and four ask for a chart.
  - **`python -m evals.run`** (one command, run in the backend's uv environment) calls `run_agent` directly with the real provider and real sandbox. Each case gets a fresh session folder whose files are stored under ids, as uploads are. Using no API and no Postgres keeps the evals about the agent. Cases run 3 at a time.
  - **Scoring is deterministic** (`evals/scoring.py`, unit-tested):
    - expected numbers must appear in the answer text or a result-table cell, within an absolute or relative tolerance
    - expected rows must appear in the same table row as their key (or on one line of text)
    - text must appear in the answer
    - a chart must exist when one was asked for
    - the run's status must be `ok`
  - **Each report** records per case: pass/fail and why, status, runs and failed runs (self-corrections), languages used, latency, tokens, the answer and the code.
  - **Reports** go to `docs/evals/<UTC time>-<model>.{json,md}`. The Markdown compares with the previous JSON report: the pass-rate change, regressions (passed before, fail now) and fixes, listed by case.
- **Alternatives:**
  - An LLM judge: it handles free-form answers, but costs more, isn't deterministic, and needs evals of its own. "Appears somewhere" can pass by coincidence; that limit is noted in the code.
  - Evals through the HTTP API: slower, and they would test the API and database too.
  - Hand-written answers: error-prone; generating them alongside the data keeps them in sync.
- **Follow-up (same day): API outages aren't agent failures.** A second back-to-back full run hit Gemini's rate limit, and 17 cases "regressed". The runner now:
  - reports the API or sandbox error as the case's reason
  - counts those cases as `errored`
  - keeps them out of regressions and fixes, listing them as "not comparable"
  - retries a rate-limited case once after 60 s
  - runs 2 cases at a time (down from 3)

  That polluted report was deleted instead of committed, because it measured the outage, not the agent. The same run also exposed a backend bug: a slow `docker run` raised an uncaught `TimeoutExpired`, which would have been a 500 in the app. Starting the sandbox now gets 90 s, and a slow start becomes `SandboxUnavailable` (503).
