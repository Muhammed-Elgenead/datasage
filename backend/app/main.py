from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.core.config import Settings, get_settings
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.db.dispose()

    app = FastAPI(title="DataSage", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = Database(settings.database_url)  # connects lazily, on first query
    app.include_router(health_router)
    app.include_router(sessions_router)
    return app


app = create_app()
