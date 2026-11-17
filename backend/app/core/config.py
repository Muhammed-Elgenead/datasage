"""Application settings, loaded from environment variables / the repo-root .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
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

    # Sandbox: the Docker CLI command (local dev: "wsl -d Ubuntu-24.04 -u root -e docker"),
    # and whether bind-mount paths must be turned into WSL paths (E:\x -> /mnt/e/x).
    sandbox_docker: str = "docker"
    sandbox_wsl_paths: bool = False
    sandbox_image: str = "datasage-sandbox:latest"
    sandbox_timeout_seconds: float = 30.0
    sandbox_memory_mb: int = 512
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 64
    sandbox_tmpfs_mb: int = 64
    sandbox_max_file_mb: int = 64  # RLIMIT_FSIZE: largest file the code may write
    sandbox_max_output_chars: int = 20_000
    sandbox_max_table_rows: int = 200
    sandbox_max_tables: int = 10
    sandbox_max_charts: int = 5
    sandbox_max_chart_kb: int = 2048
    sandbox_max_result_kb: int = 8192

    # LLM. Models and keys come from env only; /ask answers 503 while the chosen provider is
    # missing them. Each provider reads only its own settings.
    llm_provider: Literal["gemini", "anthropic"] = "gemini"

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = ""
    gemini_max_output_tokens: int = 16000
    gemini_max_retries: int = 2
    gemini_timeout_seconds: float = 120.0

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = ""
    # Claude Opus 5.5 defaults to medium, so it is always sent explicitly.
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Server-side refusal fallback: "default" lets the API pick; empty turns it off.
    anthropic_fallbacks: str = "default"
    anthropic_max_tokens: int = 16000
    anthropic_max_retries: int = 2

    # Agent guards: API calls per question, and failed code runs before giving up.
    agent_max_iterations: int = 12
    agent_max_code_errors: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
