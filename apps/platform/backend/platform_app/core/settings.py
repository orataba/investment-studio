from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Yungu Platform API"
    app_version: str = "0.1.0"
    environment: str = "development"
    frontend_url: str = "http://127.0.0.1:5172"
    cors_origins: list[str] = ["http://127.0.0.1:5172", "http://localhost:5172"]
    watchlist_url: str = "http://127.0.0.1:5173"
    portfolio_url: str = "http://127.0.0.1:5174"
    watchlist_api_url: str = "http://127.0.0.1:8000"
    portfolio_api_url: str = "http://127.0.0.1:8001"
    database_url: str = "postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu"
    database_schema: str | None = "shared_asset"
    sql_echo: bool = False
    email_sync_enabled: bool = False
    email_imap_host: str | None = None
    email_imap_port: int = 993
    email_imap_username: str | None = None
    email_imap_password: str | None = None
    email_imap_folder: str = "INBOX"
    email_imap_use_ssl: bool = True
    email_imap_max_messages: int = 500
    email_imap_mark_seen: bool = False

    model_config = SettingsConfigDict(
        env_prefix="YUNGU_PLATFORM_",
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
