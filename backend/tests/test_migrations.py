"""Migrations build the schema from scratch and match the ORM models."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DB_URL, requires_db

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": TEST_DB_URL or ""},
        capture_output=True,
        text=True,
        check=False,
    )


@requires_db
async def test_upgrade_from_empty_matches_models() -> None:
    assert TEST_DB_URL
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
        assert name.endswith("_test"), "never reset a non-test database"
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    upgrade = alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    check = alembic("check")
    assert check.returncode == 0, check.stdout + check.stderr
