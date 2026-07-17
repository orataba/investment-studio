from __future__ import annotations

from datetime import date
from decimal import Decimal

from portfolio_ops_instrument_core.db_models import FundNavEvent

from portfolio_app.db.models import PortfolioInstrumentUniverseRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_event_tasks import (
    reconcile_instrument_event_tasks,
)


def _insert_distribution_revision(
    *,
    event_id: str,
    revision_number: int,
    revision_kind: str,
    supersedes_event_id: str | None,
    cash_per_unit: str,
) -> None:
    timestamp = f"2026-04-{10 + revision_number:02d}T08:00:00Z"
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            FundNavEvent(
                fund_nav_event_id=event_id,
                fund_nav_action_id="fund-us-agg-distribution-20260410",
                revision_number=revision_number,
                revision_kind=revision_kind,
                supersedes_fund_nav_event_id=supersedes_event_id,
                instrument_id="fund-us-agg",
                event_type="cash_distribution",
                announcement_date=date(2026, 4, 8),
                record_date=date(2026, 4, 10),
                effective_date=date(2026, 4, 10),
                payable_date=date(2026, 4, 15),
                sequence_order=1,
                cash_per_unit=Decimal(cash_per_unit),
                unit_ratio=None,
                evidence_kind="manual_verified",
                source="test-admin",
                external_event_id=None,
                provenance_json={"test": "portfolio-event-task"},
                recorded_by="test-admin",
                revision_reason=f"test revision {revision_number}",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.commit()


def test_no_confirmed_distribution_keeps_unit_nav_portfolio_return_available(client) -> None:
    task_response = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    )
    assert task_response.status_code == 200
    assert task_response.json()["accounting_policy"] == (
        "official_unit_nav_assume_no_unrecorded_distribution"
    )
    assert task_response.json()["tasks"] == []

    performance_response = client.get("/api/portfolios/portfolio-ops/performance")
    assert performance_response.status_code == 200
    assert performance_response.json()["summary"]["cumulative_twr"] is not None


def test_confirmed_distribution_creates_pending_task_without_posting_transactions(client) -> None:
    _insert_distribution_revision(
        event_id="fund-nav-event-1",
        revision_number=1,
        revision_kind="original",
        supersedes_event_id=None,
        cash_per_unit="0.1",
    )
    before = client.get("/api/portfolios/portfolio-ops/transactions").json()[
        "summary"
    ]["total_transactions"]

    response = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accounting_policy"] == (
        "official_unit_nav_assume_no_unrecorded_distribution"
    )
    assert payload["attention_count"] == 1
    assert len(payload["tasks"]) == 1
    task = payload["tasks"][0]
    assert task["status"] == "pending"
    assert task["account_id"] == "broker-us-core"
    assert Decimal(str(task["entitled_quantity"])) == Decimal("304.236000000000")
    assert Decimal(str(task["expected_gross_amount"])) == Decimal("30.4236000000000")
    after = client.get("/api/portfolios/portfolio-ops/transactions").json()[
        "summary"
    ]["total_transactions"]
    assert after == before


def test_historical_transaction_universe_still_receives_distribution_task(client) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        universe_record = session.get(
            PortfolioInstrumentUniverseRecordModel,
            ("portfolio-ops", "fund-us-agg"),
        )
        assert universe_record is not None
        universe_record.status = "inactive"
        session.commit()

    _insert_distribution_revision(
        event_id="fund-nav-event-1",
        revision_number=1,
        revision_kind="original",
        supersedes_event_id=None,
        cash_per_unit="0.1",
    )
    reconciliation = reconcile_instrument_event_tasks(
        instrument_ids=["fund-us-agg"]
    )
    assert "portfolio-ops" in reconciliation

    response = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    )
    assert response.status_code == 200
    assert len(response.json()["tasks"]) == 1


