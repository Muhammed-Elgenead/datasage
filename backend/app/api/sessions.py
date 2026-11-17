import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CodeRun, Session
from app.db.session import get_db_session
from app.sandbox.client import Table
from app.services.datasets import DatasetError, create_dataset
from app.services.questions import QuestionError, ask

router = APIRouter(prefix="/sessions", tags=["sessions"])

DB = Annotated[AsyncSession, Depends(get_db_session)]


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    filename: str
    file_type: str
    size_bytes: int
    profile: dict[str, Any]
    created_at: datetime


@router.post("", status_code=201)
async def create_session(db: DB) -> SessionOut:
    session = Session()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut.model_validate(session)


@router.post("/{session_id}/datasets", status_code=201)
async def upload_dataset(
    session_id: uuid.UUID, file: UploadFile, request: Request, db: DB
) -> DatasetOut:
    try:
        dataset = await create_dataset(db, request.app.state.settings, session_id, file)
    except DatasetError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    await db.refresh(dataset)
    return DatasetOut.model_validate(dataset)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class AnswerOut(BaseModel):
    id: uuid.UUID
    question: str
    status: str
    answer: str | None
    error: str | None
    # The run whose output the user sees: the last successful one, else the last attempt.
    run_id: str | None
    code: str | None
    code_error: str | None
    tables: list[Table]
    charts: list[str]  # file names in <data_dir>/<session_id>/runs/<run_id>/
    attempts: int
    usage: dict[str, int]


def _shown_run(runs: list[CodeRun]) -> CodeRun | None:
    ok = [r for r in runs if r.status == "ok"]
    return ok[-1] if ok else (runs[-1] if runs else None)


@router.post("/{session_id}/ask")
async def ask_question(session_id: uuid.UUID, body: AskIn, request: Request, db: DB) -> AnswerOut:
    try:
        question, runs = await ask(
            db, request.app.state.settings, request.app.state.llm, session_id, body.question
        )
    except QuestionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    shown = _shown_run(runs)
    result = shown.result if shown else {}
    return AnswerOut(
        id=question.id,
        question=question.text,
        status=question.status,
        answer=question.answer,
        error=question.error,
        run_id=shown.id if shown else None,
        code=shown.code if shown else None,
        code_error=result.get("error"),
        tables=result.get("tables", []),
        charts=result.get("charts", []),
        attempts=len(runs),
        usage=question.usage,
    )
