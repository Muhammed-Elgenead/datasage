"""Hand-written tool-use loop: the model writes pandas code or DuckDB SQL, the sandbox runs
it, errors go back to the model as `is_error` tool results so it can fix it and try again.

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
questions about them. Answer by running pandas code (run_python) or SQL (run_sql).

How to work:
- Call get_profile first to see each file's path, SQL table name, columns, types and \
sample rows.
- Pick the tool that fits: run_sql for filtering, grouping, joining and ranking clean \
columns; run_python for cleaning messy values, statistics, reshaping, and every chart.
- Each run starts fresh: nothing carries over between runs, so every run must load or \
query the data it needs.
- If a run fails, read the error, fix the code or query and run it again.
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

The run_sql environment:
- One DuckDB query over the session's files, each loaded as a table (names from \
get_profile; quote a name with double quotes if it is a SQL keyword). The query can't read \
files or reach the network. The result set is shown to the user as a table. SQL can't \
make charts.

The user sees the tables and charts from your last successful run, together with its \
code or query. So make that final run produce exactly what supports the answer: the result table, \
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
    ToolSpec(
        name="run_sql",
        description=(
            "Run one DuckDB SQL query in the sandbox over the session's files, each loaded "
            "as a table. Returns the result set as a table, or the error message."
        ),
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The SQL query to run."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
]

# The user can force one language per question ("auto" lets the model choose).
MODES = {
    "auto": ("get_profile", "run_python", "run_sql"),
    "python": ("get_profile", "run_python"),
    "sql": ("get_profile", "run_sql"),
}
MODE_NOTES = {
    "python": "\n\nFor this question the user asked for pandas: use run_python only.",
    "sql": "\n\nFor this question the user asked for SQL: use run_sql only. If a chart is "
    "requested, explain that SQL mode returns tables only.",
}
CODE_TOOLS = {"run_python": ("python", "code"), "run_sql": ("sql", "query")}

# Runs code in the sandbox: (code, language) where language is "python" or "sql".
RunCode = Callable[[str, str], Awaitable[SandboxResult]]
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
    language: str = "python"


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
    run_code: RunCode,
    state: AgentState,
    on_event: EventSink = _no_events,
    mode: str = "auto",
) -> None:
    tools = [t for t in TOOLS if t.name in MODES[mode]]
    system = SYSTEM_PROMPT + MODE_NOTES.get(mode, "")
    state.history.append(provider.user_entry(question))
    failures = 0

    for step in range(1, limits.max_iterations + 1):
        await on_event({"type": "thinking", "step": step})
        turn = await provider.complete(state.history, tools, system)
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
            results.append(await _run_tool(call, tools, profile, run_code, state, on_event))
            failures += results[-1].is_error and call.name in CODE_TOOLS
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
    call: ToolCall,
    tools: list[ToolSpec],
    profile: str,
    run_code: RunCode,
    state: AgentState,
    on_event: EventSink,
) -> ToolResult:
    if call.name not in {t.name for t in tools}:  # unknown, or not offered in this mode
        await on_event({"type": "tool", "name": call.name})
        return ToolResult(call, f"Tool not available: {call.name}", is_error=True)
    if call.name == "get_profile":
        await on_event({"type": "tool", "name": "get_profile"})
        return ToolResult(call, profile, is_error=False)

    language, field_name = CODE_TOOLS[call.name]
    code = call.input.get(field_name, "")
    attempt = len(state.runs) + 1
    await on_event({"type": "code", "attempt": attempt, "language": language, "code": code})
    run = await run_code(code, language)  # one at a time: each run is a container
    state.runs.append(CodeAttempt(code, run, language))
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
