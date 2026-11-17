"""Async engine + session factory, one per app (tests point it at the test database)."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> str | None:
        """None when the database answers, else a short reason."""
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # report, never raise: health checks must not crash
            return f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        return None

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessions() as session:
        yield session
