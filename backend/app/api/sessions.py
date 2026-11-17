import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Session
from app.db.session import get_db_session
from app.services.datasets import DatasetError, create_dataset

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
