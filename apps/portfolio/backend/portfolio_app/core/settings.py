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
    database_url: str = "postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops"
    alembic_database_url: str | None = None
    database_schema: str | None = "portfolio"
    allocation_research_outputs_root: Path = (
        WORKSPACE_ROOT / "backend" / "allocation_research_outputs"
    )
    sql_echo: bool = False
    default_trade_timezone: str = "Asia/Shanghai"
    default_trade_time: str = "12:00"
    daily_market_valuation_quote_max_age_days: int = 5
    fund_valuation_quote_max_age_days: int = 45
    fx_valuation_quote_max_age_days: int = 5
    calculation_job_max_attempts: int = 3
    calculation_worker_lease_seconds: int = 120
    calculation_worker_heartbeat_seconds: int = 30
    calculation_worker_poll_milliseconds: int = 500
    calculation_job_retry_delay_seconds: int = 15
    calculation_worker_readiness_max_age_seconds: int = 90
    cors_origins: list[str] = ["http://127.0.0.1:5174", "http://localhost:5174"]

    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_OPS_PORTFOLIO_",
        env_file=WORKSPACE_ROOT / "backend" / ".env",
        extra="ignore",
    )

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

    @field_validator(
        "daily_market_valuation_quote_max_age_days",
        "fund_valuation_quote_max_age_days",
        "fx_valuation_quote_max_age_days",
    )
    @classmethod
    def _validate_valuation_quote_max_age_days(cls, value: int) -> int:
        resolved = int(value)
        if not 1 <= resolved <= 366:
            raise ValueError("valuation quote max age must be between 1 and 366 days.")
        return resolved

    @field_validator("calculation_job_max_attempts")
    @classmethod
    def _validate_calculation_job_max_attempts(cls, value: int) -> int:
        resolved = int(value)
        if not 1 <= resolved <= 20:
            raise ValueError("calculation_job_max_attempts must be between 1 and 20.")
        return resolved

    @field_validator(
        "calculation_worker_lease_seconds",
        "calculation_worker_heartbeat_seconds",
        "calculation_job_retry_delay_seconds",
        "calculation_worker_readiness_max_age_seconds",
    )
    @classmethod
    def _validate_calculation_worker_seconds(cls, value: int) -> int:
        resolved = int(value)
        if not 1 <= resolved <= 86_400:
            raise ValueError(
                "calculation worker durations must be between 1 and 86400 seconds"
            )
        return resolved

    @field_validator("calculation_worker_poll_milliseconds")
    @classmethod
    def _validate_calculation_worker_poll_milliseconds(cls, value: int) -> int:
        resolved = int(value)
        if not 50 <= resolved <= 60_000:
            raise ValueError(
                "calculation worker poll interval must be between 50 and 60000 milliseconds"
            )
        return resolved

    @model_validator(mode="after")
    def _validate_calculation_worker_timing(self) -> "Settings":
        if self.calculation_worker_heartbeat_seconds * 2 >= self.calculation_worker_lease_seconds:
            raise ValueError(
                "calculation worker heartbeat must be less than half the lease duration"
            )
        if (
            self.calculation_worker_readiness_max_age_seconds
            < self.calculation_worker_heartbeat_seconds * 2
        ):
            raise ValueError(
                "worker readiness max age must cover at least two heartbeat intervals"
            )
        return self

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
