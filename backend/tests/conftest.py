from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.main import create_app

TEST_DB_URL = get_settings().test_database_url
requires_db = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL is not set")


async def reset_test_db(app) -> None:  # type: ignore[no-untyped-def]
    async with app.state.db.engine.begin() as conn:
        name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
        assert name.endswith("_test"), "never reset a non-test database"
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=TEST_DB_URL or "", data_dir=tmp_path / "data", max_upload_mb=1)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    await reset_test_db(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.db.dispose()
