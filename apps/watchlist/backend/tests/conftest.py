from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "PORTFOLIO_OPS_WATCHLIST_DATABASE_URL",
    "sqlite+pysqlite:///:memory:",
)
BACKEND_ROOT_STR = str(BACKEND_ROOT)
if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
INSTRUMENT_CORE_PYTHON_STR = str(INSTRUMENT_CORE_PYTHON)
if INSTRUMENT_CORE_PYTHON_STR in sys.path:
    sys.path.remove(INSTRUMENT_CORE_PYTHON_STR)
sys.path.insert(0, INSTRUMENT_CORE_PYTHON_STR)

from portfolio_ops_instrument_core.db_models import InstrumentRegistryBase
from portfolio_ops_instrument_core import instrument_store as shared_store


TEST_NAV_PROVIDER = "watchlist_test_fixture"
TEST_NAV_METHOD_VERSION = "provider-explicit-test-fixture/v1"
FACTOR_QUANTUM = Decimal("0.000000000000000001")


def canonical_quote_policy(instrument_type: str) -> dict[str, list[str]]:
    policies = {
        "public_fund": {
            "trading": ["official_nav"],
            "valuation": ["official_nav"],
            "total_return": ["total_return_nav"],
            "chart": ["total_return_nav"],
            "reference": ["official_nav"],
        },
        "private_fund": {
            "trading": ["official_nav"],
            "valuation": ["official_nav"],
            "total_return": ["total_return_nav"],
            "chart": ["total_return_nav"],
            "reference": ["official_nav"],
        },
        "index": {
            "trading": ["close", "last"],
            "valuation": ["close", "last"],
            "total_return": ["adjusted_close", "close", "last"],
            "chart": ["adjusted_close", "close", "last"],
            "reference": ["close", "last"],
        },
        "etf": {
            "trading": ["last", "close"],
            "valuation": ["close", "last"],
            "total_return": ["adjusted_close", "close", "last"],
            "chart": ["adjusted_close", "close", "last"],
            "reference": ["close", "last"],
        },
        "equity": {
            "trading": ["last", "close"],
            "valuation": ["close", "last"],
            "total_return": ["adjusted_close", "close", "last"],
            "chart": ["adjusted_close", "close", "last"],
            "reference": ["close", "last"],
        },
    }
    return deepcopy(policies[instrument_type])

TEST_SHARED_INSTRUMENTS = {
    "fund-us-agg": {
        "instrument_id": "fund-us-agg",
        "instrument_name": "iShares Core U.S. Aggregate Bond ETF",
        "instrument_type": "etf",
        "currency": "USD",
        "exchange_code": "XNAS",
        "quote_selection_policy": canonical_quote_policy("etf"),
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "AGG", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-04-15",
                "value": "96.8200",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            }
        ],
        "lifecycle_state": {"status": "active"},
    },
    "sxv264": {
        "instrument_id": "sxv264",
        "instrument_name": "SXV264 Total Return Fund",
        "instrument_type": "private_fund",
        "currency": "USD",
        "quote_selection_policy": canonical_quote_policy("private_fund"),
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "SXV264", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2025-12-31",
                "value": "97.500000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-03-14",
                "value": "99.000000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-04-07",
                "value": "100.000000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-04-14",
                "value": "101.236476",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            },
        ],
        "lifecycle_state": {"status": "active"},
    },
    "savf63": {
        "instrument_id": "savf63",
        "instrument_name": "SAVF63 Short Duration Income Fund",
        "instrument_type": "public_fund",
        "currency": "USD",
        "quote_selection_policy": canonical_quote_policy("public_fund"),
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "SAVF63", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-04-14",
                "value": "99.870000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            },
        ],
        "lifecycle_state": {"status": "active"},
    },
}


def _nav_points(instrument: dict[str, object]) -> list[dict[str, object]]:
    return [
        deepcopy(point)
        for point in instrument.get("market_data", [])
        if isinstance(point, dict)
        and str(point.get("metric_family") or "").strip().lower() == "nav"
    ]


