"""Sandbox command and output validation, without Docker."""

import json
import os
import uuid
from pathlib import Path

import pytest

from app.core.config import Settings
from app.sandbox.client import (
    PNG_MAGIC,
    SandboxUnavailable,
    build_run_args,
    host_path,
    read_output,
    run_code_sync,
)


@pytest.fixture
def s(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, sandbox_max_output_chars=10, sandbox_max_charts=2)


def flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_run_args_apply_every_limit(s: Settings, tmp_path: Path) -> None:
    args = build_run_args(s, "n", tmp_path / "sess", tmp_path / "sess" / "runs" / "r")

    assert flag_values(args, "--network") == ["none"]
    assert flag_values(args, "--user") == ["10001:10001"]
    assert flag_values(args, "--cap-drop") == ["ALL"]
    assert flag_values(args, "--security-opt") == ["no-new-privileges"]
    assert flag_values(args, "--memory") == flag_values(args, "--memory-swap") == ["512m"]
    assert flag_values(args, "--cpus") == ["1.0"]
    assert flag_values(args, "--pids-limit") == ["64"]
    assert "--read-only" in args
    mounts = flag_values(args, "--volume")
    assert len(mounts) == 2
    assert mounts[0].endswith(":/data:ro")
    assert mounts[1].endswith(":/out:rw")
    assert not any("docker.sock" in a for a in args)
    assert args[-1] == s.sandbox_image


def test_wsl_paths() -> None:
    s = Settings(sandbox_wsl_paths=True)
    assert host_path(Path(r"E:\ai\data\x"), s) == "/mnt/e/ai/data/x"


def write_result(out: Path, **fields: object) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(fields), encoding="utf-8")


def test_output_is_capped_and_charts_checked(s: Settings, tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_result(
        out,
        stdout="x" * 50,
        charts=["chart_1.png", "chart_2.png", "../secret.png", "chart_3.png"],
        tables=[{"columns": ["a"], "rows": [[1]] * 500, "total_rows": 500, "truncated": True}],
    )
    (out / "chart_1.png").write_bytes(PNG_MAGIC + b"data")
    (out / "chart_2.png").write_bytes(b"not a png")
    (out / "chart_3.png").write_bytes(PNG_MAGIC)  # beyond sandbox_max_charts

    result = read_output(out, s)

    assert not isinstance(result, str)
    assert result.stdout == "x" * 10
    assert result.charts == ["chart_1.png"]
    assert len(result.tables[0].rows) == s.sandbox_max_table_rows


@pytest.mark.parametrize(
    ("content", "reason"),
    [(None, "usable result"), ("{not json", "not valid"), ('{"tables": 5}', "not valid")],
)
def test_bad_result_files(s: Settings, tmp_path: Path, content: str | None, reason: str) -> None:
    out = tmp_path / "out"
    out.mkdir()
    if content is not None:
        (out / "result.json").write_text(content, encoding="utf-8")
    result = read_output(out, s)
    assert isinstance(result, str)
    assert reason in result


def test_oversized_result_is_rejected(tmp_path: Path) -> None:
    s = Settings(sandbox_max_result_kb=1)
    write_result(tmp_path / "out", stdout="x" * 2000)
    assert "too large" in read_output(tmp_path / "out", s)


def test_symlinked_result_is_not_followed(s: Settings, tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"stdout": "host secret"}), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    try:
        os.symlink(secret, out / "result.json")
    except OSError:
        pytest.skip("creating symlinks needs extra rights on this Windows account")
    assert isinstance(read_output(out, s), str)


def test_missing_docker_is_unavailable(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, sandbox_docker="no-such-docker-binary")
    with pytest.raises(SandboxUnavailable):
        run_code_sync(s, uuid.uuid4(), "1")
