from pathlib import Path
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Portfolio Operations Watchlist API"
    app_version: str = "0.1.0"
    environment: str = "development"
    frontend_url: str = "http://127.0.0.1:5173"
    database_url: str = "postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops"
    alembic_database_url: str | None = None
    database_schema: str | None = "watchlist"
    sql_echo: bool = False
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    recalc_worker_poll_interval_seconds: float = 1.0
    recalc_worker_running_job_timeout_seconds: float = 300.0
    recalc_worker_heartbeat_interval_seconds: float = 30.0
    recalc_worker_readiness_max_age_seconds: float = 90.0
    recalc_worker_retry_base_seconds: int = 5
    recalc_worker_retry_max_seconds: int = 300
    daily_market_quote_max_age_days: int = 5
    fund_quote_max_age_days: int = 45
    document_storage_root: Path = WORKSPACE_ROOT / "var" / "watchlist-documents"
    document_upload_max_bytes: int = 25 * 1024 * 1024
    copilot_provider: str = "stub"
    copilot_openai_model: str = "gpt-5.4"
    copilot_openai_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_OPS_WATCHLIST_",
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
        "recalc_worker_running_job_timeout_seconds",
        "recalc_worker_heartbeat_interval_seconds",
        "recalc_worker_readiness_max_age_seconds",
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

    @field_validator(
        "recalc_worker_retry_base_seconds",
        "recalc_worker_retry_max_seconds",
        mode="before",
    )
    @classmethod
    def _coerce_positive_retry_seconds(cls, value: object) -> int:
        numeric = int(value)
        if numeric <= 0:
            raise ValueError("retry timing values must be positive.")
        return numeric

    @field_validator("document_upload_max_bytes", mode="before")
    @classmethod
    def _coerce_positive_bytes(cls, value: object) -> object:
        numeric = int(value)
        if numeric <= 0:
            raise ValueError("document_upload_max_bytes must be positive.")
        return numeric

    @field_validator(
        "daily_market_quote_max_age_days",
        "fund_quote_max_age_days",
        mode="before",
    )
    @classmethod
    def _coerce_quote_age_days(cls, value: object) -> int:
        numeric = int(value)
        if not 1 <= numeric <= 366:
            raise ValueError("quote freshness age must be between 1 and 366 days.")
        return numeric

    @model_validator(mode="after")
    def _validate_cors_policy(self) -> "Settings":
        environment = self.environment.strip().lower()
        if environment not in {"development", "dev", "local", "test"} and "*" in self.cors_origins:
            raise ValueError("cors_origins must not contain '*' outside development/test.")
        if (
            self.recalc_worker_heartbeat_interval_seconds * 2
            >= self.recalc_worker_running_job_timeout_seconds
        ):
            raise ValueError(
                "recalc worker heartbeat must be less than half the running-job timeout."
            )
        if (
            self.recalc_worker_readiness_max_age_seconds
            < self.recalc_worker_heartbeat_interval_seconds * 2
        ):
            raise ValueError(
                "recalc worker readiness max age must cover at least two heartbeat intervals."
            )
        if self.recalc_worker_retry_max_seconds < self.recalc_worker_retry_base_seconds:
            raise ValueError(
                "recalc worker retry max must be greater than or equal to retry base."
            )
        return self

    def recalc_retry_delay_seconds(self, attempt_count: int) -> int:
        exponent = min(max(attempt_count - 1, 0), 30)
        return min(
            self.recalc_worker_retry_max_seconds,
            self.recalc_worker_retry_base_seconds * (2**exponent),
        )

    @property
    def cors_allow_credentials(self) -> bool:
        return "*" not in self.cors_origins

    @property
    def migration_database_url(self) -> str:
        return self.alembic_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
