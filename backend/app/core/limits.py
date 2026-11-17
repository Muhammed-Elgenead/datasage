"""Guards for a public instance: request size, questions per IP, and old-data cleanup."""

import asyncio
import logging
import shutil
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.db.models import Session
from app.db.session import Database

logger = logging.getLogger(__name__)


class MaxBodySize:
    """Rejects requests whose Content-Length is over the limit before the body is read.

    shortcut: chunked uploads without Content-Length aren't counted here; a reverse proxy
    with a body limit closes that gap when the app is deployed.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            length = dict(scope["headers"]).get(b"content-length", b"0")
            if length.isdigit() and int(length) > self.max_bytes:
                await send({"type": "http.response.start", "status": 413,
                            "headers": [(b"content-type", b"application/json")]})  # fmt: skip
                await send({"type": "http.response.body",
                            "body": b'{"detail":"Request body is too large"}'})  # fmt: skip
                return
        await self.app(scope, receive, send)


class AskLimiter:
    """At most `per_hour` questions per client IP in any rolling hour.

    shortcut: in-process memory, so it resets on restart and isn't shared between workers;
    move it to Postgres or Redis before running several backend processes.
    """

    def __init__(self, per_hour: int) -> None:
        self.per_hour = per_hour
        self.seen: dict[str, deque[float]] = defaultdict(deque)

    def check(self, request: Request) -> None:
        if self.per_hour <= 0:
            return
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        times = self.seen[ip]
        while times and now - times[0] > 3600:
            times.popleft()
        if len(times) >= self.per_hour:
            retry = int(3600 - (now - times[0])) + 1
            minutes = retry // 60 + 1
            raise HTTPException(
                429,
                f"Limit of {self.per_hour} questions per hour reached; try again in {minutes} min",
                headers={"Retry-After": str(retry)},
            )
        times.append(now)


async def delete_old_sessions(db: Database, settings: Settings) -> int:
    """Delete sessions older than the TTL: rows (questions, runs and datasets cascade) and
    their folder with the uploads and run outputs. Returns how many were deleted."""
    if settings.session_ttl_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=settings.session_ttl_days)
    async with db.sessions() as s:
        ids = list(await s.scalars(select(Session.id).where(Session.created_at < cutoff)))
        if ids:
            await s.execute(delete(Session).where(Session.id.in_(ids)))
            await s.commit()
    for sid in ids:  # files after rows: a crash in between leaves files, never dangling rows
        await asyncio.to_thread(shutil.rmtree, settings.data_dir / str(sid), True)
    return len(ids)


async def cleanup_forever(db: Database, settings: Settings, every_seconds: float = 3600) -> None:
    while True:
        try:
            if n := await delete_old_sessions(db, settings):
                logger.info("deleted %d sessions older than %d days", n, settings.session_ttl_days)
        except Exception:  # the database may be down for a while; try again next round
            logger.warning("session cleanup failed", exc_info=True)
        await asyncio.sleep(every_seconds)
