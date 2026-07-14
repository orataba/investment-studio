from __future__ import annotations

import pytest

from portfolio_app.db.session import get_session_factory
from portfolio_app.services import portfolio_store


def test_reset_store_rejects_destructive_reset_after_ledger_initialization() -> None:
    before_ids = {
        item["transaction_id"]
        for item in portfolio_store.list_transactions("portfolio-ops")
    }

    with pytest.raises(
        RuntimeError,
        match="append-only transaction ledger",
    ):
        portfolio_store.reset_store()

    assert {
        item["transaction_id"]
        for item in portfolio_store.list_transactions("portfolio-ops")
    } == before_ids


def test_database_load_round_trips_manual_instrument_universe() -> None:
    instrument_ref = {
        "instrument_id": "fund-us-watch",
        "instrument_name": "Watchlist Fund",
        "instrument_type": "fund",
        "currency": "USD",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "WATCH",
                "is_primary": True,
            }
        ],
    }
    persisted = portfolio_store.upsert_portfolio_instrument_universe_record(
        "portfolio-ops",
        "fund-us-watch",
        instrument_ref=instrument_ref,
    )
    assert persisted is not None

    session_factory = get_session_factory()
    with session_factory() as session:
        loaded = portfolio_store._load_store_from_db(session)
    loaded_row = next(
        row
        for row in loaded["instrument_universe"]
        if row["portfolio_id"] == "portfolio-ops"
        and row["instrument_id"] == "fund-us-watch"
    )
    assert loaded_row["source"] == "manual"
    assert loaded_row["holding_state"] == "not_held"
    assert loaded_row["instrument_ref"]["instrument_name"] == "Watchlist Fund"


def test_portfolio_archive_preserves_facts_and_restore_reactivates() -> None:
    transaction_ids_before = {
        row["transaction_id"]
        for row in portfolio_store.list_transactions("portfolio-ops")
    }
    portfolio_store.create_portfolio(
        "Archive Guard",
        base_currency="CNY",
        operating_profile="standard_taxonomy",
    )

    archived = portfolio_store.archive_portfolio("portfolio-ops")

    assert archived is not None
    assert archived["lifecycle_status"] == "archived"
    assert "portfolio-ops" not in {
        row["portfolio_id"] for row in portfolio_store.list_portfolios()
    }
    assert next(
        row
        for row in portfolio_store.list_portfolios(include_archived=True)
        if row["portfolio_id"] == "portfolio-ops"
    )["lifecycle_status"] == "archived"
    assert {
        row["transaction_id"]
        for row in portfolio_store.list_transactions("portfolio-ops")
    } == transaction_ids_before

    restored = portfolio_store.restore_portfolio("portfolio-ops")

    assert restored is not None
    assert restored["lifecycle_status"] == "active"
    active = portfolio_store.list_portfolios()
    assert {row["portfolio_id"] for row in active} >= {
        "portfolio-ops",
        "archive-guard",
    }
    assert sorted(int(row["sort_order"]) for row in active) == list(range(len(active)))


def test_portfolio_archive_rejects_removing_last_active_portfolio() -> None:
    with pytest.raises(ValueError, match="At least one active portfolio must remain"):
        portfolio_store.archive_portfolio("portfolio-ops")


def test_portfolio_reorder_requires_an_exact_active_permutation() -> None:
    created = portfolio_store.create_portfolio(
        "Reorder Guard",
        base_currency="CNY",
        operating_profile="standard_taxonomy",
    )
    created_id = str(created["portfolio_id"])

    with pytest.raises(ValueError, match="duplicate ids"):
        portfolio_store.reorder_portfolios(["portfolio-ops", "portfolio-ops"])
    with pytest.raises(ValueError, match="exact permutation"):
        portfolio_store.reorder_portfolios(["portfolio-ops"])

    reordered = portfolio_store.reorder_portfolios(
        [created_id, "portfolio-ops"]
    )

    assert [row["portfolio_id"] for row in reordered] == [
        created_id,
        "portfolio-ops",
    ]
    assert [row["sort_order"] for row in reordered] == [0, 1]
