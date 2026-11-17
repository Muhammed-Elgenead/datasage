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
