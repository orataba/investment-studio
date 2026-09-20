from pathlib import Path
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    research_portfolio_api_url: str | None = None
    research_regime_api_url: str | None = None

    app_name: str = "Investment Studio Watchlist API"
    app_version: str = "1.2.6"
    environment: str = "development"
    frontend_url: str = "http://127.0.0.1:5173"
    database_url: str
    alembic_database_url: str | None = None
    database_schema: str | None = "watchlist"
    sql_echo: bool = False
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    recalc_worker_enabled: bool = True
    recalc_worker_poll_interval_seconds: float = 1.0
    recalc_worker_shutdown_timeout_seconds: float = 5.0
    recalc_worker_running_job_timeout_seconds: float = 300.0
    recalc_worker_heartbeat_interval_seconds: float = 30.0
    recalc_worker_reconcile_interval_seconds: float = 60.0
    recalc_worker_reconcile_batch_size: int = 500
    document_storage_root: Path = Path.home() / ".local/share/investment-studio/watchlist-documents"
    document_upload_max_bytes: int = 25 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_prefix="INVESTMENT_STUDIO_WATCHLIST_",
        extra="ignore",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _validate_database_url(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("database_url must be explicitly configured.")
        return value.strip()

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
            if normalized != "watchlist":
                raise ValueError("database_schema must be the canonical 'watchlist' schema.")
            return normalized
        return value

    @field_validator(
        "recalc_worker_poll_interval_seconds",
        "recalc_worker_shutdown_timeout_seconds",
        "recalc_worker_running_job_timeout_seconds",
        "recalc_worker_heartbeat_interval_seconds",
        "recalc_worker_reconcile_interval_seconds",
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

    @field_validator("recalc_worker_reconcile_batch_size", mode="before")
    @classmethod
    def _coerce_reconcile_batch_size(cls, value: object) -> int:
        numeric = int(value)
        if numeric < 1 or numeric > 10_000:
            raise ValueError(
                "recalc_worker_reconcile_batch_size must be between 1 and 10000."
            )
        return numeric

    @field_validator("document_upload_max_bytes", mode="before")
    @classmethod
    def _coerce_positive_bytes(cls, value: object) -> object:
        numeric = int(value)
        if numeric <= 0:
            raise ValueError("document_upload_max_bytes must be positive.")
        return numeric

    @model_validator(mode="after")
    def _validate_cors_policy(self) -> "Settings":
        environment = self.environment.strip().lower()
        if environment not in {"development", "dev", "local", "test"} and "*" in self.cors_origins:
            raise ValueError("cors_origins must not contain '*' outside development/test.")
        if self.recalc_worker_heartbeat_interval_seconds >= self.recalc_worker_running_job_timeout_seconds:
            raise ValueError("recalc worker heartbeat interval must be shorter than the running-job timeout.")
        return self

    @property
    def cors_allow_credentials(self) -> bool:
        return "*" not in self.cors_origins

    @property
    def migration_database_url(self) -> str:
        return self.alembic_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
