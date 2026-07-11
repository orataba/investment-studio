from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Portfolio Operations Platform API"
    app_version: str = "0.1.0"
    environment: str = "development"
    frontend_url: str = "http://127.0.0.1:5172"
    cors_origins: list[str] = ["http://127.0.0.1:5172", "http://localhost:5172"]
    watchlist_url: str = "http://127.0.0.1:5173"
    portfolio_url: str = "http://127.0.0.1:5174"
    watchlist_api_url: str = "http://127.0.0.1:8000"
    portfolio_api_url: str = "http://127.0.0.1:8001"
    database_url: str = "postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops"
    database_schema: str | None = "instrument_registry"
    sql_echo: bool = False
    email_sync_enabled: bool = False
    email_imap_host: str | None = None
    email_imap_port: int = 993
    email_imap_username: str | None = None
    email_imap_password: str | None = None
    email_imap_folder: str = "INBOX"
    email_imap_use_ssl: bool = True
    email_imap_timeout_seconds: int = 60
    email_imap_max_messages: int = 500
    email_imap_mark_seen: bool = False
    market_data_batch_item_timeout_seconds: int = 300
    tushare_token: str | None = None
    tushare_api_url: str = "https://fastapic.stockai888.top"
    tushare_timeout_seconds: int = 30
    tushare_batch_max_workers: int = 4
    tushare_batch_timeout_seconds: int = 3600

    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_OPS_PLATFORM_",
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

    @field_validator("tushare_batch_max_workers", mode="before")
    @classmethod
    def _coerce_tushare_batch_workers(cls, value: object) -> int:
        workers = int(value)
        if workers < 1 or workers > 8:
            raise ValueError("tushare_batch_max_workers must be between 1 and 8.")
        return workers

    @field_validator("tushare_batch_timeout_seconds", mode="before")
    @classmethod
    def _coerce_tushare_batch_timeout(cls, value: object) -> int:
        timeout = int(value)
        if timeout < 60 or timeout > 21600:
            raise ValueError("tushare_batch_timeout_seconds must be between 60 and 21600.")
        return timeout

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
    def email_sync_ready(self) -> bool:
        return bool(self.email_imap_host and self.email_imap_username and self.email_imap_password)

    @property
    def tushare_ready(self) -> bool:
        return bool(self.tushare_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
