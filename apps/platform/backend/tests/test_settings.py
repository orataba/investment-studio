from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from platform_app.core.settings import Settings
from platform_app.db.session import _search_path_fragments


def test_settings_require_an_explicit_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


def test_settings_reject_a_blank_database_url() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(database_url="   ")


def test_settings_define_a_private_operations_schema_and_migration_target() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")

    assert settings.database_schema == "instrument_registry"
    assert settings.operations_database_schema == "platform"
    assert settings.migration_database_url == settings.database_url
    assert settings.tushare_api_url == "https://ttx.dailyfetch.top"
    assert settings.csindex_api_url == "https://www.csindex.com.cn/csindex-home"
    assert settings.csindex_timeout_seconds == 30


def test_platform_search_path_precedes_the_shared_registry() -> None:
    assert _search_path_fragments(
        "platform",
        "instrument_registry",
    ) == ["platform", "instrument_registry", "public"]
    assert _search_path_fragments(
        "instrument_registry",
        "instrument_registry",
    ) == ["instrument_registry", "public"]


def test_email_ingestion_settings_normalize_folders_and_apply_safe_defaults() -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        email_imap_folders="INBOX, 云谷3号,INBOX,策略精选1号",  # type: ignore[arg-type]
    )

    assert settings.email_imap_folders == ["INBOX", "云谷3号", "策略精选1号"]
    assert settings.email_history_start_date == date(2025, 12, 26)
    assert settings.email_header_fetch_batch_size == 200
    assert settings.email_message_fetch_batch_size == 20
    assert settings.email_attachment_max_bytes == 25 * 1024 * 1024
    assert settings.email_ingestion_lease_seconds == 1800


def test_email_ingestion_folders_accept_csv_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_FOLDERS",
        "INBOX,云谷3号,策略精选1号",
    )

    settings = Settings(database_url="sqlite+pysqlite:///:memory:")

    assert settings.email_imap_folders == ["INBOX", "云谷3号", "策略精选1号"]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("email_imap_folders", "  "),
        ("email_history_start_date", "not-a-date"),
        ("email_header_fetch_batch_size", 1001),
        ("email_message_fetch_batch_size", 101),
        ("email_attachment_max_bytes", 100 * 1024 * 1024 + 1),
        ("email_ingestion_lease_seconds", 59),
    ],
)
def test_email_ingestion_settings_reject_invalid_limits(
    field_name: str,
    invalid_value: object,
) -> None:
    with pytest.raises(ValidationError, match=field_name):
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            **{field_name: invalid_value},
        )
