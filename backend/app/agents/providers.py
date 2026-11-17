"""LLM adapters behind one provider-neutral contract.

The loop never touches an SDK's message format. Each provider keeps the history in its own
native form, turns a response into a `ModelTurn`, appends assistant turns exactly as
returned (thinking blocks / thought signatures survive), formats tool results, and raises
`LLMError` with the HTTP status the API should answer.
"""

import itertools
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
import httpx
import httpx2
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.core.config import Settings

StopKind = Literal["end", "tool_use", "pause", "refusal", "max_tokens", "other"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]  # JSON Schema with additionalProperties: false


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call: ToolCall
    output: str
    is_error: bool


@dataclass
class ModelTurn:
    stop: StopKind
    text: str
    tool_calls: list[ToolCall]
    usage: dict[str, int] = field(default_factory=dict)
    native: Any = None  # appended to the history unchanged


class LLMError(Exception):
    """An API failure after the SDK's own retries; `status_code` is what /ask returns."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class LLMNotConfigured(Exception):
    pass


class LLMProvider(Protocol):
    name: str

    def user_entry(self, text: str) -> Any: ...
    async def complete(
        self, history: list[Any], tools: list[ToolSpec], system: str
    ) -> ModelTurn: ...
    def tool_results_entry(self, results: list[ToolResult]) -> Any: ...
    def dump_history(self, history: list[Any]) -> list[dict[str, Any]]: ...


# --- Anthropic ---------------------------------------------------------------------------

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_ANTHROPIC_STOPS: dict[str | None, StopKind] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_use",
    "pause_turn": "pause",
    "refusal": "refusal",
    "max_tokens": "max_tokens",
}


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self, client: Any, model: str, effort: str, max_tokens: int, fallbacks: str
    ) -> None:
        self.client, self.model, self.effort = client, model, effort
        self.max_tokens, self.fallbacks = max_tokens, fallbacks

    def user_entry(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def request_params(
        self, history: list[Any], tools: list[ToolSpec], system: str
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.schema,
                    "strict": True,
                }
                for t in tools
            ],
            "messages": history,
            # Claude Opus 5.5 always thinks; effort is the depth control (default medium).
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            # Each iteration re-sends the whole history; auto-cache the latest prefix.
            "cache_control": {"type": "ephemeral"},
        }
        if self.fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = self.fallbacks
        return params

    async def complete(self, history: list[Any], tools: list[ToolSpec], system: str) -> ModelTurn:
        try:
            r = await self.client.beta.messages.create(
                **self.request_params(history, tools, system)
            )
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LLMError(504, "Could not reach the Anthropic API") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(429, "Anthropic rate limit reached, try again shortly") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError(502, "Anthropic rejected the API key (ANTHROPIC_API_KEY)") from exc
        except anthropic.APIError as exc:
            raise LLMError(502, f"Anthropic API error: {exc.message}") from exc
        u = r.usage
        return ModelTurn(
            stop=_ANTHROPIC_STOPS.get(r.stop_reason, "other"),
            text="".join(b.text for b in r.content if b.type == "text").strip(),
            tool_calls=[
                ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else {})
                for b in r.content
                if b.type == "tool_use"
            ],
            usage={
                "input_tokens": u.input_tokens or 0,
                "output_tokens": u.output_tokens or 0,
                "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
            },
            # Kept exactly as returned: preserved thinking and the cache prefix need it.
            native={
                "role": "assistant",
                "content": [b.model_dump(mode="json", exclude_none=True) for b in r.content],
            },
        )

    def tool_results_entry(self, results: list[ToolResult]) -> Any:
        blocks = []
        for r in results:
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": r.call.id,
                "content": r.output,
            }
            if r.is_error:
                block["is_error"] = True
            blocks.append(block)
        return {"role": "user", "content": blocks}  # all results in ONE message

    def dump_history(self, history: list[Any]) -> list[dict[str, Any]]:
        return history  # already JSON-safe dicts


# --- Gemini ------------------------------------------------------------------------------

_GEMINI_REFUSALS = {
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "MODEL_ARMOR",
}
_LOCAL_ID = "local_call_"


class GeminiProvider:
    name = "gemini"

    def __init__(self, client: Any, model: str, max_output_tokens: int) -> None:
        self.client, self.model, self.max_output_tokens = client, model, max_output_tokens
        self._ids = itertools.count(1)

    def user_entry(self, text: str) -> Any:
        return types.Content(role="user", parts=[types.Part(text=text)])

    def request_config(self, tools: list[ToolSpec], system: str) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=self.max_output_tokens,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name, description=t.description, parameters_json_schema=t.schema
                        )
                        for t in tools
                    ]
                )
            ],
            # The SDK must never run tools itself; the loop does.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def complete(self, history: list[Any], tools: list[ToolSpec], system: str) -> ModelTurn:
        try:
            r = await self.client.aio.models.generate_content(
                model=self.model, contents=history, config=self.request_config(tools, system)
            )
        except genai_errors.APIError as exc:
            raise _gemini_error(exc) from exc
        except (httpx.TransportError, httpx2.TransportError) as exc:
            raise LLMError(504, "Could not reach the Gemini API") from exc

        u = r.usage_metadata
        usage = {
            "input_tokens": (u.prompt_token_count or 0) if u else 0,
            # Thinking tokens are billed as output.
            "output_tokens": ((u.candidates_token_count or 0) + (u.thoughts_token_count or 0))
            if u
            else 0,
            "cache_read_input_tokens": (u.cached_content_token_count or 0) if u else 0,
        }
        if not r.candidates:  # the prompt itself was blocked
            return ModelTurn(stop="refusal", text="", tool_calls=[], usage=usage)
        candidate = r.candidates[0]
        parts = list(candidate.content.parts or []) if candidate.content else []
        finish = candidate.finish_reason.name if candidate.finish_reason else None
        calls = [
            # Gemini may omit call ids; a local one is used and never sent back.
            ToolCall(p.function_call.id or f"{_LOCAL_ID}{next(self._ids)}",
                     p.function_call.name or "", dict(p.function_call.args or {}))
            for p in parts
            if p.function_call
        ]  # fmt: skip
        if finish in (None, "STOP", "FINISH_REASON_UNSPECIFIED"):
            stop: StopKind = "tool_use" if calls else "end"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish in _GEMINI_REFUSALS:
            stop = "refusal"
        else:
            stop = "other"  # MALFORMED_FUNCTION_CALL, ...
        return ModelTurn(
            stop=stop,
            text="".join(p.text for p in parts if p.text and not p.thought).strip(),
            tool_calls=calls,
            usage=usage,
            # Every part kept as received: thought signatures may sit on any of them.
            native=types.Content(role="model", parts=parts),
        )

    def tool_results_entry(self, results: list[ToolResult]) -> Any:
        parts = [
            types.Part(
                function_response=types.FunctionResponse(
                    id=None if r.call.id.startswith(_LOCAL_ID) else r.call.id,
                    name=r.call.name,
                    response={"error": r.output} if r.is_error else {"output": r.output},
                )
            )
            for r in results
        ]
        return types.Content(role="user", parts=parts)  # all results in ONE turn

    def dump_history(self, history: list[Any]) -> list[dict[str, Any]]:
        # mode="json" base64-encodes thought signatures, so the transcript stays lossless.
        return [c.model_dump(mode="json", exclude_none=True) for c in history]


def _gemini_error(exc: genai_errors.APIError) -> LLMError:
    code, status, msg = exc.code, (exc.status or "").upper(), exc.message or str(exc)
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return LLMError(429, "Gemini rate limit or quota reached, try again shortly")
    if code == 401 or status == "UNAUTHENTICATED" or (code == 400 and "API key" in msg):
        return LLMError(502, "Gemini rejected the API key (GEMINI_API_KEY)")
    return LLMError(502, f"Gemini API error ({code}): {msg}")


# --- Factory -----------------------------------------------------------------------------


def create_provider(settings: Settings) -> LLMProvider:
    """Raises LLMNotConfigured when the chosen provider is missing its model or key."""
    if settings.llm_provider == "gemini":
        key = settings.gemini_api_key.get_secret_value()
        if not key or not settings.gemini_model:
            raise LLMNotConfigured("The LLM is not configured: set GEMINI_API_KEY and GEMINI_MODEL")
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=int(settings.gemini_timeout_seconds * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(attempts=settings.gemini_max_retries + 1),
            ),
        )
        return GeminiProvider(client, settings.gemini_model, settings.gemini_max_output_tokens)

    if not settings.anthropic_model:
        raise LLMNotConfigured("The LLM is not configured: set ANTHROPIC_MODEL")
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value() or None,  # None: SDK finds creds
        max_retries=settings.anthropic_max_retries,
    )
    return AnthropicProvider(
        client,
        settings.anthropic_model,
        settings.anthropic_effort,
        settings.anthropic_max_tokens,
        settings.anthropic_fallbacks,
    )
