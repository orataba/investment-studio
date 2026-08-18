from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sqlite3
from threading import Barrier

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_ops_instrument_core.instrument_store import (
    StaleFundNavPublicationError,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
REGISTRY_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"
FUND_ID = "fund-nav-concurrency"
FUND_POLICY = {
    "trading": ["official_nav"],
    "valuation": ["official_nav"],
    "total_return": ["total_return_nav"],
    "chart": ["total_return_nav"],
    "reference": ["official_nav"],
}


@dataclass(frozen=True)
class NavDatabase:
    path: Path
    factory: sessionmaker[Session]


@pytest.fixture()
def nav_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> NavDatabase:
    database_path = tmp_path / "fund-nav-concurrency.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = Config(str(REGISTRY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REGISTRY_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url, connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    shared_store.reset_store(
        factory,
        {
            "registry_name": "Fund NAV concurrency tests",
            "instruments": [
                {
                    "instrument_id": FUND_ID,
                    "instrument_name": "Concurrent NAV Fund",
                    "instrument_type": "public_fund",
                    "currency": "CNY",
                    "identifiers": [
                        {
                            "identifier_type": "internal",
                            "identifier_value": "FUND-NAV-CONCURRENCY",
                            "is_primary": True,
                        }
                    ],
                    "quote_selection_policy": FUND_POLICY,
                    "market_data": [],
                }
            ],
        },
    )
    return NavDatabase(path=database_path, factory=factory)


def _official_row(value: str) -> dict[str, object]:
    return {
        "as_of_date": "2026-07-15",
        "nav": value,
        "nav_status": "complete",
        "nav_source_provider": "concurrency-test",
        "nav_lineage": {
            "kind": "provider_explicit",
            "evidence": {"source_field": "unit_nav"},
        },
        "currency": "CNY",
    }


def _unavailable_run(seed: str) -> dict[str, object]:
    return {
        "source_observation_fingerprint": seed * 64,
        "projection_kind": "event_derived",
        "projection_status": "unavailable",
        "method_version": "dividend_reinvestment/v2",
        "anchor_date": None,
        "source_provider": "concurrency-test",
        "evidence": {
            "source_snapshot": seed,
            "unavailable_reason": "no_reinvestment_evidence",
            "published_total_return_dates": [],
            "missing_total_return_dates": ["2026-07-15"],
        },
        "created_by": "pytest",
    }


def _publish(
    database: NavDatabase,
    *,
    value: str,
    seed: str,
    expected_watermark: str | None,
) -> dict[str, object]:
    result = shared_store.publish_fund_nav_history(
        database.factory,
        instrument_id=FUND_ID,
        rows=[_official_row(value)],
        projection_run=_unavailable_run(seed),
        current_fund_nav_event_ids=[],
        current_fund_nav_reinvestment_evidence_ids=[],
        expected_market_data_updated_at=expected_watermark,
        refresh_status="ready",
        updated_by="pytest",
        message="concurrent canonical NAV publish",
    )
    assert result is not None
    return result


def _transaction_state(path: Path) -> tuple[object, ...]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            """
            SELECT quote_basis, as_of_date, value, provider, status,
                   nav_lineage_kind, nav_lineage_evidence_json,
                   fund_nav_adjustment_factor_id
            FROM instrument_market_data
            WHERE instrument_id = ? AND metric_family = 'nav'
            ORDER BY quote_basis, as_of_date
            """,
            (FUND_ID,),
        ).fetchall()
        counts = tuple(
            int(connection.execute(query, (FUND_ID,)).fetchone()[0])
            for query in (
                "SELECT count(*) FROM fund_nav_event WHERE instrument_id = ?",
                "SELECT count(*) FROM fund_nav_reinvestment_evidence "
                "WHERE instrument_id = ?",
                "SELECT count(*) FROM fund_nav_projection_run "
                "WHERE instrument_id = ?",
                "SELECT count(*) FROM fund_nav_adjustment_factor "
                "WHERE instrument_id = ?",
                "SELECT count(*) FROM fund_nav_current_projection "
                "WHERE instrument_id = ?",
            )
        )
        instrument = connection.execute(
            """
            SELECT market_data_updated_at, refresh_status_json
            FROM instrument WHERE instrument_id = ?
            """,
            (FUND_ID,),
        ).fetchone()
    finally:
        connection.close()
    assert instrument is not None
    return rows, counts, instrument


def test_stale_watermark_rejects_the_entire_publication(
    nav_database: NavDatabase,
) -> None:
    stale = shared_store.get_instrument(nav_database.factory, FUND_ID)
    assert stale is not None
    advanced = shared_store.upsert_market_data(
        nav_database.factory,
        instrument_id=FUND_ID,
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 7, 15),
        value="1.20",
        currency="CNY",
        provider="concurrency-test",
        status="complete",
    )
    assert advanced is not None
    assert advanced["market_data_updated_at"] != stale["market_data_updated_at"]
    before = _transaction_state(nav_database.path)

    with pytest.raises(StaleFundNavPublicationError, match="snapshot is stale"):
        _publish(
            nav_database,
            value="1.21",
            seed="a",
            expected_watermark=stale["market_data_updated_at"],
        )

    assert _transaction_state(nav_database.path) == before


def test_competing_publishers_cannot_commit_the_same_source_watermark(
    nav_database: NavDatabase,
) -> None:
    initial = shared_store.get_instrument(nav_database.factory, FUND_ID)
    assert initial is not None
    expected_watermark = initial["market_data_updated_at"]
    start = Barrier(2)

    def compete(candidate: tuple[str, str]) -> tuple[str, object]:
        value, seed = candidate
        start.wait()
        try:
            return "published", _publish(
                nav_database,
                value=value,
                seed=seed,
                expected_watermark=expected_watermark,
            )
        except StaleFundNavPublicationError as error:
            return "stale", error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(compete, [("1.10", "b"), ("1.20", "c")]))

    assert sorted(outcome for outcome, _ in outcomes) == ["published", "stale"]
    published = next(result for outcome, result in outcomes if outcome == "published")
    assert isinstance(published, dict)
    assert published["changed"] is True

    rows, counts, instrument = _transaction_state(nav_database.path)
    assert counts == (0, 0, 1, 0, 1)
    assert len(rows) == 1
    assert rows[0][0] == "official_nav"
    assert rows[0][2] in {"1.10", "1.20"}
    assert instrument[0] == published["market_data_updated_at"]


def test_retry_after_reloading_the_winner_is_exactly_idempotent(
    nav_database: NavDatabase,
) -> None:
    initial = shared_store.get_instrument(nav_database.factory, FUND_ID)
    assert initial is not None
    first = _publish(
        nav_database,
        value="1.10",
        seed="d",
        expected_watermark=initial["market_data_updated_at"],
    )
    before = _transaction_state(nav_database.path)

    replay = _publish(
        nav_database,
        value="1.10",
        seed="d",
        expected_watermark=first["market_data_updated_at"],
    )

    assert replay["changed"] is False
    assert replay["dirty_from"] is None
    assert replay["market_data_updated_at"] == first["market_data_updated_at"]
    assert _transaction_state(nav_database.path) == before
