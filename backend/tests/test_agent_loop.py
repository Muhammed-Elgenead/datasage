"""The agent loop through the Anthropic adapter, with a scripted client and sandbox."""

import json

from app.agents.loop import AgentState, Limits, run_agent
from app.agents.providers import FALLBACK_BETA, AnthropicProvider
from tests.fakes import TABLE, FakeLLM, FakeSandbox, error_run, ok_run, reply, text, tool

LIMITS = Limits(max_iterations=6, max_code_errors=2)
PROFILE = '[{"path": "/data/x.csv"}]'


async def go(
    llm: FakeLLM, sandbox: FakeSandbox, limits: Limits = LIMITS, fallbacks: str = "default"
) -> AgentState:
    provider = AnthropicProvider(llm, "test-model", "high", 16000, fallbacks)
    state = AgentState()
    await run_agent(provider, limits, "Total revenue by region?", PROFILE, sandbox, state)
    return state


def tool_results(state: AgentState, index: int) -> list[dict]:
    return state.history[index]["content"]


async def test_success_on_first_try() -> None:
    llm = FakeLLM(
        reply(tool("get_profile", "t1")),
        reply(tool("run_python", "t2", code="df.groupby('region').sum()")),
        reply(text("North leads with 100."), stop="end_turn"),
    )
    sandbox = FakeSandbox(ok_run(tables=[TABLE], charts=["chart_1.png"]))

    state = await go(llm, sandbox)

    assert state.status == "ok"
    assert state.answer == "North leads with 100."
    assert sandbox.codes == ["df.groupby('region').sum()"]
    assert [r.result.status for r in state.runs] == ["ok"]
    assert [m["role"] for m in state.history] == ["user", "assistant"] * 3
    assert tool_results(state, 2) == [
        {"type": "tool_result", "tool_use_id": "t1", "content": PROFILE}
    ]
    run_result = tool_results(state, 4)[0]
    assert "is_error" not in run_result
    assert json.loads(run_result["content"])["tables"][0]["rows"] == [["north", 100]]
    # thinking blocks go back exactly as returned
    assert state.history[1]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "sig",
    }
    assert state.usage == {
        "input_tokens": 30,
        "output_tokens": 15,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


async def test_request_params() -> None:
    llm = FakeLLM(reply(text("hi"), stop="end_turn"))
    await go(llm, FakeSandbox())
    [params] = llm.calls

    assert params["model"] == "test-model"
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "high"}
    assert params["cache_control"] == {"type": "ephemeral"}
    assert params["betas"] == [FALLBACK_BETA]
    assert params["fallbacks"] == "default"
    assert "tool_choice" not in params  # forcing a tool is a 400 on this model
    assert [t["name"] for t in params["tools"]] == ["get_profile", "run_python", "run_sql"]
    assert all(
        t["strict"] and t["input_schema"]["additionalProperties"] is False for t in params["tools"]
    )


async def test_fallbacks_can_be_turned_off() -> None:
    llm = FakeLLM(reply(text("hi"), stop="end_turn"))
    await go(llm, FakeSandbox(), fallbacks="")
    assert "betas" not in llm.calls[0]
    assert "fallbacks" not in llm.calls[0]


async def test_error_then_fix() -> None:
    llm = FakeLLM(
        reply(tool("run_python", "t1", code="df['nope']")),
        reply(tool("run_python", "t2", code="df['revenue'].sum()")),
        reply(text("Total is 350."), stop="end_turn"),
    )
    sandbox = FakeSandbox(
        error_run("r1", "Traceback ...\nKeyError: 'nope'"), ok_run("r2", result="350")
    )

    state = await go(llm, sandbox)

    assert state.status == "ok"
    assert [r.result.status for r in state.runs] == ["error", "ok"]
    failed = tool_results(state, 2)[0]
    assert failed["is_error"] is True
    assert "KeyError: 'nope'" in json.loads(failed["content"])["error"]
    # the model saw the traceback before writing the fix
    assert llm.calls[1]["messages"][-1]["content"][0]["is_error"] is True


async def test_retry_limit_stops_the_loop() -> None:
    llm = FakeLLM(
        reply(tool("run_python", "t1", code="bad 1")),
        reply(tool("run_python", "t2", code="bad 2")),
    )
    sandbox = FakeSandbox(error_run("r1"), error_run("r2"))

    state = await go(llm, sandbox)

    assert state.status == "code_failed"
    assert "2 failed attempts" in (state.answer or "")
    assert len(llm.calls) == 2  # no third API call
    assert llm.responses == []


async def test_timeouts_count_as_failures() -> None:
    from app.sandbox.client import SandboxResult

    llm = FakeLLM(
        reply(tool("run_python", "t1", code="while True: pass")),
        reply(tool("run_python", "t2", code="while True: pass")),
    )
    timeout = SandboxResult(run_id="r", status="timeout", error="Stopped after 30 seconds.")

    state = await go(llm, FakeSandbox(timeout, timeout))

    assert state.status == "code_failed"


async def test_max_iterations() -> None:
    llm = FakeLLM(*(reply(tool("get_profile", f"t{i}")) for i in range(3)))

    state = await go(llm, FakeSandbox(), Limits(max_iterations=3, max_code_errors=10))

    assert state.status == "max_iterations"
    assert len(llm.calls) == 3


async def test_parallel_calls_answered_in_one_message() -> None:
    llm = FakeLLM(
        reply(tool("get_profile", "t1"), tool("run_python", "t2", code="1")),
        reply(text("done"), stop="end_turn"),
    )
    state = await go(llm, FakeSandbox(ok_run()))

    results = tool_results(state, 2)
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]


async def test_unknown_tool_is_an_error_result() -> None:
    llm = FakeLLM(reply(tool("rm_rf", "t1")), reply(text("sorry"), stop="end_turn"))
    state = await go(llm, FakeSandbox())
    [result] = tool_results(state, 2)
    assert result["is_error"] is True
    assert state.status == "ok"


async def test_refusal() -> None:
    llm = FakeLLM(reply(stop="refusal"))
    state = await go(llm, FakeSandbox())
    assert state.status == "refusal"


async def test_max_tokens_never_runs_a_cut_off_tool_call() -> None:
    llm = FakeLLM(reply(tool("run_python", "t1", code="df.gro"), stop="max_tokens"))
    sandbox = FakeSandbox()
    state = await go(llm, sandbox)
    assert state.status == "incomplete"
    assert sandbox.codes == []
