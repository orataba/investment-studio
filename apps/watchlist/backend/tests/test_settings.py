from __future__ import annotations

import pytest
from pydantic import ValidationError

from watchlist_app.core.settings import Settings


def test_settings_require_an_explicit_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


def test_settings_reject_a_blank_database_url() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(database_url="   ")
