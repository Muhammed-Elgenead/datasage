import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import update

from app.agents.providers import LLMNotConfigured, LLMProvider, create_provider
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.core.config import Settings, get_settings
from app.db.models import Question
from app.db.session import Database

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
        yield
        await app.state.db.dispose()

    app = FastAPI(title="DataSage", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = Database(settings.database_url)  # connects lazily, on first query
    # The app still starts without an LLM; /ask then answers 503 with the reason.
    llm: LLMProvider | LLMNotConfigured
    try:
        llm = create_provider(settings)
    except LLMNotConfigured as exc:
        llm = exc
    app.state.llm = llm
    app.include_router(health_router)
    app.include_router(sessions_router)
    return app


app = create_app()
