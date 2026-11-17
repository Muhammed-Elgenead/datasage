import asyncio
import json
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CodeRun, Dataset, Question, Session
from app.db.session import get_db_session
from app.sandbox.client import CHART_NAME, PNG_MAGIC, Table, read_plain_file
from app.services.datasets import DatasetError, create_dataset
from app.services.questions import QuestionError, ask

logger = logging.getLogger(__name__)

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


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    # auto: the agent picks pandas or SQL; python / sql: the user forces one.
    mode: Literal["auto", "python", "sql"] = "auto"


class AnswerOut(BaseModel):
    id: uuid.UUID
    question: str
    status: str
    answer: str | None
    error: str | None
    # The run whose output the user sees: the last successful one, else the last attempt.
    run_id: str | None
    code: str | None
    language: str | None  # python | sql: what `code` is
    code_error: str | None
    tables: list[Table]
    charts: list[str]  # GET /sessions/{session_id}/runs/{run_id}/{chart}
    attempts: int
    usage: dict[str, int]
    created_at: datetime


class SessionSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    files: list[str]
    first_question: str | None
    questions: int


class SessionDetail(BaseModel):
    id: uuid.UUID
    created_at: datetime
    datasets: list[DatasetOut]
    questions: list[AnswerOut]


def answer_out(question: Question, runs: list[CodeRun]) -> AnswerOut:
    ok = [r for r in runs if r.status == "ok"]
    shown = ok[-1] if ok else (runs[-1] if runs else None)
    result = shown.result if shown else {}
    return AnswerOut(
        id=question.id,
        question=question.text,
        status=question.status,
        answer=question.answer,
        error=question.error,
        run_id=shown.id if shown else None,
        code=shown.code if shown else None,
        language=shown.language if shown else None,
        code_error=result.get("error"),
        tables=result.get("tables", []),
        charts=result.get("charts", []),
        attempts=len(runs),
        usage=question.usage or {},
        created_at=question.created_at,
    )


@router.post("", status_code=201)
async def create_session(db: DB) -> SessionOut:
    session = Session()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut.model_validate(session)


@router.get("")
async def list_sessions(db: DB) -> list[SessionSummary]:
    """The 50 newest sessions, for the history sidebar."""
    sessions = list(await db.scalars(select(Session).order_by(Session.created_at.desc()).limit(50)))
    ids = [s.id for s in sessions]
    files: dict[uuid.UUID, list[str]] = defaultdict(list)
    for sid, name in await db.execute(
        select(Dataset.session_id, Dataset.filename)
        .where(Dataset.session_id.in_(ids))
        .order_by(Dataset.created_at)
    ):
        files[sid].append(name)
    texts: dict[uuid.UUID, list[str]] = defaultdict(list)
    for sid, text in await db.execute(
        select(Question.session_id, Question.text)
        .where(Question.session_id.in_(ids))
        .order_by(Question.created_at)
    ):
        texts[sid].append(text)
    return [
        SessionSummary(
            id=s.id,
            created_at=s.created_at,
            files=files[s.id],
            first_question=texts[s.id][0] if texts[s.id] else None,
            questions=len(texts[s.id]),
        )
        for s in sessions
    ]


@router.get("/{session_id}")
async def get_session(session_id: uuid.UUID, db: DB) -> SessionDetail:
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    datasets = await db.scalars(
        select(Dataset).where(Dataset.session_id == session_id).order_by(Dataset.created_at)
    )
    questions = list(
        await db.scalars(
            select(Question).where(Question.session_id == session_id).order_by(Question.created_at)
        )
    )
    runs: dict[uuid.UUID, list[CodeRun]] = defaultdict(list)
    for run in await db.scalars(
        select(CodeRun)
        .where(CodeRun.question_id.in_([q.id for q in questions]))
        .order_by(CodeRun.seq)
    ):
        runs[run.question_id].append(run)
    return SessionDetail(
        id=session.id,
        created_at=session.created_at,
        datasets=[DatasetOut.model_validate(d) for d in datasets],
        questions=[answer_out(q, runs[q.id]) for q in questions],
    )


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


RUN_ID = re.compile(r"[0-9a-f]{32}")


@router.get("/{session_id}/runs/{run_id}/{name}")
async def get_chart(session_id: uuid.UUID, run_id: str, name: str, request: Request) -> Response:
    settings = request.app.state.settings
    if not RUN_ID.fullmatch(run_id) or not CHART_NAME.fullmatch(name):
        raise HTTPException(404, "Chart not found")
    folder = settings.data_dir / str(session_id) / "runs" / run_id
    # Written by sandboxed code: read only as a plain file, and only if it really is a PNG.
    data = read_plain_file(folder, name, settings.sandbox_max_chart_kb * 1024)
    if data is None or not data.startswith(PNG_MAGIC):
        raise HTTPException(404, "Chart not found")
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@router.post("/{session_id}/ask")
async def ask_question(session_id: uuid.UUID, body: AskIn, request: Request, db: DB) -> AnswerOut:
    request.app.state.ask_limiter.check(request)
    try:
        question, runs = await ask(
            db,
            request.app.state.settings,
            request.app.state.llm,
            session_id,
            body.question,
            mode=body.mode,
        )
    except QuestionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return answer_out(question, runs)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
KEEPALIVE_SECONDS = 15.0
_running: set[asyncio.Task[None]] = set()  # strong references, so runs aren't garbage-collected


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/{session_id}/ask/stream")
async def ask_stream(session_id: uuid.UUID, body: AskIn, request: Request) -> StreamingResponse:
    """Server-Sent Events over POST (the browser reads them with fetch). Progress events
    (thinking, tool, code, run) end with one `answer` or `error` event. The run isn't tied
    to the connection: if the client leaves, it still finishes and is stored."""
    request.app.state.ask_limiter.check(request)  # a plain 429, before the stream starts
    app = request.app
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(sse(event["type"], event))

    async def produce() -> None:
        try:
            async with app.state.db.sessions() as db:
                question, runs = await ask(
                    db,
                    app.state.settings,
                    app.state.llm,
                    session_id,
                    body.question,
                    emit,
                    body.mode,
                )
                answer = answer_out(question, runs)
            await queue.put(sse("answer", answer.model_dump(mode="json")))
        except QuestionError as exc:
            await queue.put(sse("error", {"status": exc.status_code, "detail": exc.detail}))
        except Exception:
            logger.exception("question run failed")
            await queue.put(sse("error", {"status": 500, "detail": "Internal error"}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    _running.add(task)
    task.add_done_callback(_running.discard)

    async def frames() -> AsyncIterator[str]:
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if frame is None:
                return
            yield frame

    return StreamingResponse(frames(), media_type="text/event-stream", headers=SSE_HEADERS)
