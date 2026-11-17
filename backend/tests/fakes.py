"""A scripted stand-in for AsyncAnthropic and the sandbox: no API calls, no Docker."""

import copy
from types import SimpleNamespace
from typing import Any

from anthropic.types.beta import BetaMessage

from app.sandbox.client import SandboxResult, Table


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def tool(name: str, id: str, **input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": id, "name": name, "input": input}


def reply(*blocks: dict[str, Any], stop: str = "tool_use", in_tokens: int = 10) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, *blocks],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": in_tokens, "output_tokens": 5},
        }
    )


class FakeLLM:
    def __init__(self, *responses: BetaMessage | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=self)

    async def create(self, **params: Any) -> BetaMessage:
        self.calls.append(copy.deepcopy(params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok_run(run_id: str = "r", **fields: Any) -> SandboxResult:
    return SandboxResult(run_id=run_id, status="ok", **fields)


def error_run(run_id: str = "r", error: str = "KeyError: 'x'") -> SandboxResult:
    return SandboxResult(run_id=run_id, status="error", error=error)


TABLE = Table(columns=["region", "total"], rows=[["north", 100]], total_rows=1, truncated=False)


class FakeSandbox:
    def __init__(self, *results: SandboxResult | Exception) -> None:
        self.results = list(results)
        self.codes: list[str] = []

    async def __call__(self, code: str) -> SandboxResult:
        self.codes.append(code)
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
