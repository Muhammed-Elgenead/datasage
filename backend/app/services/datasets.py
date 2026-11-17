"""Store an uploaded file in its session's data folder, profile it, and record it."""

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Dataset, Session
from app.services.profiling import profile_file

FILE_TYPES = {".csv": "csv", ".xlsx": "xlsx"}


class DatasetError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _copy(src: BinaryIO, dest: Path) -> None:
    with dest.open("wb") as out:
        shutil.copyfileobj(src, out)


async def create_dataset(
    db: AsyncSession, settings: Settings, session_id: uuid.UUID, upload: UploadFile
) -> Dataset:
    if await db.get(Session, session_id) is None:
        raise DatasetError(404, "Session not found")

    filename = upload.filename or ""
    file_type = FILE_TYPES.get(Path(filename).suffix.lower())
    if file_type is None:
        raise DatasetError(415, "Only .csv and .xlsx files are supported")
    # shortcut: the body is fully received before this check, upgrade by capping request size
    # at the reverse proxy when deployed (Phase 7)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if upload.size is not None and upload.size > max_bytes:
        raise DatasetError(413, f"File is larger than {settings.max_upload_mb} MB")
    if not upload.size:
        raise DatasetError(422, "The file is empty")

    dataset_id = uuid.uuid4()
    folder = settings.data_dir / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{dataset_id}.{file_type}"  # never derived from the user's filename
    path = folder / stored_name
    try:
        await asyncio.to_thread(_copy, upload.file, path)
        try:
            profile = await asyncio.to_thread(
                profile_file, path, file_type, settings.profile_sample_rows
            )
        except Exception as exc:  # any parse failure means a bad file, not a server error
            reason = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            raise DatasetError(
                422, f"Could not read the file as {file_type.upper()}: {reason}"
            ) from exc
        dataset = Dataset(
            id=dataset_id,
            session_id=session_id,
            filename=filename[:255],
            stored_name=stored_name,
            file_type=file_type,
            size_bytes=upload.size,
            profile=profile,
        )
        db.add(dataset)
        await db.commit()
    except BaseException:
        path.unlink(missing_ok=True)  # no orphan files when anything above fails
        raise
    return dataset
