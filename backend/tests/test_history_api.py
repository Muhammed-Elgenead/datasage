"""Session history, chart files, the SSE stream and restart cleanup (scripted LLM/sandbox)."""

import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.db.models import Question
from app.main import fail_interrupted_questions
from app.sandbox.client import PNG_MAGIC
from tests.conftest import requires_db
from tests.fakes import TABLE, FakeLLM, FakeSandbox, error_run, ok_run, reply, text, tool
from tests.test_ask_api import script

pytestmark = requires_db

RUN = "a" * 32


@pytest.fixture
async def sid(client: AsyncClient) -> str:
    sid = (await client.post("/sessions")).json()["id"]
    files = {"file": ("sales.csv", b"region,revenue\nnorth,100\nsouth,250\n")}
    assert (await client.post(f"/sessions/{sid}/datasets", files=files)).status_code == 201
    return sid


def answering_llm() -> FakeLLM:
    return FakeLLM(
        reply(tool("get_profile", "t1")),
        reply(tool("run_python", "t2", code="bad")),
        reply(tool("run_python", "t3", code="good")),
        reply(text("North leads."), stop="end_turn"),
    )


def answering_sandbox() -> FakeSandbox:
    return FakeSandbox(
        error_run("r1", "Traceback ...\nKeyError: 'x'"),
        ok_run(RUN, tables=[TABLE], charts=["chart_1.png"]),
    )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for frame in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def test_stream_reports_progress_then_the_answer(
    app: FastAPI, client: AsyncClient, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(app, monkeypatch, answering_llm(), answering_sandbox())

    resp = await client.post(f"/sessions/{sid}/ask/stream", json={"question": "Who leads?"})

    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    assert [e for e, _ in events] == [
        "thinking",
        "tool",
        "thinking",
        "code",
        "run",
        "thinking",
        "code",
        "run",
        "thinking",
        "answer",
    ]
    runs = [d for e, d in events if e == "run"]
    assert runs[0] == {
        "type": "run",
        "attempt": 1,
        "status": "error",
        "error": "KeyError: 'x'",
        "tables": 0,
        "charts": 0,
    }
    assert runs[1]["status"] == "ok"
    assert [d["code"] for e, d in events if e == "code"] == ["bad", "good"]
    answer = events[-1][1]
    assert (answer["answer"], answer["code"], answer["charts"]) == (
        "North leads.",
        "good",
        ["chart_1.png"],
    )


async def test_stream_errors_become_an_error_event(client: AsyncClient) -> None:
    resp = await client.post(f"/sessions/{uuid.uuid4()}/ask/stream", json={"question": "q"})
    assert parse_sse(resp.text) == [("error", {"status": 404, "detail": "Session not found"})]


async def test_history_list_and_detail(
    app: FastAPI, client: AsyncClient, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(app, monkeypatch, answering_llm(), answering_sandbox())
    await client.post(f"/sessions/{sid}/ask", json={"question": "Who leads?"})
    empty = (await client.post("/sessions")).json()["id"]

    listing = (await client.get("/sessions")).json()
    assert [s["id"] for s in listing] == [empty, sid]  # newest first
    assert listing[1] | {"created_at": None} == {
        "id": sid,
        "created_at": None,
        "files": ["sales.csv"],
        "first_question": "Who leads?",
        "questions": 1,
    }

    detail = (await client.get(f"/sessions/{sid}")).json()
    assert [d["filename"] for d in detail["datasets"]] == ["sales.csv"]
    assert detail["datasets"][0]["profile"]["row_count"] == 2
    [q] = detail["questions"]
    assert (q["question"], q["status"], q["code"], q["run_id"], q["attempts"]) == (
        "Who leads?",
        "ok",
        "good",
        RUN,
        2,
    )
    assert (await client.get(f"/sessions/{uuid.uuid4()}")).status_code == 404


def chart_dir(settings: Settings, sid: str) -> Path:
    folder = settings.data_dir / sid / "runs" / RUN
    folder.mkdir(parents=True)
    return folder


async def test_chart_is_served(client: AsyncClient, settings: Settings, sid: str) -> None:
    (chart_dir(settings, sid) / "chart_1.png").write_bytes(PNG_MAGIC + b"pixels")

    resp = await client.get(f"/sessions/{sid}/runs/{RUN}/chart_1.png")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == PNG_MAGIC + b"pixels"


@pytest.mark.parametrize(
    "path",
    [
        f"runs/{RUN}/chart_2.png",  # missing
        f"runs/{RUN}/code.py",  # not a chart name
        f"runs/{RUN}/notpng_1.png",
        "runs/..%2F..%2Fx/chart_1.png",  # traversal in run id
        f"runs/{'b' * 32}/chart_1.png",
    ],
)
async def test_bad_chart_paths_are_404(
    client: AsyncClient, settings: Settings, sid: str, path: str
) -> None:
    folder = chart_dir(settings, sid)
    (folder / "code.py").write_text("x")
    (folder / "chart_3.png").write_bytes(b"not a png")
    assert (await client.get(f"/sessions/{sid}/{path}")).status_code == 404


async def test_fake_png_is_404(client: AsyncClient, settings: Settings, sid: str) -> None:
    (chart_dir(settings, sid) / "chart_1.png").write_bytes(b"<script>alert(1)</script>")
    assert (await client.get(f"/sessions/{sid}/runs/{RUN}/chart_1.png")).status_code == 404


async def test_restart_marks_running_questions_failed(app: FastAPI, sid: str) -> None:
    async with app.state.db.sessions() as db:
        db.add(Question(session_id=uuid.UUID(sid), text="q", status="running", messages=[]))
        db.add(Question(session_id=uuid.UUID(sid), text="q2", status="ok", messages=[]))
        await db.commit()

    await fail_interrupted_questions(app.state.db)

    async with app.state.db.sessions() as db:
        from sqlalchemy import select

        rows = {q.text: (q.status, q.error) for q in await db.scalars(select(Question))}
    assert rows["q"][0] == "failed" and "restarted" in rows["q"][1]
    assert rows["q2"] == ("ok", None)
