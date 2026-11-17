"""Answer a question: run the agent against the session's files and store everything."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.loop import AgentState, Limits, run_agent
from app.agents.providers import LLMError, LLMNotConfigured, LLMProvider
from app.core.config import Settings
from app.db.models import CodeRun, Dataset, Question, Session
from app.sandbox.client import SandboxResult, SandboxUnavailable, run_code


class QuestionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def profile_text(datasets: list[Dataset]) -> str:
    return json.dumps(
        [
            {"path": f"/data/{d.stored_name}", "uploaded_as": d.filename, "profile": d.profile}
            for d in datasets
        ]
    )


async def ask(
    db: AsyncSession,
    settings: Settings,
    llm: LLMProvider | LLMNotConfigured,
    session_id: uuid.UUID,
    text: str,
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

    async def run_python(code: str) -> SandboxResult:
        return await run_code(settings, session_id, code)

    state = AgentState()
    failure: QuestionError | None = None
    try:
        limits = Limits(settings.agent_max_iterations, settings.agent_max_code_errors)
        await run_agent(llm, limits, text, profile_text(datasets), run_python, state)
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
