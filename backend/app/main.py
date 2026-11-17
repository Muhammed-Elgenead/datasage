import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update

from app.agents.providers import LLMNotConfigured, LLMProvider, create_provider
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.core.config import Settings, get_settings
from app.core.limits import AskLimiter, MaxBodySize, cleanup_forever
from app.db.models import Question
from app.db.session import Database
from app.sandbox.client import remove_leftover_containers

logger = logging.getLogger(__name__)


async def fail_interrupted_questions(db: Database) -> None:
    """Questions still `running` at startup were cut off by a restart or crash."""
    # shortcut: assumes one backend process; with several workers, track which one owns a run
    try:
        async with db.engine.begin() as conn:
            await conn.execute(
                update(Question)
                .where(Question.status == "running")
                .values(status="failed", error="Interrupted: the server restarted while answering.")
            )
    except Exception:  # the database may be down; /health reports that
        logger.warning("could not mark interrupted questions as failed", exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await fail_interrupted_questions(app.state.db)
        await asyncio.to_thread(remove_leftover_containers, settings)
        cleanup = asyncio.create_task(cleanup_forever(app.state.db, settings))
        yield
        cleanup.cancel()
        await app.state.db.dispose()

    app = FastAPI(title="DataSage", version="0.1.0", lifespan=lifespan)
    # The upload limit plus room for the multipart wrapper around the file.
    app.add_middleware(MaxBodySize, max_bytes=(settings.max_upload_mb + 1) * 1024 * 1024)
    app.state.settings = settings
    app.state.ask_limiter = AskLimiter(settings.ask_limit_per_hour)
    app.state.db = Database(settings.database_url)  # connects lazily, on first query
    # The app still starts without an LLM; /ask then answers 503 with the reason.
    llm: LLMProvider | LLMNotConfigured
    try:
        llm = create_provider(settings)
    except LLMNotConfigured as exc:
        llm = exc
    app.state.llm = llm
    # The same routes at /x (dev: Vite strips /api) and /api/x (production: one origin).
    for prefix in ("", "/api"):
        app.include_router(health_router, prefix=prefix)
        app.include_router(sessions_router, prefix=prefix)
    if settings.static_dir:  # mounted last, so API routes win
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
    return app


app = create_app()
