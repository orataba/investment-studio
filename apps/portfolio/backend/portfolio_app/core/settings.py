from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Portfolio Operations Portfolio API"
    app_version: str = "0.1.0"
    environment: str = "development"
    frontend_url: str = "http://127.0.0.1:5174"
    database_url: str
    alembic_database_url: str | None = None
    database_schema: str | None = "portfolio"
    research_outputs_root: Path = WORKSPACE_ROOT / "backend" / "research_outputs"
    sql_echo: bool = False
    default_trade_timezone: str = "Asia/Shanghai"
    default_trade_time: str = "12:00"
    daily_snapshot_worker_enabled: bool = True
    daily_snapshot_worker_poll_seconds: float = 0.25
    daily_snapshot_worker_reconciliation_batch_size: int = 32
    daily_snapshot_worker_shutdown_seconds: float = 5.0
    copilot_analysis_timeout_seconds: float = 900.0
    cors_origins: list[str] = ["http://127.0.0.1:5174", "http://localhost:5174"]

    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_OPS_PORTFOLIO_",
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

    @field_validator("default_trade_timezone", mode="before")
    @classmethod
    def _validate_default_trade_timezone(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("default_trade_timezone must not be empty.")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("default_trade_timezone must be a valid IANA timezone.") from exc
        return normalized

    @field_validator("default_trade_time", mode="before")
    @classmethod
    def _validate_default_trade_time(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        try:
            parsed = time.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("default_trade_time must use HH:MM format.") from exc
        return f"{parsed.hour:02d}:{parsed.minute:02d}"

    @field_validator("database_schema", mode="before")
    @classmethod
    def _coerce_database_schema(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if normalized != "portfolio":
                raise ValueError("database_schema must be the canonical 'portfolio' schema.")
            return normalized
        return value

    @field_validator(
        "daily_snapshot_worker_poll_seconds",
        "daily_snapshot_worker_shutdown_seconds",
        "copilot_analysis_timeout_seconds",
    )
    @classmethod
    def _validate_positive_duration(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Worker and copilot durations must be positive.")
        return value

    @field_validator("daily_snapshot_worker_reconciliation_batch_size")
    @classmethod
    def _validate_positive_worker_batch_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(
                "daily_snapshot_worker_reconciliation_batch_size must be positive."
            )
        return value

    @model_validator(mode="after")
    def _validate_cors_policy(self) -> "Settings":
        environment = self.environment.strip().lower()
        if environment not in {"development", "dev", "local", "test"} and "*" in self.cors_origins:
            raise ValueError("cors_origins must not contain '*' outside development/test.")
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
