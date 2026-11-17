"""Hand-written tool-use loop: the model writes pandas code, the sandbox runs it, errors go
back to the model as `is_error` tool results so it can fix the code and try again.

The loop only mutates an `AgentState`, so the caller can store whatever happened even
when the loop raises (API or sandbox failure).
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.providers import LLMProvider, ToolCall, ToolResult, ToolSpec
from app.sandbox.client import SandboxResult

MODEL_TABLE_ROWS = 30  # rows of each table the model sees (the user gets the full capped table)

SYSTEM_PROMPT = """\
You are DataSage, a data analyst. The user uploaded one or more CSV/Excel files and asks \
questions about them. Answer by writing pandas code and running it with the run_python tool.

How to work:
- Call get_profile first to see each file's path, columns, types and sample rows.
- Then call run_python. Each run is a fresh Python process: nothing carries over between \
runs, so every run must load the data it needs.
- If a run fails, read the traceback, fix the code and run it again.
- Base every number in your answer on code output, never on guesses.
- Real files are messy (numbers stored as text, mixed date formats). After cleaning or \
parsing a column, print how many values became null or were dropped. Never let \
errors="coerce" or dropna hide rows silently: if values are lost, fix the parsing.

The run_python environment:
- Files are read-only under /data (use the paths from get_profile). There is no network \
and you can't install packages. Each run has a time and memory limit.
- pandas (pd), numpy (np) and matplotlib.pyplot (plt) are already imported.
- show(df, title=None) returns a DataFrame or Series to the user as a table. If the last \
line is an expression, its value is shown too, like in a notebook.
- Every matplotlib figure that is still open at the end is saved as a chart for the user.

The user sees the tables and charts from your last successful run, together with its \
code. So make that final run produce exactly what supports the answer: the result table, \
and a chart when it helps or when the user asks for one. Give each chart a title and \
axis labels.

Your final reply is the answer itself: short, in plain language, with the key numbers. \
Don't paste the code or repeat whole tables, since the user already sees them. Write \
plain text without Markdown (no **, #, tables or LaTeX): it is shown as-is.\
"""

TOOLS = [  # sorted by name, so the cached prefix stays stable
    ToolSpec(
        name="get_profile",
        description=(
            "Return every uploaded file of this session: its path under /data, original "
            "name, row and column counts, column types, null counts, basic stats and "
            "sample rows."
        ),
        schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="run_python",
        description=(
            "Run Python code in an isolated sandbox with the session's files under /data. "
            "Returns stdout, the error traceback if it failed, tables passed to show() "
            "(or the last expression), and the names of saved charts."
        ),
        schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The complete Python code to run."}
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    ),
]

RunPython = Callable[[str], Awaitable[SandboxResult]]
# Progress for the UI: {"type": "thinking" | "tool" | "code" | "run", ...}
EventSink = Callable[[dict[str, Any]], Awaitable[None]]


async def _no_events(event: dict[str, Any]) -> None:
    pass


@dataclass(frozen=True)
class Limits:
    max_iterations: int = 12  # model calls per question
    max_code_errors: int = 3  # failed code runs before giving up


@dataclass
class CodeAttempt:
    code: str
    result: SandboxResult


@dataclass
class AgentState:
    history: list[Any] = field(default_factory=list)  # the provider's native messages
    runs: list[CodeAttempt] = field(default_factory=list)
    status: str = "running"
    answer: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def format_run(r: SandboxResult) -> str:
    """The tool result the model sees: compact JSON, empty fields left out."""
    data: dict[str, Any] = {
        "status": r.status,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "error": r.error,
        "result": r.result,
        "tables": [
            {
                "title": t.title,
                "columns": t.columns,
                "rows": t.rows[:MODEL_TABLE_ROWS],
                "total_rows": t.total_rows,
            }
            for t in r.tables
        ],
        "charts": r.charts,
        "notes": r.notes,
    }
    return json.dumps({k: v for k, v in data.items() if v}, default=str)


_FINAL_STATUS = {"end": "ok", "refusal": "refusal"}


async def run_agent(
    provider: LLMProvider,
    limits: Limits,
    question: str,
    profile: str,
    run_python: RunPython,
    state: AgentState,
    on_event: EventSink = _no_events,
) -> None:
    state.history.append(provider.user_entry(question))
    failures = 0

    for step in range(1, limits.max_iterations + 1):
        await on_event({"type": "thinking", "step": step})
        turn = await provider.complete(state.history, TOOLS, SYSTEM_PROMPT)
        for key, n in turn.usage.items():
            state.usage[key] = state.usage.get(key, 0) + n
        state.history.append(turn.native)
        if turn.stop == "pause":
            continue
        # Checked before running tools: a turn cut off by max_tokens can hold a truncated call.
        if turn.stop != "tool_use":
            state.answer = turn.text
            state.status = _FINAL_STATUS.get(turn.stop, "incomplete")
            return

        results = []
        for call in turn.tool_calls:
            results.append(await _run_tool(call, profile, run_python, state, on_event))
            failures += results[-1].is_error and call.name == "run_python"
        state.history.append(provider.tool_results_entry(results))  # all results, one message

        if failures >= limits.max_code_errors:
            state.status = "code_failed"
            state.answer = (
                f"I couldn't get working code after {failures} failed attempts. "
                "The last error is shown with the code."
            )
            return

    state.status = "max_iterations"
    state.answer = f"I stopped after {limits.max_iterations} steps without a final answer."


async def _run_tool(
    call: ToolCall, profile: str, run_python: RunPython, state: AgentState, on_event: EventSink
) -> ToolResult:
    if call.name == "get_profile":
        await on_event({"type": "tool", "name": "get_profile"})
        return ToolResult(call, profile, is_error=False)
    if call.name == "run_python":
        code = call.input.get("code", "")
        attempt = len(state.runs) + 1
        await on_event({"type": "code", "attempt": attempt, "code": code})
        run = await run_python(code)  # one at a time: each run is a container
        state.runs.append(CodeAttempt(code, run))
        await on_event(
            {
                "type": "run",
                "attempt": attempt,
                "status": run.status,
                "error": run.error.strip().splitlines()[-1]
                if run.error and run.error.strip()
                else None,
                "tables": len(run.tables),
                "charts": len(run.charts),
            }
        )
        return ToolResult(call, format_run(run), is_error=run.status != "ok")
    await on_event({"type": "tool", "name": call.name})
    return ToolResult(call, f"Unknown tool: {call.name}", is_error=True)
