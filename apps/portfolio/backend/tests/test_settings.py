from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from portfolio_app.core.settings import Settings


def test_settings_require_an_explicit_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


def test_settings_reject_a_blank_database_url() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(database_url="   ")


def test_worker_and_copilot_defaults_match_the_environment_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "INVESTMENT_STUDIO_PORTFOLIO_DAILY_SNAPSHOT_WORKER_POLL_SECONDS",
        raising=False,
    )
    monkeypatch.delenv(
        "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ANALYSIS_TIMEOUT_SECONDS",
        raising=False,
    )
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    template_path = Path(__file__).resolve().parents[1] / ".env.example"
    template_values = dict(
        line.split("=", 1)
        for line in template_path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    assert settings.daily_snapshot_worker_poll_seconds == 5.0
    assert settings.copilot_analysis_timeout_seconds == 900.0
    assert float(
        template_values[
            "INVESTMENT_STUDIO_PORTFOLIO_DAILY_SNAPSHOT_WORKER_POLL_SECONDS"
        ]
    ) == settings.daily_snapshot_worker_poll_seconds
    assert float(
        template_values[
            "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ANALYSIS_TIMEOUT_SECONDS"
        ]
    ) == settings.copilot_analysis_timeout_seconds
