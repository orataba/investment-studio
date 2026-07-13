from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event

from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services.instrument_charts import (
    build_instrument_holdings_market_profile_from_market_data,
    build_instrument_price_chart_from_market_data,
    canonical_series_points,
    lock_instrument_market_data_in_session,
)
from portfolio_app.services.risk_basis import (
    calculation_frequency_profile_for_instruments,
)
from portfolio_ops_instrument_core import instrument_store as shared_store


def _create_role_separated_instrument(suffix: str) -> str:
    instrument = shared_store.create_instrument(
        get_session_factory(),
        instrument_name=f"Canonical Chart Risk {suffix}",
        instrument_type="equity",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": f"CCR-{suffix}",
                "is_primary": True,
            }
        ],
        quote_selection_policy={
            "trading": ["close"],
            "valuation": ["close"],
            "total_return": ["adjusted_close"],
            "chart": ["close"],
            "reference": ["close"],
        },
    )
    return str(instrument["instrument_id"])


def _upsert_points(
    instrument_id: str,
    *,
    start_date: date,
    count: int = 2,
    spacing_days: int = 1,
) -> None:
    rows: list[dict[str, object]] = []
    for index in range(count):
        point_date = start_date + timedelta(days=index * spacing_days)
        rows.extend(
            [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": point_date.isoformat(),
                    "value": str(100 + index),
                    "currency": "USD",
                    "source_ref": f"test:chart:{index}",
                    "status": "complete",
                },
                {
                    "metric_family": "price",
                    "quote_basis": "adjusted_close",
                    "as_of_date": point_date.isoformat(),
                    "value": str(200 + index * 4),
                    "currency": "USD",
                    "source_ref": f"test:return:{index}",
                    "status": "complete",
                },
            ]
        )
    assert shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=rows,
    ) == len(rows)


def _lock(instrument_id: str, as_of_date: date):
    with get_session_factory()() as session:
        return lock_instrument_market_data_in_session(
            session,
            instrument_ids=[instrument_id],
            as_of_date=as_of_date,
        )


def test_chart_and_return_metrics_use_distinct_explicit_roles() -> None:
    instrument_id = _create_role_separated_instrument("ROLE")
    _upsert_points(instrument_id, start_date=date(2026, 1, 1))
    assert shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=[
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2025-12-31",
                "value": "200",
                "currency": "USD",
                "source_ref": "test:return:period-anchor",
                "status": "complete",
            }
        ],
    ) == 1
    market_data = _lock(instrument_id, date(2026, 1, 2))

    chart = build_instrument_price_chart_from_market_data(
        market_data,
        instrument_id=instrument_id,
        range_key="all",
    )
    profile = build_instrument_holdings_market_profile_from_market_data(
        market_data,
        instrument_id=instrument_id,
    )

    assert chart is not None
    assert chart["chart_basis"] == "close"
    assert chart["summary"]["change_pct"] == pytest.approx(0.01)
    assert profile["instrument_trend_basis"] == "adjusted_close"
    assert profile["instrument_return_ytd"] == pytest.approx(0.02)


def test_january_only_total_return_history_does_not_invent_mtd_or_ytd() -> None:
    instrument_id = _create_role_separated_instrument("NO-ANCHOR")
    _upsert_points(instrument_id, start_date=date(2026, 1, 1))
    market_data = _lock(instrument_id, date(2026, 1, 2))

    profile = build_instrument_holdings_market_profile_from_market_data(
        market_data,
        instrument_id=instrument_id,
    )

    assert profile["instrument_return_mtd"] is None
    assert profile["instrument_return_ytd"] is None


@pytest.mark.parametrize("revision_status", ["partial", "rejected"])
def test_newer_non_complete_revision_blocks_only_its_locked_role(
    revision_status: str,
) -> None:
    instrument_id = _create_role_separated_instrument(revision_status.upper())
    _upsert_points(instrument_id, start_date=date(2026, 2, 1))
    assert shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=[
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-02-02",
                "value": "204",
                "currency": "USD",
                "source_ref": f"test:return:{revision_status}-revision",
                "status": revision_status,
            }
        ],
    ) == 1
    market_data = _lock(instrument_id, date(2026, 2, 2))

    assert canonical_series_points(
        market_data,
        instrument_id=instrument_id,
        role="chart",
    )
    assert canonical_series_points(
        market_data,
        instrument_id=instrument_id,
        role="total_return",
    ) == []
    risk_basis = calculation_frequency_profile_for_instruments(
        [instrument_id],
        end_date=date(2026, 2, 2),
        market_data=market_data,
    )
    assert risk_basis["coverage_status"] == "incomplete"
    assert risk_basis["unavailable_instrument_ids"] == [instrument_id]


def test_late_endpoint_never_exposes_old_chart_or_return_points() -> None:
    instrument_id = _create_role_separated_instrument("LATE")
    _upsert_points(instrument_id, start_date=date(2026, 3, 1))
    market_data = _lock(instrument_id, date(2026, 3, 20))

    for role in ("chart", "total_return"):
        assert canonical_series_points(
            market_data,
            instrument_id=instrument_id,
            role=role,
        ) == []


def test_lock_query_count_is_independent_of_history_length() -> None:
    instrument_id = _create_role_separated_instrument("QUERY")
    _upsert_points(
        instrument_id,
        start_date=date(2025, 1, 1),
        count=2,
    )
    engine = get_engine()

    def lock_query_count(as_of_date: date) -> int:
        statements: list[str] = []

        def before_cursor_execute(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            statements.append(str(statement))

        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            _lock(instrument_id, as_of_date)
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)
        return len(statements)

    short_history_queries = lock_query_count(date(2025, 1, 2))
    _upsert_points(
        instrument_id,
        start_date=date(2025, 1, 3),
        count=200,
    )
    long_history_queries = lock_query_count(date(2025, 7, 21))

    assert short_history_queries == long_history_queries
    assert long_history_queries <= 8


def test_chart_and_risk_sources_have_no_flat_detail_or_fallback_symbols() -> None:
    app_root = Path(__file__).resolve().parents[1] / "portfolio_app"
    service_root = app_root / "services"
    source = "\n".join(
        (service_root / filename).read_text(encoding="utf-8")
        for filename in (
            "instrument_charts.py",
            "risk_basis.py",
            "calculation_frequency.py",
        )
    )
    source += "\n" + "\n".join(
        (app_root / "api" / "routes" / filename).read_text(encoding="utf-8")
        for filename in ("positions.py", "taxonomies.py", "workspace.py")
    )
    for banned_symbol in (
        "get_registry_instrument_detail",
        "selected_observation_dates_from_detail",
        "build_instrument_trend_metrics_from_detail",
        "build_instrument_price_chart_from_detail",
        "build_instrument_holdings_market_profile_from_detail",
        "_candidate_chart_bases",
        "services.market_data",
        "detail_loader",
    ):
        assert banned_symbol not in source