def test_human_transaction_resolves_task_and_registry_correction_reopens_it(client) -> None:
    _insert_distribution_revision(
        event_id="fund-nav-event-1",
        revision_number=1,
        revision_kind="original",
        supersedes_event_id=None,
        cash_per_unit="0.1",
    )
    task = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    ).json()["tasks"][0]
    transaction_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg",
            "gross_amount": 30.42,
            "fees": 0,
            "taxes": 0,
            "currency": "USD",
        },
    )
    assert transaction_response.status_code == 200
    transaction_id = transaction_response.json()["transaction_id"]

    review_response = client.post(
        "/api/portfolios/portfolio-ops/instrument-event-tasks/"
        f"{task['instrument_event_task_id']}/reviews",
        json={
            "decision": "processed",
            "transaction_ids": [transaction_id],
            "note": "Verified cash distribution.",
            "reviewed_by": "test-admin",
            "expected_row_version": task["row_version"],
        },
    )
    assert review_response.status_code == 200
    assert review_response.json()["status"] == "processed"
    assert review_response.json()["linked_transactions"][0]["transaction_id"] == (
        transaction_id
    )
    assert client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    ).json()["tasks"] == []

    _insert_distribution_revision(
        event_id="fund-nav-event-2",
        revision_number=2,
        revision_kind="correction",
        supersedes_event_id="fund-nav-event-1",
        cash_per_unit="0.11",
    )
    reopened = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    ).json()["tasks"]
    assert len(reopened) == 1
    assert reopened[0]["status"] == "needs_review"
    assert reopened[0]["current_event_revision_id"] == "fund-nav-event-2"
    assert reopened[0]["reviewed_event_revision_id"] == "fund-nav-event-1"


def test_cancellation_never_mutates_linked_transaction_and_requires_review(client) -> None:
    _insert_distribution_revision(
        event_id="fund-nav-event-1",
        revision_number=1,
        revision_kind="original",
        supersedes_event_id=None,
        cash_per_unit="0.1",
    )
    task = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    ).json()["tasks"][0]
    transaction = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": "broker-us-core",
            "instrument_id": "fund-us-agg",
            "quantity": 0.3,
            "gross_amount": 30.42,
            "currency": "USD",
        },
    )
    assert transaction.status_code == 200
    transaction_id = transaction.json()["transaction_id"]
    assert client.post(
        "/api/portfolios/portfolio-ops/instrument-event-tasks/"
        f"{task['instrument_event_task_id']}/reviews",
        json={
            "decision": "processed",
            "transaction_ids": [transaction_id],
            "note": "Verified reinvestment.",
            "reviewed_by": "test-admin",
            "expected_row_version": task["row_version"],
        },
    ).status_code == 200

    _insert_distribution_revision(
        event_id="fund-nav-event-2",
        revision_number=2,
        revision_kind="cancellation",
        supersedes_event_id="fund-nav-event-1",
        cash_per_unit="0.1",
    )
    attention = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    ).json()["tasks"]
    assert len(attention) == 1
    assert attention[0]["status"] == "needs_review"
    assert attention[0]["source_event_state"] == "cancelled"
    persisted_transaction = client.get(
        f"/api/portfolios/portfolio-ops/transactions?instrument_id=fund-us-agg"
    ).json()["transactions"]
    assert any(item["transaction_id"] == transaction_id for item in persisted_transaction)


def test_processed_review_rejects_wrong_distribution_amount(client) -> None:
    _insert_distribution_revision(
        event_id="fund-nav-event-1",
        revision_number=1,
        revision_kind="original",
        supersedes_event_id=None,
        cash_per_unit="0.1",
    )
    task = client.get(
        "/api/portfolios/portfolio-ops/instrument-event-tasks?attention_only=true"
    ).json()["tasks"][0]
    transaction = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg",
            "gross_amount": 99,
            "currency": "USD",
        },
    )
    assert transaction.status_code == 200

    response = client.post(
        "/api/portfolios/portfolio-ops/instrument-event-tasks/"
        f"{task['instrument_event_task_id']}/reviews",
        json={
            "decision": "processed",
            "transaction_ids": [transaction.json()["transaction_id"]],
            "note": "Attempt wrong amount.",
            "reviewed_by": "test-admin",
            "expected_row_version": task["row_version"],
        },
    )
    assert response.status_code == 409
    assert "does not match entitled quantity" in response.json()["detail"]
