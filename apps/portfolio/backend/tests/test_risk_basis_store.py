from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from portfolio_app.services import risk_basis, risk_basis_store
from tests.test_postgres_instrument_registry_constraints import postgres_portfolio_env  # noqa: F401


END_DATE = date(2026, 7, 28)


def _point(day: date, *, basis="adjusted_close", **changes):
    return {
        "as_of_date": day,
        "metric_family": "price",
        "quote_basis": basis,
        "value": "100",
        "currency": "USD",
        "status": "complete",
        "price_unit": "per_unit",
        "price_scale": 1,
        **changes,
    }


def _recent_points(**changes):
    return [_point(END_DATE - timedelta(days=offset), **changes) for offset in (1, 0)]


def _registry_tables():
    # Deliberately omit write constraints so legacy/corrupt series can exercise
    # the reader's refusal rules, including a bad observation before lookback.
    metadata = sa.MetaData()
    instruments = sa.Table(
        "instrument", metadata,
        sa.Column("instrument_id", sa.String, primary_key=True),
        sa.Column("instrument_type", sa.String),
        sa.Column("currency", sa.String),
        sa.Column("quote_selection_policy_json", sa.JSON),
        sa.Column("source_settings_json", sa.JSON),
    )
    points = sa.Table(
        "instrument_market_data", metadata,
        sa.Column("instrument_market_data_id", sa.Integer, primary_key=True),
        sa.Column("instrument_id", sa.String),
        sa.Column("as_of_date", sa.Date),
        sa.Column("metric_family", sa.String),
        sa.Column("quote_basis", sa.String),
        sa.Column("value", sa.Text),
        sa.Column("currency", sa.String),
        sa.Column("status", sa.String),
        sa.Column("price_unit", sa.String),
        sa.Column("price_scale", sa.Float),
    )
    return metadata, instruments, points


def _insert_details(connection, instruments, points, details):
    for instrument_id, detail in details.items():
        connection.execute(instruments.insert().values(
            instrument_id=instrument_id,
            instrument_type=detail["instrument_type"],
            currency=detail["currency"],
            quote_selection_policy_json=detail["quote_selection_policy"],
            source_settings_json=detail.get("source_settings", {}),
        ))
        if detail["market_data"]:
            connection.execute(points.insert(), [
                {"instrument_id": instrument_id, **point}
                for point in detail["market_data"]
            ])


@pytest.fixture
def registry_rows(monkeypatch):
    metadata, instruments, points = _registry_tables()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    monkeypatch.setattr(risk_basis_store, "get_session_factory", lambda: sessionmaker(engine))

    def insert(details):
        with engine.begin() as connection:
            _insert_details(connection, instruments, points, details)

    yield insert
    engine.dispose()


CASES = [
    pytest.param(_recent_points(), {}, id="daily-complete"),
    pytest.param([_point(date(2023, 1, 1)), *_recent_points(basis="close")], {}, id="preferred-before-lookback-does-not-fallback"),
    pytest.param([_point(END_DATE + timedelta(days=1)), *_recent_points(basis="close")], {}, id="preferred-only-in-future"),
    pytest.param([*_recent_points(status="partial"), *_recent_points(basis="close")], {}, id="partial-preferred-ignored"),
    pytest.param([*_recent_points(status="unavailable"), *_recent_points(basis="close")], {}, id="unavailable-preferred-ignored"),
    pytest.param([*_recent_points(), _point(END_DATE, currency="usd")], {}, id="duplicate-normalized-date"),
    pytest.param([*_recent_points(), _point(END_DATE - timedelta(days=2), currency="HKD")], {}, id="mixed-currency-identity"),
    pytest.param(_recent_points(currency="HKD"), {}, id="instrument-currency-mismatch"),
    pytest.param([*_recent_points(), _point(END_DATE - timedelta(days=2), metric_family="nav")], {}, id="mixed-metric-identity"),
    pytest.param(_recent_points(metric_family="nav"), {}, id="wrong-metric-family"),
    pytest.param(_recent_points(price_unit="rate"), {}, id="wrong-price-unit"),
    pytest.param(_recent_points(price_scale=100), {}, id="wrong-price-scale"),
    pytest.param([*_recent_points(), _point(END_DATE - timedelta(days=2), price_scale=2)], {}, id="mixed-price-scale"),
    pytest.param(_recent_points(price_scale=None), {}, id="missing-price-scale"),
    pytest.param(_recent_points(value="1e-20"), {}, id="small-finite-positive-value"),
    pytest.param(_recent_points(value="1e100"), {}, id="large-finite-positive-value"),
    pytest.param([*_recent_points(), _point(END_DATE + timedelta(days=1), value="NaN")], {}, id="invalid-future-observation-ignored"),
    pytest.param([*_recent_points(), _point(END_DATE - timedelta(days=2), value="NaN", status="partial")], {}, id="invalid-unusable-observation-ignored"),
    pytest.param(_recent_points(basis="official_nav", metric_family="nav"), {"instrument_type": "public_fund", "quote_selection_policy": {"total_return": ["official_nav"]}}, id="fund-nav-not-total-return"),
    pytest.param(_recent_points(basis="total_return_nav", metric_family="nav"), {"instrument_type": "public_fund", "quote_selection_policy": {"total_return": ["total_return_nav", "official_nav"]}}, id="fund-total-return"),
    pytest.param(_recent_points(), {"quote_selection_policy": {"total_return": ["unknown", "adjusted_close"]}}, id="unsupported-policy-basis"),
    pytest.param([], {}, id="existing-without-observations"),
    pytest.param([_point(END_DATE)], {}, id="insufficient-observations"),
    pytest.param([_point(END_DATE - timedelta(days=15)), _point(END_DATE)], {}, id="calendar-day-gap"),
    pytest.param([_point(END_DATE - timedelta(days=15)), _point(END_DATE)], {"source_settings": {"expected_frequency": "event_driven"}}, id="event-driven-gap-allowed"),
    *[
        pytest.param(
            [_point(date(2023, 1, 1), value=value), *_recent_points(), *_recent_points(basis="close")],
            {}, id=f"old-invalid-{value}",
        )
        for value in ("bad", "NaN", "Infinity", "0", "-1", "1e400", "1e-400")
    ],
]


