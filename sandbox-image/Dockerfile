# Locked-down runtime for untrusted pandas code. The backend adds the runtime limits
# (no network, CPU/memory/pids, read-only root, dropped caps); see app/sandbox/client.py.
# GHCR base: Docker Hub is blocked in our region (docs/decisions.md).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" \
    MPLBACKEND=Agg PYTHONDONTWRITEBYTECODE=1

WORKDIR /runner
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project \
    && MPLCONFIGDIR=/opt/mplconfig python -c "import matplotlib.pyplot" \
    && chmod -R a+rX /opt/mplconfig
COPY run.py ./

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
USER 10001:10001
# /tmp is a small tmpfs at runtime (the root filesystem is read-only).
ENV MPLCONFIGDIR=/tmp/mpl HOME=/tmp
WORKDIR /data
ENTRYPOINT ["python", "/runner/run.py"]
