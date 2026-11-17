from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok", "error"]
    database_error: str | None = None


@router.get("/health")
async def health(request: Request) -> HealthResponse:
    problem = await request.app.state.db.ping()
    return HealthResponse(
        status="ok", database="error" if problem else "ok", database_error=problem
    )
