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
    platform_api_url: str = "http://127.0.0.1:8002"
    database_url: str = "postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu"
    alembic_database_url: str | None = None
    database_schema: str | None = "watchlist"
    sql_echo: bool = False
    cors_origins: list[str] = ["*"]
    email_sync_enabled: bool = False
    email_imap_host: str | None = None
    email_imap_port: int = 993
    email_imap_username: str | None = None
    email_imap_password: str | None = None
    email_imap_folder: str = "INBOX"
    email_imap_use_ssl: bool = True
    email_imap_max_messages: int = 20
    email_imap_mark_seen: bool = False
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

    @property
    def migration_database_url(self) -> str:
        return self.alembic_database_url or self.database_url

    @property
    def email_sync_ready(self) -> bool:
        return bool(self.email_imap_host and self.email_imap_username and self.email_imap_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
