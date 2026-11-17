"""Application settings, loaded from environment variables / the repo-root .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"

    database_url: str = "postgresql+asyncpg://datasage:change-me@127.0.0.1:5432/datasage"
    # Separate *_test database; tests that need Postgres are skipped when unset.
    test_database_url: str | None = None

    # Uploaded files live in <data_dir>/<session_id>/ (the folder the sandbox will mount).
    data_dir: Path = REPO_ROOT / "data"
    max_upload_mb: int = 50
    profile_sample_rows: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
