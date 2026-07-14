from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from portfolio_app.api.routes import calculations as calculation_routes
from portfolio_app.calculations.portfolio_daily.commands import (
    PortfolioDailyRunHandle,
)
from portfolio_ops_calculation_core import CalculationRunStatus


pytestmark = pytest.mark.no_database


def test_portfolio_daily_command_returns_typed_202_handle(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    monkeypatch.setattr(
        calculation_routes,
        "enqueue_portfolio_daily_unit_of_work",
        lambda *args, **kwargs: PortfolioDailyRunHandle(
            run_id=UUID("11111111-1111-1111-1111-111111111111"),
            manifest_id=UUID("22222222-2222-2222-2222-222222222222"),
            portfolio_id="portfolio-exact",
            status=CalculationRunStatus.QUEUED,
            requested_as_of=date(2026, 7, 14),
            effective_as_of=date(2026, 7, 14),
            cutoff_at=cutoff,
            captured_generation=7,
            deduplicated=False,
            canonical_manifest_hash="a" * 64,
        ),
    )

    response = client.post(
        "/api/calculations/portfolio-daily",
        json={"portfolio_id": "portfolio-exact", "as_of_date": "2026-07-14"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "manifest_id": "22222222-2222-2222-2222-222222222222",
        "portfolio_id": "portfolio-exact",
        "status": "queued",
        "requested_as_of": "2026-07-14",
        "effective_as_of": "2026-07-14",
        "cutoff_at": "2026-07-14T08:00:00Z",
        "captured_generation": 7,
        "deduplicated": False,
        "canonical_manifest_hash": "a" * 64,
    }


def test_portfolio_daily_command_rejects_unknown_fields(client) -> None:
    response = client.post(
        "/api/calculations/portfolio-daily",
        json={
            "portfolio_id": "portfolio-exact",
            "as_of_date": "2026-07-14",
            "compatibility_mode": True,
        },
    )
    assert response.status_code == 422
