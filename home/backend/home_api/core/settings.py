from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Investment Studio Home"
    app_version: str = "1.2.0"
    environment: str = "development"
    frontend_url: str = "http://127.0.0.1:5172"
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://127.0.0.1:5172", "http://localhost:5172",
    ]
    apps_file: Path = Path(__file__).resolve().parents[3] / "apps.json"
    app_urls: dict[str, str] = {}
    database_url: str = ""
    auth_mode: Literal["account", "local"] = "account"
    auth_cookie_secure: bool = True
    auth_cookie_name: str = "__Secure-yungu_session"
    auth_cookie_domain: str | None = None
    auth_session_ttl_seconds: int = 24 * 60 * 60

    model_config = SettingsConfigDict(env_prefix="INVESTMENT_STUDIO_HOME_", extra="ignore")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _coerce_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.startswith("[") and normalized.endswith("]"):
                normalized = normalized[1:-1]
            return [item.strip().strip("'\"") for item in normalized.split(",") if item.strip()]
        return value

    @field_validator("auth_session_ttl_seconds", mode="before")
    @classmethod
    def _validate_session_ttl(cls, value: object) -> int:
        seconds = int(value)
        if seconds < 300 or seconds > 7 * 24 * 60 * 60:
            raise ValueError("auth_session_ttl_seconds must be between 300 and 604800.")
        return seconds

    @model_validator(mode="after")
    def _validate_cors_policy(self) -> "Settings":
        if self.auth_mode == "local" and self.environment.strip().lower() != "local":
            raise ValueError("Local owner access requires environment=local.")
        if not self.auth_cookie_secure and self.environment.strip().lower() not in {"development", "dev", "local", "test"}:
            raise ValueError("Insecure cookies are only allowed in explicit local/test environments.")
        if not self.auth_cookie_secure and self.auth_cookie_name.startswith("__Secure-"):
            raise ValueError("Local HTTP cookies must use a name without the __Secure- prefix.")
        if self.environment.strip().lower() not in {"development", "dev", "local", "test"} and "*" in self.cors_origins:
            raise ValueError("cors_origins must not contain '*' outside development/test.")
        return self

    @property
    def cors_allow_credentials(self) -> bool:
        return "*" not in self.cors_origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
