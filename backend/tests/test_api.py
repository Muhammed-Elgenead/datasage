import io
import uuid
from pathlib import Path

import pandas as pd
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import requires_db

CSV = b"region,revenue\nnorth,100\nsouth,250\n"


async def new_session(client: AsyncClient) -> str:
    resp = await client.post("/sessions")
    assert resp.status_code == 201
    return resp.json()["id"]


async def upload(client: AsyncClient, sid: str, name: str, content: bytes):  # type: ignore[no-untyped-def]
    return await client.post(f"/sessions/{sid}/datasets", files={"file": (name, content)})


def stored_files(settings: Settings) -> list[Path]:
    return [p for p in settings.data_dir.rglob("*") if p.is_file()]


async def test_health_reports_database_down_without_failing() -> None:
    app = create_app(Settings(database_url="postgresql+asyncpg://u:p@127.0.0.1:1/x_test"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["database"] == "error"


@requires_db
async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.json() == {"status": "ok", "database": "ok", "database_error": None}


@requires_db
async def test_upload_csv(client: AsyncClient, settings: Settings) -> None:
    sid = await new_session(client)

    resp = await upload(client, sid, "sales.csv", CSV)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "sales.csv"
    assert body["file_type"] == "csv"
    assert body["size_bytes"] == len(CSV)
    assert body["profile"]["row_count"] == 2
    revenue = next(c for c in body["profile"]["columns"] if c["name"] == "revenue")
    assert revenue["stats"]["mean"] == 175.0
    assert stored_files(settings) == [settings.data_dir / sid / f"{body['id']}.csv"]


@requires_db
async def test_upload_xlsx(client: AsyncClient) -> None:
    buf = io.BytesIO()
    pd.DataFrame({"x": [1, 2, 3]}).to_excel(buf, index=False)
    sid = await new_session(client)

    resp = await upload(client, sid, "Book.XLSX", buf.getvalue())

    assert resp.status_code == 201, resp.text
    assert resp.json()["file_type"] == "xlsx"
    assert resp.json()["profile"]["columns"][0]["stats"]["max"] == 3.0


@requires_db
async def test_user_filename_never_reaches_the_filesystem(
    client: AsyncClient, settings: Settings
) -> None:
    sid = await new_session(client)

    resp = await upload(client, sid, "../../evil.csv", CSV)

    assert resp.status_code == 201
    [path] = stored_files(settings)
    assert path.parent == settings.data_dir / sid
    assert "evil" not in path.name


@requires_db
async def test_rejects_wrong_type(client: AsyncClient, settings: Settings) -> None:
    resp = await upload(client, await new_session(client), "data.json", b"{}")
    assert resp.status_code == 415
    assert stored_files(settings) == []


@requires_db
async def test_rejects_too_large(client: AsyncClient, settings: Settings) -> None:
    big = b"a\n" + b"1\n" * (settings.max_upload_mb * 1024 * 1024 // 2)
    resp = await upload(client, await new_session(client), "big.csv", big)
    assert resp.status_code == 413
    assert stored_files(settings) == []


@requires_db
async def test_rejects_empty(client: AsyncClient) -> None:
    resp = await upload(client, await new_session(client), "empty.csv", b"")
    assert resp.status_code == 422


@requires_db
async def test_rejects_unparsable_and_removes_file(client: AsyncClient, settings: Settings) -> None:
    resp = await upload(client, await new_session(client), "fake.xlsx", b"not a zip file")
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("Could not read the file as XLSX")
    assert stored_files(settings) == []


@requires_db
async def test_unknown_session_is_404(client: AsyncClient) -> None:
    resp = await upload(client, str(uuid.uuid4()), "a.csv", CSV)
    assert resp.status_code == 404
