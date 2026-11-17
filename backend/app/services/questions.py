"""Answer a question: run the agent against the session's files and store everything."""

import json
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.loop import AgentState, EventSink, Limits, run_agent
from app.agents.providers import LLMError, LLMNotConfigured, LLMProvider
from app.core.config import Settings
from app.db.models import CodeRun, Dataset, Question, Session
from app.sandbox.client import SandboxResult, SandboxUnavailable, run_code


class QuestionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def sql_tables(datasets: list[Dataset]) -> dict[str, str]:
    """A SQL table name per file, from its original name ("Q3 Sales.xlsx" -> q3_sales)."""
    tables: dict[str, str] = {}
    for d in datasets:
        base = re.sub(r"\W+", "_", d.filename.rsplit(".", 1)[0].lower()).strip("_") or "data"
        base = f"t_{base}" if base[0].isdigit() else base
        name, n = base, 2
        while name in tables:
            name, n = f"{base}_{n}", n + 1
        tables[name] = f"/data/{d.stored_name}"
    return tables


def profile_text(datasets: list[Dataset], tables: dict[str, str]) -> str:
    table_of = {path: name for name, path in tables.items()}
    return json.dumps(
        [
            {
                "path": f"/data/{d.stored_name}",
                "sql_table": table_of[f"/data/{d.stored_name}"],
                "uploaded_as": d.filename,
                "profile": d.profile,
            }
            for d in datasets
        ]
    )


async def ask(
    db: AsyncSession,
    settings: Settings,
    llm: LLMProvider | LLMNotConfigured,
    session_id: uuid.UUID,
    text: str,
    on_event: EventSink | None = None,
    mode: str = "auto",
) -> tuple[Question, list[CodeRun]]:
    if await db.get(Session, session_id) is None:
        raise QuestionError(404, "Session not found")
    datasets = list(
        await db.scalars(
            select(Dataset).where(Dataset.session_id == session_id).order_by(Dataset.created_at)
        )
    )
    if not datasets:
        raise QuestionError(422, "Upload a file to this session first")
    if isinstance(llm, LLMNotConfigured):
        raise QuestionError(503, str(llm))

    question = Question(session_id=session_id, text=text, status="running", messages=[])
    db.add(question)
    await db.commit()

    tables = sql_tables(datasets)

    async def run(code: str, language: str) -> SandboxResult:
        return await run_code(settings, session_id, code, tables if language == "sql" else None)

    state = AgentState()
    failure: QuestionError | None = None
    try:
        limits = Limits(settings.agent_max_iterations, settings.agent_max_code_errors)
        events = {"on_event": on_event} if on_event else {}
        profile = profile_text(datasets, tables)
        await run_agent(llm, limits, text, profile, run, state, mode=mode, **events)
    except LLMError as exc:
        failure = QuestionError(exc.status_code, exc.message)
    except SandboxUnavailable as exc:
        failure = QuestionError(503, f"The code sandbox is unavailable: {exc}")
    finally:
        # Stored whatever happened, so a failed run can still be inspected.
        question.status = "failed" if failure or state.status == "running" else state.status
        question.answer = state.answer
        question.error = failure.detail if failure else None
        question.messages = llm.dump_history(state.history)
        question.usage = state.usage
        question.finished_at = datetime.now(UTC)
        runs = [
            CodeRun(
                id=a.result.run_id,
                question_id=question.id,
                seq=i,
                code=a.code,
                language=a.language,
                status=a.result.status,
                result=a.result.model_dump(mode="json"),
            )
            for i, a in enumerate(state.runs)
        ]
        db.add_all(runs)
        await db.commit()
    if failure:
        raise failure
    return question, runs
