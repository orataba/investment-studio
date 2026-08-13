from datetime import date
from functools import lru_cache
import json
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    database_url: str
    alembic_database_url: str | None = None
    database_schema: str | None = "instrument_registry"
    operations_database_schema: str | None = "platform"
    sql_echo: bool = False
    email_sync_enabled: bool = False
    email_imap_host: str | None = None
    email_imap_port: int = 993
    email_imap_username: str | None = None
    email_imap_password: str | None = None
    email_imap_folders: Annotated[list[str], NoDecode] = ["INBOX"]
    email_imap_use_ssl: bool = True
    email_imap_timeout_seconds: int = 60
    email_imap_mark_seen: bool = False
    email_history_start_date: date = date(2025, 12, 26)
    email_header_fetch_batch_size: int = 200
    email_message_fetch_batch_size: int = 20
    email_attachment_max_bytes: int = 25 * 1024 * 1024
    email_ingestion_lease_seconds: int = 1800
    market_data_batch_item_timeout_seconds: int = 300
    datahub_api_key: str | None = None
    datahub_tushare_api_url: str = (
        "http://datahubco.com/app-api/openapi/v1/tushare"
    )
    datahub_timeout_seconds: int = 30
    datahub_tushare_batch_timeout_seconds: int = 3600
    csindex_api_url: str = "https://www.csindex.com.cn/csindex-home"
    csindex_timeout_seconds: int = 30

    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_OPS_PLATFORM_",
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

    @field_validator("email_imap_folders", mode="before")
    @classmethod
    def _coerce_email_imap_folders(cls, value: object) -> object:
        raw_values: object = value
        if isinstance(raw_values, str):
            stripped = raw_values.strip()
            if stripped.startswith("["):
                try:
                    raw_values = json.loads(stripped)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        "email_imap_folders must be CSV or a JSON string array."
                    ) from error
            else:
                raw_values = stripped.split(",")
        if not isinstance(raw_values, (list, tuple)):
            return raw_values
        folders: list[str] = []
        for item in raw_values:
            folder = str(item).strip()
            if folder and folder not in folders:
                folders.append(folder)
        if not folders:
            raise ValueError("email_imap_folders must contain at least one folder.")
        return folders

    @field_validator("email_header_fetch_batch_size", mode="before")
    @classmethod
    def _validate_email_header_fetch_batch_size(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("email_header_fetch_batch_size must be an integer.")
        batch_size = int(value)
        if batch_size < 1 or batch_size > 1000:
            raise ValueError(
                "email_header_fetch_batch_size must be between 1 and 1000."
            )
        return batch_size

    @field_validator("email_message_fetch_batch_size", mode="before")
    @classmethod
    def _validate_email_message_fetch_batch_size(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("email_message_fetch_batch_size must be an integer.")
        batch_size = int(value)
        if batch_size < 1 or batch_size > 100:
            raise ValueError(
                "email_message_fetch_batch_size must be between 1 and 100."
            )
        return batch_size

    @field_validator("email_attachment_max_bytes", mode="before")
    @classmethod
    def _validate_email_attachment_max_bytes(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("email_attachment_max_bytes must be an integer.")
        byte_count = int(value)
        if byte_count < 1 or byte_count > 100 * 1024 * 1024:
            raise ValueError(
                "email_attachment_max_bytes must be between 1 byte and 100 MiB."
            )
        return byte_count

    @field_validator("email_ingestion_lease_seconds", mode="before")
    @classmethod
    def _validate_email_ingestion_lease_seconds(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("email_ingestion_lease_seconds must be an integer.")
        seconds = int(value)
        if seconds < 60 or seconds > 21600:
            raise ValueError(
                "email_ingestion_lease_seconds must be between 60 and 21600."
            )
        return seconds

    @field_validator("database_schema", mode="before")
    @classmethod
    def _coerce_database_schema(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if normalized != "instrument_registry":
                raise ValueError(
                    "database_schema must be the canonical 'instrument_registry' schema."
                )
            return normalized
        return value

    @field_validator("operations_database_schema", mode="before")
    @classmethod
    def _coerce_operations_database_schema(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if normalized != "platform":
                raise ValueError(
                    "operations_database_schema must be the canonical 'platform' schema."
                )
            return normalized
        return value

    @field_validator("datahub_tushare_batch_timeout_seconds", mode="before")
    @classmethod
    def _coerce_datahub_tushare_batch_timeout(cls, value: object) -> int:
        timeout = int(value)
        if timeout < 60 or timeout > 21600:
            raise ValueError(
                "datahub_tushare_batch_timeout_seconds must be between 60 and 21600."
            )
        return timeout

    @field_validator("csindex_timeout_seconds", mode="before")
    @classmethod
    def _coerce_csindex_timeout(cls, value: object) -> int:
        timeout = int(value)
        if timeout < 1 or timeout > 300:
            raise ValueError("csindex_timeout_seconds must be between 1 and 300.")
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
    def datahub_ready(self) -> bool:
        return bool(self.datahub_api_key)

    @property
    def migration_database_url(self) -> str:
        return self.alembic_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
