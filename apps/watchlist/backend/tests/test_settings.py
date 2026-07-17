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


@pytest.mark.parametrize("batch_size", [0, 10_001])
def test_settings_bound_reconciliation_batch_size(batch_size: int) -> None:
    with pytest.raises(ValidationError, match="recalc_worker_reconcile_batch_size"):
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            recalc_worker_reconcile_batch_size=batch_size,
        )


def test_settings_require_positive_reconciliation_interval() -> None:
    with pytest.raises(ValidationError, match="worker timing values must be positive"):
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            recalc_worker_reconcile_interval_seconds=0,
        )
