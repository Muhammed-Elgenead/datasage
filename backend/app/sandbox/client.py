"""Run untrusted Python in a locked-down, throwaway Docker container.

Each run gets its own folder, <data_dir>/<session_id>/runs/<run_id>/, mounted writable at
/out. The session folder is mounted read-only at /data. Nothing else from the host is
mounted. Everything the container writes is untrusted and is validated in `read_output`.
"""

import asyncio
import contextlib
import json
import os
import re
import shlex
import stat
import subprocess
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.core.config import Settings

CONTAINER_PREFIX = "datasage-sbx-"
CHART_NAME = re.compile(r"chart_\d{1,3}\.png")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
OOM_EXIT = 137  # SIGKILL: the OOM killer (or a kill from outside)


class SandboxUnavailable(Exception):
    """Docker or the sandbox image isn't usable: an infrastructure problem, not a code error."""


class Table(BaseModel):
    title: str | None = None
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int
    truncated: bool


class RunnerOutput(BaseModel):
    """The shape of result.json as the runner writes it."""

    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    result: str | None = None
    tables: list[Table] = []
    charts: list[str] = []
    notes: list[str] = []
    duration_ms: int | None = None


class SandboxResult(RunnerOutput):
    run_id: str
    status: Literal["ok", "error", "timeout", "oom", "crashed"]
    charts: list[str] = []  # file names inside the run folder, already checked as PNG


def host_path(path: Path, settings: Settings) -> str:
    """The path as the Docker daemon sees it."""
    if not settings.sandbox_wsl_paths:
        return str(path.resolve())
    p = PureWindowsPath(path.resolve())
    return "/mnt/" + p.drive[0].lower() + "/" + "/".join(p.parts[1:])


def build_run_args(settings: Settings, name: str, session_dir: Path, out_dir: Path) -> list[str]:
    """`docker run` arguments: every limit from the CLAUDE.md sandbox rules."""
    s = settings
    return [
        "run",
        "--detach",
        "--pull", "never",
        "--name", name,
        "--network", "none",
        "--user", "10001:10001",
        "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size={s.sandbox_tmpfs_mb}m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", f"{s.sandbox_memory_mb}m",
        "--memory-swap", f"{s.sandbox_memory_mb}m",  # same as --memory: no swap
        "--cpus", str(s.sandbox_cpus),
        "--pids-limit", str(s.sandbox_pids),
        "--ulimit", f"fsize={s.sandbox_max_file_mb * 1024 * 1024}",
        "--log-driver", "none",  # code output goes to result.json, never to the backend
        "--volume", f"{host_path(session_dir, s)}:/data:ro",
        # shortcut: fsize caps each file but not how many, so /out can grow until the timeout;
        # upgrade to a size-limited volume before the public deploy (Phase 7)
        "--volume", f"{host_path(out_dir, s)}:/out:rw",
        "--env", f"MAX_OUTPUT_CHARS={s.sandbox_max_output_chars}",
        "--env", f"MAX_TABLE_ROWS={s.sandbox_max_table_rows}",
        "--env", f"MAX_TABLES={s.sandbox_max_tables}",
        "--env", f"MAX_CHARTS={s.sandbox_max_charts}",
        s.sandbox_image,
    ]  # fmt: skip


def read_plain_file(out_dir: Path, name: str, max_bytes: int) -> bytes | None:
    """The bytes of a plain file directly inside out_dir, or None when it is missing, too
    large, or not a plain file. The code can plant symlinks (seen on Windows as unreadable
    reparse points), so links are never followed."""
    path = out_dir / name
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or getattr(st, "st_reparse_tag", 0):
            return None
        if st.st_size > max_bytes:
            return None
        return path.read_bytes()
    except OSError:
        return None


def read_output(out_dir: Path, settings: Settings) -> RunnerOutput | str:
    """The validated runner output, or the reason it can't be used."""
    raw = read_plain_file(out_dir, "result.json", settings.sandbox_max_result_kb * 1024)
    if raw is None:
        return "the code ended without a usable result (missing, too large, or not a file)"
    try:
        out = RunnerOutput.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return "the result file is not valid"

    cap = settings.sandbox_max_output_chars
    charts = []
    for name in out.charts[: settings.sandbox_max_charts]:
        if not CHART_NAME.fullmatch(name):
            continue
        png = read_plain_file(out_dir, name, settings.sandbox_max_chart_kb * 1024)
        if png is not None and png.startswith(PNG_MAGIC):
            charts.append(name)
    return out.model_copy(
        update={
            "stdout": out.stdout[:cap],
            "stderr": out.stderr[:cap],
            "error": out.error[-cap:] if out.error else None,
            "result": out.result[:cap] if out.result else None,
            "tables": [
                t.model_copy(update={"rows": t.rows[: settings.sandbox_max_table_rows]})
                for t in out.tables[: settings.sandbox_max_tables]
            ],
            "charts": charts,
        }
    )


def _docker(
    settings: Settings, *args: str, timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*shlex.split(settings.sandbox_docker), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except OSError as exc:  # the docker command itself is missing
        raise SandboxUnavailable(f"cannot run {settings.sandbox_docker!r}: {exc}") from exc


def run_code_sync(settings: Settings, session_id: uuid.UUID, code: str) -> SandboxResult:
    run_id = uuid.uuid4().hex
    session_dir = settings.data_dir / str(session_id)
    out_dir = session_dir / "runs" / run_id
    out_dir.mkdir(parents=True)
    os.chmod(out_dir, 0o777)  # the sandbox user (uid 10001) must be able to write here
    (out_dir / "code.py").write_text(code, encoding="utf-8")
    name = CONTAINER_PREFIX + run_id

    def done(status: str, **fields: Any) -> SandboxResult:
        return SandboxResult(run_id=run_id, status=status, **fields)  # type: ignore[arg-type]

    try:
        started = _docker(settings, *build_run_args(settings, name, session_dir, out_dir))
        if started.returncode != 0:
            raise SandboxUnavailable(started.stderr.strip()[:500] or "docker run failed")
        try:
            waited = _docker(settings, "wait", name, timeout=settings.sandbox_timeout_seconds)
        except subprocess.TimeoutExpired:
            return done(
                "timeout", error=f"Stopped after {settings.sandbox_timeout_seconds:g} seconds."
            )
        exit_code = int(waited.stdout.strip() or -1)
        if exit_code == OOM_EXIT:
            oom = _docker(settings, "inspect", "-f", "{{.State.OOMKilled}}", name)
            if oom.stdout.strip() == "true":
                return done(
                    "oom", error=f"Ran out of memory (limit {settings.sandbox_memory_mb} MB)."
                )
        output = read_output(out_dir, settings)
        if isinstance(output, str):
            return done("crashed", error=f"Sandbox failed (exit code {exit_code}): {output}.")
        return done("error" if output.error else "ok", **output.model_dump())
    finally:
        # Always remove the container, including after a timeout (rm -f kills it first).
        with contextlib.suppress(SandboxUnavailable, subprocess.TimeoutExpired):
            _docker(settings, "rm", "--force", name)


async def run_code(settings: Settings, session_id: uuid.UUID, code: str) -> SandboxResult:
    """Run `code` against the session's files. Raises SandboxUnavailable for Docker problems;
    problems in the code itself come back as the result's status."""
    return await asyncio.to_thread(run_code_sync, settings, session_id, code)