def _instrument_with_official_nav_only(
    instrument: dict[str, object],
) -> dict[str, object]:
    """Build the reset-store seed without ever inserting total_return_nav."""

    seeded = deepcopy(instrument)
    market_data: list[dict[str, object]] = []
    official_dates = {
        str(point.get("as_of_date") or "")
        for point in _nav_points(instrument)
        if str(point.get("quote_basis") or "").strip().lower() == "official_nav"
    }
    for raw_point in instrument.get("market_data", []):
        if not isinstance(raw_point, dict):
            continue
        point = deepcopy(raw_point)
        if str(point.get("metric_family") or "").strip().lower() != "nav":
            market_data.append(point)
            continue
        quote_basis = str(point.get("quote_basis") or "").strip().lower()
        if quote_basis == "official_nav":
            point["nav_lineage"] = {
                "kind": "provider_explicit",
                "evidence": {"source_field": "test_unit_nav"},
            }
            market_data.append(point)
            continue
        if quote_basis != "total_return_nav":
            continue
        if str(point.get("as_of_date") or "") in official_dates:
            continue
        point["quote_basis"] = "official_nav"
        point["nav_lineage"] = {
            "kind": "provider_explicit",
            "evidence": {"source_field": "test_unit_nav"},
        }
        market_data.append(point)
    seeded["market_data"] = market_data
    return seeded


