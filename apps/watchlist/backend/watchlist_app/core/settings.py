from pathlib import Path
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Yungu Watchlist API"
    app_version: str = "0.1.0"
    environment: str = "development"
    frontend_url: str = "http://127.0.0.1:5173"
    database_url: str = "postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu"
    alembic_database_url: str | None = None
    database_schema: str | None = "watchlist"
    sql_echo: bool = False
    cors_origins: list[str] = ["*"]
    recalc_worker_enabled: bool = True
    recalc_worker_poll_interval_seconds: float = 1.0
    recalc_worker_shutdown_timeout_seconds: float = 5.0
    recalc_worker_running_job_timeout_seconds: float = 300.0
    copilot_provider: str = "stub"
    copilot_openai_model: str = "gpt-5.4"
    copilot_openai_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="FTV2_",
        env_file=WORKSPACE_ROOT / "backend" / ".env",
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _coerce_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_schema", mode="before")
    @classmethod
    def _coerce_database_schema(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if not normalized.replace("_", "").isalnum() or normalized[0].isdigit():
                raise ValueError("database_schema must be a valid SQL identifier.")
            return normalized
        return value

    @field_validator(
        "recalc_worker_poll_interval_seconds",
        "recalc_worker_shutdown_timeout_seconds",
        "recalc_worker_running_job_timeout_seconds",
        mode="before",
    )
    @classmethod
    def _coerce_positive_seconds(cls, value: object) -> object:
        if value is None:
            return value
        numeric = float(value)
        if numeric <= 0:
            raise ValueError("worker timing values must be positive.")
        return numeric

    @property
    def migration_database_url(self) -> str:
        return self.alembic_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
