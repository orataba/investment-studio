from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    alembic_database_url: str | None = None
    frontend_url: str = "http://127.0.0.1:5175"
    cors_origins: list[str] = ["http://127.0.0.1:5175", "http://localhost:5175"]
    api_base_url: str = "http://127.0.0.1:8010/api/briefing"
    timezone: str = "Asia/Shanghai"
    data_root: Path | None = None
    edition_role: Literal["preview", "publisher"] = "preview"

    model_config = SettingsConfigDict(env_prefix="INVESTMENT_STUDIO_BRIEFING_", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def explicit_database(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Briefing database_url must be explicitly configured")
        return value

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo
        ZoneInfo(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
