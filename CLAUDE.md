# DataSage — Project Rules

DataSage lets a user upload a CSV or Excel file and ask questions about it in plain English. An agent writes pandas code, runs it in an isolated sandbox, fixes its own errors, and returns the answer with tables, charts, and the code it used.

These rules are permanent. Follow them in every session.

## Stack

| Layer     | Choice |
|-----------|--------|
| Backend   | Python 3.12, FastAPI, Pydantic v2 |
| Database  | PostgreSQL 16, SQLAlchemy 2 (async), Alembic migrations |
| LLM       | Two adapters behind one hand-written tool-use loop: Google Gemini (`google-genai`, the default) and Anthropic (`anthropic`), both with native tool use. `LLM_PROVIDER` picks one. Model IDs and API keys come from env (`GEMINI_MODEL`/`GEMINI_API_KEY`, `ANTHROPIC_MODEL`/`ANTHROPIC_API_KEY`). Never hardcode either. SDK code lives only in `app/agents/providers.py`; the loop stays provider-neutral |
| Sandbox   | A separate, locked-down Docker container built from `sandbox-image/` |
| Frontend  | React + Vite + TypeScript + Tailwind |
| Runtime   | Docker Compose (postgres, backend, frontend; the backend starts sandbox containers) |

## Sandbox rules

Code written by the LLM or the user is untrusted. It runs **only** inside a sandbox container, never in the backend process. Every sandbox run must have:

- **No network** (`network_mode="none"`).
- **CPU and memory limits** (for example `nano_cpus`, `mem_limit`, and `pids_limit`), set from config.
- **A wall-clock timeout**. The container is killed and removed when it runs out.
- **A non-root user** (set in the image and in the run call), a read-only root filesystem, and dropped capabilities (`cap_drop=["ALL"]`, `no-new-privileges`).
- **Only the session's data folder mounted**: the input files read-only, plus one writable output folder. Never mount the backend code, `.env`, the Docker socket, or other sessions' folders.

Sandbox tests must prove each of these limits holds, for example that network calls fail, that runaway code hits the timeout, and that files outside the mount can't be read.

## Hard rules

1. **Use uv for ALL Python work**: `uv init`, `uv add`, `uv sync`, `uv run`. **Never use pip** (no `pip install`, no `uv pip`, no `requirements.txt`). This includes `sandbox-image/`: install its packages with uv inside the Dockerfile.
2. **Never write anything to the C: drive.** Everything lives under `E:\ai-agent-portfolio\datasage`.
   - Point the uv cache and Python installs at E:: `UV_CACHE_DIR=E:\ai-agent-portfolio\.uv-cache`, `UV_PYTHON_INSTALL_DIR=E:\ai-agent-portfolio\.uv-python`.
   - Point npm's cache at E: (`npm_config_cache`).
   - Put temp files and uploaded session data in gitignored folders inside the project (`.tmp/`, `data/`), not in system temp.
   - **Docker data stays on E: too.** Before any phase that builds or runs images, check Docker Desktop's disk image location: *Settings → Resources → Advanced → Disk image location*. It must point to E:. With the WSL 2 backend, `wsl -l -v` lists the `docker-desktop` distros, and the `.vhdx` files must live under that E: path.
3. **Secrets only in `.env`**, which is gitignored. Every variable in `.env` appears in `.env.example` with a placeholder value.
4. **Tests for everything.** Every feature ships with tests (`uv run pytest`). **Always mock the LLM in tests**: the test suite never calls the real API.
5. **Lint with ruff** (`uv run ruff check`, `uv run ruff format`). Code must be ruff-clean before a phase counts as done.
6. **Record decisions in `docs/decisions.md`**: one dated entry per decision, giving the context, the decision, and the alternatives considered.
7. **One phase at a time.** Work only on the current phase in `PLAN.md`. When its **Done when** list is met, **stop** and report. Don't start the next phase until asked.

## Layout

```
datasage/
├── backend/
│   ├── app/
│   │   ├── api/        # FastAPI routers, request/response schemas
│   │   ├── core/       # config (pydantic-settings), logging
│   │   ├── db/         # SQLAlchemy models, session, Alembic env
│   │   ├── services/   # uploads, profiling, storage, business logic
│   │   ├── agents/     # tool-use loop, tools, prompts
│   │   └── sandbox/    # client that starts and stops sandbox containers
│   └── tests/
├── sandbox-image/      # Dockerfile + runner for the locked-down pandas container
├── frontend/
│   └── src/
├── evals/
├── docs/
│   └── decisions.md
├── docker-compose.yml
├── .env.example
├── CLAUDE.md
└── PLAN.md
```

## Conventions

- Run all Python commands through uv: `uv run pytest`, `uv run alembic upgrade head`, and so on.
- `backend/` has its own `pyproject.toml` and `uv.lock`, and so does `evals/` if it needs one.
- Use async all the way down in the backend (async SQLAlchemy sessions, async Anthropic client).
- Tools are defined once, each with a Pydantic input schema that is turned into JSON Schema for the API.
- Every answer stores and returns the exact code that produced it.
