"""POST /sessions/{id}/ask with a scripted LLM and sandbox, stored in the test database."""

import uuid

import anthropic
import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.agents.providers import AnthropicProvider, LLMNotConfigured
from app.db.models import CodeRun, Question
from app.sandbox.client import SandboxUnavailable
from app.services import questions
from tests.conftest import requires_db
from tests.fakes import TABLE, FakeLLM, FakeSandbox, error_run, ok_run, reply, text, tool

pytestmark = requires_db


@pytest.fixture
async def sid(client: AsyncClient) -> str:
    sid = (await client.post("/sessions")).json()["id"]
    files = {"file": ("sales.csv", b"region,revenue\nnorth,100\nsouth,250\n")}
    assert (await client.post(f"/sessions/{sid}/datasets", files=files)).status_code == 201
    return sid


def script(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, llm: FakeLLM, sandbox: FakeSandbox
) -> None:
    app.state.llm = AnthropicProvider(llm, "test-model", "medium", 16000, "default")

    async def fake_run_code(settings, session_id, code):  # type: ignore[no-untyped-def]
        return await sandbox(code)

    monkeypatch.setattr(questions, "run_code", fake_run_code)


async def stored(app: FastAPI) -> tuple[Question, list[CodeRun]]:
    async with app.state.db.sessions() as db:
        q = (await db.scalars(select(Question))).one()
        runs = list(await db.scalars(select(CodeRun).order_by(CodeRun.seq)))
    return q, runs


async def ask(client: AsyncClient, sid: str):  # type: ignore[no-untyped-def]
    return await client.post(f"/sessions/{sid}/ask", json={"question": "Revenue by region?"})


async def test_answer_with_fixed_code(
    app: FastAPI, client: AsyncClient, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM(
        reply(tool("get_profile", "t1")),
        reply(tool("run_python", "t2", code="bad")),
        reply(tool("run_python", "t3", code="good")),
        reply(text("South leads."), stop="end_turn"),
    )
    script(
        app,
        monkeypatch,
        llm,
        FakeSandbox(error_run("r1"), ok_run("r2", tables=[TABLE], charts=["chart_1.png"])),
    )

    resp = await ask(client, sid)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["answer"] == "South leads."
    assert body["code"] == "good"
    assert body["run_id"] == "r2"
    assert body["code_error"] is None
    assert body["tables"][0]["rows"] == [["north", 100]]
    assert body["charts"] == ["chart_1.png"]
    assert body["attempts"] == 2
    # the profile the model got names the stored file path, not the user's filename
    profile = llm.calls[1]["messages"][2]["content"][0]["content"]
    assert '"/data/' in profile and '"uploaded_as": "sales.csv"' in profile

    q, runs = await stored(app)
    assert q.status == "ok"
    assert q.finished_at is not None
    assert len(q.messages) == 8  # question, 3 tool rounds (call + result), final answer
    assert [(r.id, r.code, r.status) for r in runs] == [
        ("r1", "bad", "error"),
        ("r2", "good", "ok"),
    ]
    assert runs[0].result["error"] == "KeyError: 'x'"


async def test_code_failed_shows_last_attempt(
    app: FastAPI, client: AsyncClient, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM(*(reply(tool("run_python", f"t{i}", code=f"bad {i}")) for i in range(3)))
    script(app, monkeypatch, llm, FakeSandbox(*(error_run(f"r{i}") for i in range(3))))

    body = (await ask(client, sid)).json()

    assert body["status"] == "code_failed"
    assert body["code"] == "bad 2"
    assert body["code_error"] == "KeyError: 'x'"


async def test_api_error_is_stored_as_failed(
    app: FastAPI, client: AsyncClient, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.InternalServerError(
        "boom", response=httpx.Response(500, request=request), body=None
    )
    script(app, monkeypatch, FakeLLM(reply(tool("get_profile", "t1")), err), FakeSandbox())

    resp = await ask(client, sid)

    assert resp.status_code == 502
    q, _ = await stored(app)
    assert q.status == "failed"
    assert "Anthropic API error" in (q.error or "")
    assert len(q.messages) == 3  # what happened before the error is kept


async def test_sandbox_down_is_503(
    app: FastAPI, client: AsyncClient, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM(reply(tool("run_python", "t1", code="1")))
    script(app, monkeypatch, llm, FakeSandbox(SandboxUnavailable("docker is not running")))

    resp = await ask(client, sid)

    assert resp.status_code == 503
    assert "sandbox" in resp.json()["detail"]
    assert (await stored(app))[0].status == "failed"


async def test_needs_a_dataset(client: AsyncClient) -> None:
    sid = (await client.post("/sessions")).json()["id"]
    assert (await ask(client, sid)).status_code == 422


async def test_unknown_session(client: AsyncClient) -> None:
    assert (await ask(client, str(uuid.uuid4()))).status_code == 404


async def test_model_not_configured(app: FastAPI, client: AsyncClient, sid: str) -> None:
    app.state.llm = LLMNotConfigured("The LLM is not configured: set GEMINI_MODEL")
    resp = await ask(client, sid)
    assert resp.status_code == 503
    assert "GEMINI_MODEL" in resp.json()["detail"]


async def test_empty_question_rejected(client: AsyncClient, sid: str) -> None:
    resp = await client.post(f"/sessions/{sid}/ask", json={"question": ""})
    assert resp.status_code == 422
