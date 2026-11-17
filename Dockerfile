# Production image: the FastAPI backend serving the built frontend at "/" and the API at
# /api, plus the Docker CLI it uses to start sandbox containers on the host's Docker.
# Node and Docker Hub images can't be pulled in our region, so this builds in CI
# (docs/decisions.md); the Python base comes from GHCR.

FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM docker:29-cli AS docker-cli

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" \
    STATIC_DIR=/app/static SANDBOX_DOCKER=docker SANDBOX_WSL_PATHS=false
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY --from=frontend /frontend/dist ./static
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
# Runs as root: it holds the Docker socket, which is root-equivalent on the host anyway.
# The untrusted code runs in the sandbox containers, as uid 10001 with every limit.
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
