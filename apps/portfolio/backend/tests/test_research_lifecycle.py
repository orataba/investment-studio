from __future__ import annotations

from datetime import date

from portfolio_app.services import portfolio_store
from portfolio_app.services.research_solver import _build_leaf_target_weight_gaps


WATCH_INSTRUMENT_REF = {
    "instrument_id": "fund-us-watch",
    "instrument_name": "Watchlist Fund",
    "instrument_type": "etf",
    "currency": "USD",
    "identifiers": [
        {
            "identifier_type": "ticker",
            "identifier_value": "WATCH",
            "is_primary": True,
        }
    ],
}

OBSERVED_INSTRUMENT_REF = {
    "instrument_id": "manual-observed",
    "instrument_name": "Manual Observed Fund",
    "instrument_type": "public_fund",
    "currency": "USD",
    "identifiers": [],
}


def _create_watch_trade(transaction_type: str, trade_date: date) -> None:
    portfolio_store.create_transaction(
        portfolio_id="portfolio-ops",
        transaction_type=transaction_type,
        trade_date=trade_date,
        trade_time=None,
        settlement_date=trade_date,
        entitlement_date=None,
        acquisition_date=None,
        account_id="broker-us-core",
        settlement_cash_account_id="cash-usd-main",
        instrument_id="fund-us-watch",
        instrument_ref=WATCH_INSTRUMENT_REF,
        quantity=10,
        price=100,
        gross_amount=1000,
        counter_amount=None,
        fx_rate=None,
        fees=0,
        taxes=0,
        currency="USD",
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note=f"Research lifecycle {transaction_type} fixture",
    )


def _seed_research_lifecycle_rows() -> dict[str, dict[str, object]]:
    portfolio_store.upsert_portfolio_instrument_universe_record(
        "portfolio-ops",
        "manual-observed",
        instrument_ref=OBSERVED_INSTRUMENT_REF,
    )
    _create_watch_trade("buy", date(2026, 4, 16))
    _create_watch_trade("sell", date(2026, 4, 17))
    return {
        str(row["instrument_id"]): row
        for row in portfolio_store.list_portfolio_instrument_universe("portfolio-ops")
    }


def test_research_workbench_derives_held_observed_and_former_lifecycle(client) -> None:
    rows = _seed_research_lifecycle_rows()

    assert rows["equity-us-abbv"]["research_lifecycle"] == "held"
    assert rows["equity-us-abbv"]["research_eligibility"] == "eligible"
    assert rows["manual-observed"]["research_lifecycle"] == "observed"
    assert rows["manual-observed"]["research_eligibility"] == "eligible"
    assert rows["fund-us-watch"]["research_lifecycle"] == "former"
    assert rows["fund-us-watch"]["transaction_count"] == 2
    assert rows["fund-us-watch"]["research_pm_approved"] is False
    assert rows["fund-us-watch"]["research_eligibility"] == "pm_review_required"

    response = client.get("/api/portfolios/portfolio-ops/research/workbench")

    assert response.status_code == 200, response.json()
    workbench_rows = {
        row["instrument_id"]: row
        for row in response.json()["instrument_universe"]
    }
    assert workbench_rows["equity-us-abbv"]["research_lifecycle"] == "held"
    assert workbench_rows["manual-observed"]["research_lifecycle"] == "observed"
    assert workbench_rows["fund-us-watch"]["research_lifecycle"] == "former"
    assert workbench_rows["fund-us-watch"]["research_eligibility"] == "pm_review_required"


def test_former_positive_target_requires_pm_review_until_explicit_approval(client) -> None:
    rows = _seed_research_lifecycle_rows()
    former = rows["fund-us-watch"]
    target_rows = [
        {
            "member_type": "instrument",
            "member_id": "fund-us-watch",
            "label": "Watchlist Fund",
            "current_weight": 0.0,
            "target_weight": 0.05,
        }
    ]

    unapproved_gaps = _build_leaf_target_weight_gaps(
        leaf_target_rows=target_rows,
        base_currency="USD",
        instrument_research_state_by_id={"fund-us-watch": former},
    )

    assert unapproved_gaps[0]["execution_status"] == "manual_review_required"
    assert unapproved_gaps[0]["research_lifecycle"] == "former"
    assert unapproved_gaps[0]["research_eligibility"] == "pm_review_required"
    assert unapproved_gaps[0]["execution_note"] == (
        "Status: Former · Research eligibility: manual PM review required before execution."
    )

    approval_response = client.put(
        "/api/portfolios/portfolio-ops/research/instruments/fund-us-watch/eligibility",
        json={"pm_approved": True},
    )

    assert approval_response.status_code == 200, approval_response.json()
    approved = approval_response.json()
    assert approved["research_lifecycle"] == "former"
    assert approved["research_pm_approved"] is True
    assert approved["research_pm_approved_at"]
    assert approved["research_eligibility"] == "eligible"

    approved_gaps = _build_leaf_target_weight_gaps(
        leaf_target_rows=target_rows,
        base_currency="USD",
        instrument_research_state_by_id={"fund-us-watch": approved},
    )

    assert approved_gaps[0]["execution_status"] == "ready"
    assert approved_gaps[0]["execution_note"] is None
    assert approved_gaps[0]["research_lifecycle"] == "former"
    assert approved_gaps[0]["research_eligibility"] == "eligible"
