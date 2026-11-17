"""SQL mode with a scripted LLM and sandbox: run_sql, SQL errors and fixes, and the choice
between pandas and SQL (by the agent, or forced by the user)."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.agents.loop import AgentState, Limits, run_agent
from app.agents.providers import AnthropicProvider
from app.services.questions import sql_tables
from tests.conftest import requires_db
from tests.fakes import TABLE, FakeLLM, FakeSandbox, error_run, ok_run, reply, text, tool
from tests.test_ask_api import script, stored

SQL = "SELECT region, sum(revenue) AS total FROM sales GROUP BY region"


async def go(llm: FakeLLM, sandbox: FakeSandbox, mode: str = "auto") -> AgentState:
    provider = AnthropicProvider(llm, "test-model", "medium", 16000, "")
    state = AgentState()
    await run_agent(provider, Limits(6, 2), "Revenue by region?", "[]", sandbox, state, mode=mode)
    return state


def offered(llm: FakeLLM) -> list[str]:
    return [t["name"] for t in llm.calls[0]["tools"]]


async def test_sql_success() -> None:
    llm = FakeLLM(
        reply(tool("run_sql", "t1", query=SQL)), reply(text("South leads."), stop="end_turn")
    )
    sandbox = FakeSandbox(ok_run(tables=[TABLE]))

    state = await go(llm, sandbox)

    assert state.status == "ok"
    assert sandbox.codes == [SQL]
    assert sandbox.languages == ["sql"]
    assert state.runs[0].language == "sql"
    result = llm.calls[1]["messages"][-1]["content"][0]
    assert json.loads(result["content"])["tables"][0]["rows"] == [["north", 100]]


async def test_sql_error_then_fix() -> None:
    llm = FakeLLM(
        reply(tool("run_sql", "t1", query="SELECT revnue FROM sales")),
        reply(tool("run_sql", "t2", query=SQL)),
        reply(text("Fixed."), stop="end_turn"),
    )
    binder = 'BinderException: Binder Error: Referenced column "revnue" not found'
    sandbox = FakeSandbox(error_run("r1", binder), ok_run("r2", tables=[TABLE]))

    state = await go(llm, sandbox)

    assert state.status == "ok"
    assert [r.result.status for r in state.runs] == ["error", "ok"]
    failed = llm.calls[1]["messages"][-1]["content"][0]
    assert failed["is_error"] is True
    assert "revnue" in json.loads(failed["content"])["error"]


async def test_sql_failures_count_toward_the_retry_limit() -> None:
    llm = FakeLLM(
        reply(tool("run_sql", "t1", query="bad")), reply(tool("run_python", "t2", code="bad"))
    )
    state = await go(llm, FakeSandbox(error_run(), error_run()))
    assert state.status == "code_failed"


async def test_auto_mode_offers_both_and_the_agent_can_mix_them() -> None:
    llm = FakeLLM(
        reply(tool("run_sql", "t1", query=SQL)),
        reply(tool("run_python", "t2", code="df.plot()")),
        reply(text("Done."), stop="end_turn"),
    )
    sandbox = FakeSandbox(ok_run(tables=[TABLE]), ok_run(charts=["chart_1.png"]))

    state = await go(llm, sandbox)

    assert offered(llm) == ["get_profile", "run_python", "run_sql"]
    assert sandbox.languages == ["sql", "python"]
    assert "For this question" not in llm.calls[0]["system"]
    assert state.status == "ok"


@pytest.mark.parametrize(
    ("mode", "tools", "note"),
    [
        ("sql", ["get_profile", "run_sql"], "use run_sql only"),
        ("python", ["get_profile", "run_python"], "use run_python only"),
    ],
)
async def test_forced_mode_offers_one_language(mode: str, tools: list[str], note: str) -> None:
    llm = FakeLLM(reply(text("ok"), stop="end_turn"))
    await go(llm, FakeSandbox(), mode)
    assert offered(llm) == tools
    assert note in llm.calls[0]["system"]


async def test_tool_outside_the_forced_mode_is_refused_and_not_run() -> None:
    llm = FakeLLM(reply(tool("run_python", "t1", code="1")), reply(text("Sorry."), stop="end_turn"))
    sandbox = FakeSandbox()

    state = await go(llm, sandbox, mode="sql")

    assert sandbox.codes == []
    result = state.history[2]["content"][0]
    assert result["is_error"] is True
    assert "not available" in result["content"]


def test_sql_table_names() -> None:
    files = ["Q3 Sales.xlsx", "q3-sales.csv", "2026 report.csv", "日本.csv", "orders.csv"]
    datasets = [SimpleNamespace(filename=f, stored_name=f"id{i}.csv") for i, f in enumerate(files)]
    assert sql_tables(datasets) == {  # type: ignore[arg-type]
        "q3_sales": "/data/id0.csv",
        "q3_sales_2": "/data/id1.csv",
        "t_2026_report": "/data/id2.csv",
        "日本": "/data/id3.csv",
        "orders": "/data/id4.csv",
    }


@requires_db
async def test_sql_answer_through_the_api(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = (await client.post("/sessions")).json()["id"]
    files = {"file": ("Sales 2026.csv", b"region,revenue\nnorth,100\nsouth,250\n")}
    await client.post(f"/sessions/{sid}/datasets", files=files)
    llm = FakeLLM(
        reply(tool("get_profile", "t1")),
        reply(tool("run_sql", "t2", query=SQL)),
        reply(text("South leads."), stop="end_turn"),
    )
    sandbox = FakeSandbox(ok_run("r1", tables=[TABLE]))
    script(app, monkeypatch, llm, sandbox)

    resp = await client.post(f"/sessions/{sid}/ask", json={"question": "q", "mode": "sql"})

    body = resp.json()
    assert (body["status"], body["code"], body["language"]) == ("ok", SQL, "sql")
    [(table, path)] = sandbox.sql_tables.items()  # type: ignore[union-attr]
    assert table == "sales_2026" and path.startswith("/data/") and path.endswith(".csv")
    profile = json.loads(llm.calls[1]["messages"][2]["content"][0]["content"])
    assert profile[0]["sql_table"] == "sales_2026"
    assert [t["name"] for t in llm.calls[0]["tools"]] == ["get_profile", "run_sql"]
    _, runs = await stored(app)
    assert [(r.language, r.code) for r in runs] == [("sql", SQL)]


@requires_db
async def test_bad_mode_is_rejected(client: AsyncClient) -> None:
    sid = (await client.post("/sessions")).json()["id"]
    resp = await client.post(f"/sessions/{sid}/ask", json={"question": "q", "mode": "rust"})
    assert resp.status_code == 422
