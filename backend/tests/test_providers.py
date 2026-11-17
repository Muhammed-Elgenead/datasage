"""The Gemini adapter (scripted genai client) and provider selection. No API calls."""

import copy
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors, types
from pydantic import SecretStr

from app.agents.loop import TOOLS, AgentState, Limits, run_agent
from app.agents.providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMError,
    LLMNotConfigured,
    ToolCall,
    ToolResult,
    create_provider,
)
from app.core.config import Settings
from tests.fakes import TABLE, FakeSandbox, ok_run


def response(*parts: dict[str, Any], finish: str = "STOP") -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {"content": {"role": "model", "parts": list(parts)}, "finish_reason": finish}
            ],
            "usage_metadata": {
                "prompt_token_count": 10,
                "candidates_token_count": 3,
                "thoughts_token_count": 4,
            },
        }
    )


def call(name: str, id: str | None = None, **args: Any) -> dict[str, Any]:
    return {"function_call": {"name": name, "args": args, **({"id": id} if id else {})}}


THOUGHT = {"text": "thinking...", "thought": True, "thought_signature": "c2ln"}  # b"sig"


class FakeGenai:
    def __init__(self, *responses: types.GenerateContentResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append({**kwargs, "contents": copy.deepcopy(kwargs["contents"])})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def gemini(
    *responses: types.GenerateContentResponse | Exception,
) -> tuple[GeminiProvider, FakeGenai]:
    client = FakeGenai(*responses)
    return GeminiProvider(client, "gemini-test", 8192), client


async def test_request_config() -> None:
    provider, client = gemini(response({"text": "hi"}))
    await provider.complete([provider.user_entry("q")], TOOLS, "system prompt")

    config = client.calls[0]["config"]
    assert client.calls[0]["model"] == "gemini-test"
    assert config.system_instruction == "system prompt"
    assert config.automatic_function_calling.disable is True
    decls = config.tools[0].function_declarations
    assert [d.name for d in decls] == ["get_profile", "run_python"]
    assert decls[1].parameters_json_schema["required"] == ["code"]


async def test_tool_calls_stop_kinds_and_usage() -> None:
    provider, _ = gemini(
        response(THOUGHT, call("run_python", code="1"), call("get_profile", id="g1"))
    )
    turn = await provider.complete([], TOOLS, "s")

    assert turn.stop == "tool_use"
    assert [(c.name, c.input) for c in turn.tool_calls] == [
        ("run_python", {"code": "1"}),
        ("get_profile", {}),
    ]
    assert turn.tool_calls[0].id.startswith("local_call_")  # Gemini sent no id
    assert turn.tool_calls[1].id == "g1"
    assert turn.text == ""  # thought text is never answer text
    assert turn.usage["output_tokens"] == 7  # thinking tokens billed as output
    assert turn.native.parts[0].thought_signature == b"sig"  # kept for the next request


@pytest.mark.parametrize(
    ("finish", "stop"),
    [
        ("STOP", "end"),
        ("MAX_TOKENS", "max_tokens"),
        ("SAFETY", "refusal"),
        ("MALFORMED_FUNCTION_CALL", "other"),
    ],
)
async def test_finish_reasons(finish: str, stop: str) -> None:
    provider, _ = gemini(response({"text": "answer"}, finish=finish))
    assert (await provider.complete([], TOOLS, "s")).stop == stop


async def test_blocked_prompt_is_a_refusal() -> None:
    blocked = types.GenerateContentResponse.model_validate(
        {"prompt_feedback": {"block_reason": "SAFETY"}}
    )
    provider, _ = gemini(blocked)
    assert (await provider.complete([], TOOLS, "s")).stop == "refusal"


def test_tool_results_entry() -> None:
    provider, _ = gemini()
    entry = provider.tool_results_entry(
        [
            ToolResult(ToolCall("local_call_1", "run_python", {}), "boom", is_error=True),
            ToolResult(ToolCall("g1", "get_profile", {}), "[]", is_error=False),
        ]
    )
    first, second = (p.function_response for p in entry.parts)
    assert (first.id, first.name, first.response) == (None, "run_python", {"error": "boom"})
    assert (second.id, second.response) == ("g1", {"output": "[]"})


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [(429, "RESOURCE_EXHAUSTED", 429), (401, "UNAUTHENTICATED", 502), (500, "INTERNAL", 502)],
)
async def test_api_errors(code: int, status: str, expected: int) -> None:
    exc = errors.APIError(code, {"error": {"code": code, "status": status, "message": "x"}})
    provider, _ = gemini(exc)
    with pytest.raises(LLMError) as raised:
        await provider.complete([], TOOLS, "s")
    assert raised.value.status_code == expected


async def test_full_loop_with_gemini() -> None:
    provider, client = gemini(
        response(THOUGHT, call("get_profile")),
        response(call("run_python", code="df.sum()")),
        response({"text": "North leads."}),
    )
    state = AgentState()
    await run_agent(
        provider, Limits(), "q", "[profile]", FakeSandbox(ok_run(tables=[TABLE])), state
    )

    assert state.status == "ok"
    assert state.answer == "North leads."
    # the model's turn (with its thought signature) went back unchanged
    assert client.calls[1]["contents"][1].parts[0].thought_signature == b"sig"
    assert client.calls[1]["contents"][2].parts[0].function_response.response == {
        "output": "[profile]"
    }
    dumped = provider.dump_history(state.history)
    assert dumped[1]["parts"][0]["thought_signature"] == "c2ln"  # JSON-safe, lossless
    assert state.usage == {"input_tokens": 30, "output_tokens": 21, "cache_read_input_tokens": 0}


def test_create_provider() -> None:
    gemini_settings = Settings(llm_provider="gemini", gemini_api_key="k", gemini_model="m")
    assert isinstance(create_provider(gemini_settings), GeminiProvider)
    with pytest.raises(LLMNotConfigured, match="GEMINI_API_KEY"):
        create_provider(gemini_settings.model_copy(update={"gemini_api_key": SecretStr("")}))

    anthropic_settings = Settings(llm_provider="anthropic", anthropic_model="claude-opus-5-5")
    assert isinstance(create_provider(anthropic_settings), AnthropicProvider)
    with pytest.raises(LLMNotConfigured, match="ANTHROPIC_MODEL"):
        create_provider(anthropic_settings.model_copy(update={"anthropic_model": ""}))
