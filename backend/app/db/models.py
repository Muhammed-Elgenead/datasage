import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Session(Base):
    """One analysis session: its uploaded files share a data folder."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))  # as uploaded; display only
    stored_name: Mapped[str] = mapped_column(String(64))  # on-disk name, never user input
    file_type: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Question(Base):
    """One question and the agent's full run: the API transcript, its status and answer."""

    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    # running | ok | code_failed | max_iterations | refusal | incomplete | failed
    status: Mapped[str] = mapped_column(String(20))
    answer: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)  # why the run failed (API, sandbox)
    messages: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # every message + tool call
    usage: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CodeRun(Base):
    """One sandbox run. Its files live in <data_dir>/<session_id>/runs/<id>/."""

    __tablename__ = "code_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # the sandbox run_id
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int]  # order within the question
    code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the validated SandboxResult
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
