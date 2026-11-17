"""Public-instance guards: body size, questions per IP, old-session cleanup, and serving the
built frontend next to the API."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import limits
from app.core.config import Settings
from app.core.limits import AskLimiter, delete_old_sessions
from app.db.models import Dataset, Session
from app.main import create_app
from tests.conftest import requires_db

NO_DB = "postgresql+asyncpg://u:p@127.0.0.1:1/x_test"


def client_for(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_oversized_body_is_rejected_before_it_is_read() -> None:
    app = create_app(Settings(database_url=NO_DB, max_upload_mb=1))
    async with client_for(app) as c:
        resp = await c.post(
            f"/sessions/{uuid.uuid4()}/datasets",
            files={"file": ("big.csv", b"x" * 3 * 1024 * 1024)},
        )
    assert resp.status_code == 413
    assert resp.json() == {"detail": "Request body is too large"}


def fake_request(ip: str) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def test_ask_limiter_per_ip_and_window(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(limits.time, "monotonic", lambda: now[0])
    limiter = AskLimiter(per_hour=2)

    limiter.check(fake_request("1.1.1.1"))  # type: ignore[arg-type]
    limiter.check(fake_request("1.1.1.1"))  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        limiter.check(fake_request("1.1.1.1"))  # type: ignore[arg-type]
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "3601"}
    limiter.check(fake_request("2.2.2.2"))  # type: ignore[arg-type]  # other clients unaffected

    now[0] += 3601  # an hour later the window has moved on
    limiter.check(fake_request("1.1.1.1"))  # type: ignore[arg-type]


def test_ask_limit_zero_means_unlimited() -> None:
    limiter = AskLimiter(per_hour=0)
    for _ in range(100):
        limiter.check(fake_request("1.1.1.1"))  # type: ignore[arg-type]


@requires_db
async def test_ask_endpoints_are_rate_limited(app: FastAPI, client: AsyncClient) -> None:
    app.state.ask_limiter = AskLimiter(per_hour=1)
    sid = uuid.uuid4()
    first = await client.post(f"/sessions/{sid}/ask", json={"question": "q"})
    second = await client.post(f"/api/sessions/{sid}/ask/stream", json={"question": "q"})
    assert first.status_code == 404  # counted, then failed normally
    assert second.status_code == 429
    assert "Retry-After" in second.headers


async def test_api_prefix_and_static_frontend(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>DataSage</title>")
    app = create_app(Settings(database_url=NO_DB, static_dir=str(tmp_path)))
    async with client_for(app) as c:
        page = await c.get("/")
        api = await c.get("/api/health")
        bare = await c.get("/health")
    assert "<title>DataSage</title>" in page.text
    assert api.json()["status"] == "ok" and bare.json()["status"] == "ok"


@requires_db
async def test_old_sessions_and_their_files_are_deleted(app: FastAPI, settings: Settings) -> None:
    old, new = uuid.uuid4(), uuid.uuid4()
    async with app.state.db.sessions() as db:
        db.add(Session(id=old, created_at=datetime.now(UTC) - timedelta(days=8)))
        db.add(Session(id=new))
        await db.flush()
        db.add(Dataset(session_id=old, filename="a.csv", stored_name="x.csv", file_type="csv",
                       size_bytes=1, profile={}))  # fmt: skip
        await db.commit()
    for sid in (old, new):
        (settings.data_dir / str(sid) / "runs").mkdir(parents=True)

    deleted = await delete_old_sessions(
        app.state.db, settings.model_copy(update={"session_ttl_days": 7})
    )

    assert deleted == 1
    async with app.state.db.sessions() as db:
        assert list(await db.scalars(select(Session.id))) == [new]
        assert list(await db.scalars(select(Dataset))) == []  # cascaded
    assert not (settings.data_dir / str(old)).exists()
    assert (settings.data_dir / str(new)).exists()
    assert (
        await delete_old_sessions(app.state.db, settings.model_copy(update={"session_ttl_days": 0}))
        == 0
    )
