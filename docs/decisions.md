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