def _fixture_source_fingerprint(nav_points: list[dict[str, object]]) -> str:
    canonical = json.dumps(
        nav_points,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def publish_provider_explicit_nav(
    session_factory,
    instrument: dict[str, object],
) -> None:
    instrument_id = str(instrument["instrument_id"])
    currency = str(instrument["currency"]).upper()
    raw_nav_points = _nav_points(instrument)
    if not raw_nav_points:
        return

    official_by_date: dict[str, dict[str, object]] = {}
    total_by_date: dict[str, dict[str, object]] = {}
    for point in raw_nav_points:
        as_of_date = str(point.get("as_of_date") or "")
        quote_basis = str(point.get("quote_basis") or "").strip().lower()
        if quote_basis == "official_nav":
            official_by_date[as_of_date] = point
        elif quote_basis == "total_return_nav":
            total_by_date[as_of_date] = point

    # Provider-explicit total return still requires same-date unit NAV.  Tests
    # that historically supplied only the total curve use the same value as the
    # unit observation, which yields a real provider-implied factor of exactly 1.
    for as_of_date, total_point in total_by_date.items():
        official_by_date.setdefault(as_of_date, deepcopy(total_point))

    rows: list[dict[str, object]] = []
    factors: list[dict[str, object]] = []
    anchor_date = min(total_by_date) if total_by_date else None
    for as_of_date in sorted(official_by_date):
        official = official_by_date[as_of_date]
        unit_value = str(official["value"])
        official_status = str(official.get("status") or "complete")
        row: dict[str, object] = {
            "as_of_date": as_of_date,
            "currency": currency,
            "nav": unit_value,
            "nav_status": official_status,
            "nav_source_provider": str(official.get("provider") or TEST_NAV_PROVIDER),
            "nav_lineage": {
                "kind": "provider_explicit",
                "evidence": {"source_field": "test_unit_nav"},
            },
        }
        total = total_by_date.get(as_of_date)
        if total is not None:
            total_status = str(total.get("status") or "complete")
            if official_status != "complete" or total_status != "complete":
                raise ValueError(
                    "Provider-explicit fixture totals require complete same-date unit NAV."
                )
            total_value = str(total["value"])
            factor_level = (Decimal(total_value) / Decimal(unit_value)).quantize(
                FACTOR_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            factor_logical_key = f"provider-total:{as_of_date}"
            row.update(
                {
                    "nav_with_dividend": total_value,
                    "nav_with_dividend_status": "complete",
                    "nav_with_dividend_source_provider": str(
                        total.get("provider") or TEST_NAV_PROVIDER
                    ),
                    "nav_with_dividend_lineage": {
                        "kind": "provider_explicit",
                        "evidence": {
                            "source_field": "test_total_return_nav",
                            "factor_logical_key": factor_logical_key,
                        },
                    },
                }
            )
            factors.append(
                {
                    "factor_logical_key": factor_logical_key,
                    "as_of_date": as_of_date,
                    "factor_level": str(factor_level),
                    "factor_kind": "provider_implied",
                    "evidence_kind": "provider_total_return",
                    "method_version": TEST_NAV_METHOD_VERSION,
                    "anchor_date": as_of_date,
                    "source_provider": str(total.get("provider") or TEST_NAV_PROVIDER),
                    "evidence": {"source_field": "test_total_return_nav"},
                }
            )
        rows.append(row)

    detail = shared_store.get_instrument(session_factory, instrument_id)
    assert detail is not None
    current_events = list(detail.get("fund_nav_events", []))
    current_evidence = list(detail.get("fund_nav_reinvestment_evidence", []))
    has_total_return = bool(total_by_date)
    complete_unit_dates = sorted(
        as_of_date
        for as_of_date, point in official_by_date.items()
        if str(point.get("status") or "complete") == "complete"
    )
    published_total_dates = sorted(total_by_date)
    missing_total_dates = sorted(
        set(complete_unit_dates).difference(published_total_dates)
    )
    projection_status = (
        "unavailable"
        if not has_total_return
        else "partial"
        if missing_total_dates
        else "complete"
    )
    result = shared_store.publish_fund_nav_history(
        session_factory,
        instrument_id=instrument_id,
        rows=rows,
        projection_run={
            "source_observation_fingerprint": _fixture_source_fingerprint(raw_nav_points),
            "projection_kind": "provider_explicit",
            "projection_status": projection_status,
            "method_version": TEST_NAV_METHOD_VERSION,
            "anchor_date": anchor_date,
            "source_provider": TEST_NAV_PROVIDER,
            "evidence": (
                {
                    "fixture_contract": "provider_explicit_total_return/v1",
                    "observation_count": len(raw_nav_points),
                    "published_total_return_dates": published_total_dates,
                    "missing_total_return_dates": missing_total_dates,
                }
                if has_total_return
                else {
                    "unavailable_reason": "fixture_has_unit_nav_only",
                    "observation_count": len(raw_nav_points),
                    "published_total_return_dates": [],
                    "missing_total_return_dates": complete_unit_dates,
                }
            ),
            "created_by": "watchlist_test_fixture",
        },
        current_fund_nav_event_ids=[
            str(item["fund_nav_event_id"]) for item in current_events
        ],
        current_fund_nav_reinvestment_evidence_ids=[
            str(item["fund_nav_reinvestment_evidence_id"])
            for item in current_evidence
        ],
        adjustment_factors=factors,
        expected_market_data_updated_at=detail.get("market_data_updated_at"),
        refresh_status="complete",
        updated_by="watchlist_test_fixture",
        message="Publish factor-backed provider-explicit test NAV history.",
    )
    assert result is not None


def _rekey_fresh_test_instrument(
    session_factory,
    *,
    generated_instrument_id: str,
    target_instrument_id: str,
    identifiers: list[dict[str, object]],
) -> None:
    """Assign an explicit fixture id before any market-data ledger is written."""

    from portfolio_ops_instrument_core.db_models import Instrument, InstrumentIdentifier

    with session_factory() as session:
        source = session.get(Instrument, generated_instrument_id)
        assert source is not None
        assert not source.market_data_points
        record = {
            "instrument_name": source.instrument_name,
            "instrument_type": source.instrument_type,
            "currency": source.currency,
            "exchange_code": source.exchange_code,
            "quote_selection_policy_json": deepcopy(source.quote_selection_policy_json),
            "source_settings_json": deepcopy(source.source_settings_json),
            "refresh_status_json": deepcopy(source.refresh_status_json),
            "lifecycle_state_json": deepcopy(source.lifecycle_state_json),
            "market_data_updated_at": source.market_data_updated_at,
        }
        session.delete(source)
        session.flush()
        session.add(Instrument(instrument_id=target_instrument_id, **record))
        session.flush()
        for identifier in identifiers:
            session.add(
                InstrumentIdentifier(
                    instrument_id=target_instrument_id,
                    identifier_type=str(identifier["identifier_type"]),
                    identifier_value=str(identifier["identifier_value"]),
                    is_primary=bool(identifier.get("is_primary")),
                )
            )
        session.commit()


def seed_shared_instrument(instrument: dict[str, object]) -> None:
    from watchlist_app.db import session as session_module

    target_instrument_id = str(instrument["instrument_id"])
    session_factory = session_module.get_session_factory()
    existing = shared_store.get_instrument(session_factory, target_instrument_id)
    if existing is None:
        identifiers = [
            deepcopy(identifier)
            for identifier in instrument.get("identifiers", [])
            if isinstance(identifier, dict)
        ]
        created = shared_store.create_instrument(
            session_factory,
            instrument_name=str(instrument["instrument_name"]),
            instrument_type=str(instrument["instrument_type"]),
            currency=str(instrument["currency"]),
            exchange_code=(
                str(instrument["exchange_code"])
                if instrument.get("exchange_code")
                else None
            ),
            identifiers=identifiers,
            quote_selection_policy=deepcopy(instrument.get("quote_selection_policy")),
        )
        generated_instrument_id = str(created["instrument_id"])
        if generated_instrument_id != target_instrument_id:
            _rekey_fresh_test_instrument(
                session_factory,
                generated_instrument_id=generated_instrument_id,
                target_instrument_id=target_instrument_id,
                identifiers=identifiers,
            )
    else:
        assert existing["instrument_name"] == instrument["instrument_name"]
        assert existing["instrument_type"] == instrument["instrument_type"]
        assert existing["currency"] == str(instrument["currency"]).upper()
        shared_store.upsert_quote_selection_policy(
            session_factory,
            instrument_id=target_instrument_id,
            quote_selection_policy=deepcopy(instrument["quote_selection_policy"]),
        )

    source_settings = instrument.get("source_settings")
    if isinstance(source_settings, dict):
        shared_store.upsert_source_settings(
            session_factory,
            instrument_id=target_instrument_id,
            source_mode=str(source_settings.get("source_mode") or "manual"),
            source_email=str(source_settings.get("source_email") or ""),
            source_location=str(
                source_settings.get("source_location") or "Shared data ops"
            ),
            source_api_profile=str(source_settings.get("source_api_profile") or ""),
            source_email_rules=list(source_settings.get("source_email_rules") or []),
            expected_frequency=(
                str(source_settings["expected_frequency"])
                if source_settings.get("expected_frequency") is not None
                else None
            ),
            market_calendar=source_settings.get("market_calendar"),
            release_lag_days=(
                int(source_settings["release_lag_days"])
                if source_settings.get("release_lag_days") is not None
                else None
            ),
        )

    non_nav_rows = [
        {
            key: deepcopy(point[key])
            for key in (
                "metric_family",
                "quote_basis",
                "as_of_date",
                "value",
                "currency",
                "provider",
                "status",
                "nav_lineage",
            )
            if key in point
        }
        for point in instrument.get("market_data", [])
        if isinstance(point, dict)
        and str(point.get("metric_family") or "").strip().lower() != "nav"
    ]
    if non_nav_rows:
        shared_store.upsert_market_data_points(
            session_factory,
            instrument_id=target_instrument_id,
            rows=non_nav_rows,
        )
    publish_provider_explicit_nav(session_factory, instrument)

    lifecycle_state = instrument.get("lifecycle_state")
    if isinstance(lifecycle_state, dict):
        status = str(lifecycle_state.get("status") or "active")
        shared_store.set_instrument_lifecycle_state(
            session_factory,
            instrument_id=target_instrument_id,
            status=status,
            updated_by="watchlist_test_fixture",
        )
        canonical_instrument_id = lifecycle_state.get("canonical_instrument_id")
        if canonical_instrument_id:
            # Canonical alias topology has no public authoring API.  Keep this
            # fixture-only mutation narrow and do not touch NAV ledger tables.
            from portfolio_ops_instrument_core.db_models import Instrument

            with session_factory() as session:
                target = session.get(Instrument, target_instrument_id)
                assert target is not None
                target.lifecycle_state_json = deepcopy(lifecycle_state)
                session.commit()


def mutate_shared_instrument_metadata_for_drift(
    *,
    instrument_id: str,
    instrument_name: str,
    identifiers: list[dict[str, object]],
) -> None:
    """Simulate Registry master-data drift without rebuilding its NAV ledger."""

    from watchlist_app.db import session as session_module
    from portfolio_ops_instrument_core.db_models import Instrument, InstrumentIdentifier

    with session_module.get_session_factory()() as session:
        target = session.get(Instrument, instrument_id)
        assert target is not None
        target.instrument_name = instrument_name
        for identifier in list(target.identifiers):
            session.delete(identifier)
        session.flush()
        for identifier in identifiers:
            session.add(
                InstrumentIdentifier(
                    instrument_id=instrument_id,
                    identifier_type=str(identifier["identifier_type"]),
                    identifier_value=str(identifier["identifier_value"]),
                    is_primary=bool(identifier.get("is_primary")),
                )
            )
        target.market_data_updated_at = "2099-01-01T00:00:00+00:00"
        session.commit()


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database_path = tmp_path / "test.db"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_EMAIL_SYNC_ENABLED", "false")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_RECALC_WORKER_ENABLED", "false")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DOCUMENT_STORAGE_ROOT", str(tmp_path / "documents"))

    from watchlist_app.core import settings as settings_module
    from watchlist_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_alembic_upgrade(f"sqlite+pysqlite:///{database_path}")
    InstrumentRegistryBase.metadata.create_all(bind=session_module.get_engine())
    shared_store.reset_store(
        session_module.get_session_factory(),
        {
            "registry_name": shared_store.DEFAULT_REGISTRY_NAME,
            "instruments": [
                _instrument_with_official_nav_only(instrument)
                for instrument in TEST_SHARED_INSTRUMENTS.values()
            ],
        },
    )
    for instrument in TEST_SHARED_INSTRUMENTS.values():
        publish_provider_explicit_nav(
            session_module.get_session_factory(),
            instrument,
        )

    import watchlist_app.main as main_module

    main_module = importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        yield test_client

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