def _detail(market_data, changes):
    return {
        "instrument_type": "equity",
        "currency": "USD",
        "quote_selection_policy": {"total_return": ["adjusted_close", "close"]},
        "source_settings": {},
        "market_data": market_data,
        **changes,
    }


@pytest.mark.parametrize("market_data,changes", CASES)
def test_registry_profile_equals_full_history_resolution(registry_rows, market_data, changes):
    details = {"instrument": _detail(market_data, changes)}
    registry_rows(details)
    ids = [" instrument ", "missing", "instrument", ""]
    expected = risk_basis.calculation_frequency_profile_for_instruments(
        ids, end_date=END_DATE, detail_loader=details.get,
    )
    assert risk_basis_store.calculation_frequency_profile_from_registry(
        ids, end_date=END_DATE,
    ) == expected


def test_registry_profile_preserves_market_calendar_gap_details(registry_rows, monkeypatch):
    detail = {
        "instrument_type": "equity",
        "currency": "USD",
        "quote_selection_policy": {"total_return": ["adjusted_close"]},
        "source_settings": {"market_calendar": "XSHG"},
        "market_data": [_point(date(2026, 7, 23)), _point(END_DATE)],
    }
    details = {"calendar": detail}
    registry_rows(details)
    monkeypatch.setattr(risk_basis, "_market_calendar_sessions", lambda *args: (
        date(2026, 7, 23), date(2026, 7, 24), date(2026, 7, 27), END_DATE,
    ))
    assert risk_basis_store.calculation_frequency_profile_from_registry(
        ["calendar"], end_date=END_DATE,
    ) == risk_basis.calculation_frequency_profile_for_instruments(
        ["calendar"], end_date=END_DATE, detail_loader=details.get,
    )


@pytest.mark.postgresql_integration
def test_postgres_registry_profiles_match_full_history_resolution(postgres_portfolio_env, monkeypatch):
    # Reuse the existing fixture's random database and cleanup. A test-only
    # schema permits corrupt-series checks without disabling canonical guards.
    engine = sa.create_engine(
        postgres_portfolio_env["database_url"],
        connect_args={"options": "-csearch_path=risk_basis_test,public"},
    )
    metadata, instruments, points = _registry_tables()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE SCHEMA risk_basis_test")
        metadata.create_all(engine)
        monkeypatch.setattr(risk_basis_store, "get_session_factory", lambda: sessionmaker(engine))
        for index, test_case in enumerate(CASES):
            market_data, changes = test_case.values
            instrument_id = f"case-{index}"
            details = {instrument_id: _detail(market_data, changes)}
            with engine.begin() as connection:
                _insert_details(connection, instruments, points, details)
            ids = [instrument_id, "missing"]
            assert risk_basis_store.calculation_frequency_profile_from_registry(
                ids, end_date=END_DATE,
            ) == risk_basis.calculation_frequency_profile_for_instruments(
                ids, end_date=END_DATE, detail_loader=details.get,
            ), test_case.id
    finally:
        engine.dispose()
