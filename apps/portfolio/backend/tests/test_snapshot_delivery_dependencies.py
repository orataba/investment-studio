from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from pathlib import Path

import pytest
from investment_studio_instrument_core.db_models import Instrument
from sqlalchemy import select

from portfolio_app.api.routes import transactions as transaction_routes
from portfolio_app.db.models import PortfolioCalculationStateModel, TransactionRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots


PORTFOLIO_ID = "investment-studio"
DELIVERED_INSTRUMENT_ID = "equity-delivery-only"
SOURCE_WATERMARK = "2099-01-01T00:00:00.000001Z"


@pytest.fixture
def physical_delivery(client, monkeypatch):
    account_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/accounts",
        json={
            "account_name": "Delivery dependency FCN",
            "account_category": "fcn",
            "currency": "USD",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "opened_at": "2026-01-01",
            "status": "active",
        },
    )
    assert account_response.status_code == 200, account_response.json()
    account_id = account_response.json()["account_id"]
    instrument_ref = {
        "instrument_id": DELIVERED_INSTRUMENT_ID,
        "instrument_name": "Delivered stock",
        "instrument_type": "equity",
        "currency": "USD",
        "exchange_code": "XNYS",
        "identifiers": [],
        "broker_identifiers": [],
    }
    original_loader = transaction_routes._load_instrument_ref
    monkeypatch.setattr(
        transaction_routes,
        "_load_instrument_ref",
        lambda instrument_id: instrument_ref
        if instrument_id == DELIVERED_INSTRUMENT_ID
        else original_loader(instrument_id),
    )
    with get_session_factory()() as session:
        session.add(Instrument(
            instrument_id=DELIVERED_INSTRUMENT_ID,
            instrument_name="Delivered stock",
            instrument_type="equity",
            currency="USD",
            exchange_code="XNYS",
            quote_selection_policy_json={},
            market_data_updated_at=SOURCE_WATERMARK,
            calculation_inputs_updated_at=SOURCE_WATERMARK,
        ))
        session.commit()

    # Reuse the documented, validated physical-settlement contract, without
    # importing any ordinary purchase of the delivered security.
    example = Path(__file__).resolve().parents[4] / "docs/examples/transaction_import_stock_fund_option_fcn.csv"
    reader = csv.DictReader(StringIO(example.read_text(encoding="utf-8")))
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(
        row for row in reader
        if row["external_reference"] in {"FCN-ENTRY-001", "FCN-CLOSE-001"}
    )
    csv_text = output.getvalue().replace("broker-us-fcn", account_id).replace(
        "equity-demo-001", DELIVERED_INSTRUMENT_ID
    )
    preview_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/transactions/csv/preview",
        json={"csv_text": csv_text},
    )
    assert preview_response.status_code == 200, preview_response.json()
    preview = preview_response.json()
    assert preview["valid_count"] == 2, preview
    response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/transactions/csv/import",
        headers={"Idempotency-Key": "physical-delivery-dependency"},
        json={"csv_text": csv_text, "preview_digest": preview["preview_digest"]},
    )
    assert response.status_code == 200, response.json()
    redemption = next(
        row for row in response.json()["transactions"]
        if row["external_reference"] == "FCN-CLOSE-001"
    )
    assert redemption["instrument_id"] is None
    assert redemption["asset_deliveries"][0]["instrument_id"] == DELIVERED_INSTRUMENT_ID
    return redemption


def test_unmaterialized_physical_delivery_is_a_source_dependency(physical_delivery):
    with get_session_factory()() as session:
        assert daily_snapshots._snapshot_count(session, PORTFOLIO_ID) == 0
        assert session.scalar(select(TransactionRecordModel.transaction_id).where(
            TransactionRecordModel.instrument_id == DELIVERED_INSTRUMENT_ID
        )) is None
        assert DELIVERED_INSTRUMENT_ID in daily_snapshots._portfolio_source_instrument_ids(
            session, PORTFOLIO_ID
        )
        assert daily_snapshots._source_market_data_watermark(session, PORTFOLIO_ID) == SOURCE_WATERMARK
        assert daily_snapshots._source_calculation_inputs_watermark(session, PORTFOLIO_ID) == SOURCE_WATERMARK


@pytest.mark.parametrize("changed_field", ["market_data_updated_at", "calculation_inputs_updated_at"])
def test_delivered_security_change_invalidates_source_generation(physical_delivery, changed_field):
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        assert state is not None
        state.source_market_data_updated_at = SOURCE_WATERMARK
        state.source_calculation_inputs_updated_at = SOURCE_WATERMARK
        assert daily_snapshots._source_inputs_are_current(session, PORTFOLIO_ID, state)
        instrument = session.get(Instrument, DELIVERED_INSTRUMENT_ID)
        setattr(instrument, changed_field, "2099-01-02T00:00:00.000001Z")
        session.commit()
        assert not daily_snapshots._source_inputs_are_current(session, PORTFOLIO_ID, state)


def test_delivered_security_change_enqueues_its_portfolio(physical_delivery):
    with get_session_factory()() as session:
        assert daily_snapshots._portfolio_ids_for_instrument_change(
            session, instrument_ids=["unrelated-security"], refresh_all=False,
        ) == []
        assert daily_snapshots._portfolio_ids_for_instrument_change(
            session, instrument_ids=["equity-us-abbv"], refresh_all=False,
        ) == [PORTFOLIO_ID]

    queued = daily_snapshots.enqueue_portfolio_daily_snapshot_recalculations_for_instrument_change(
        instrument_ids=[f" {DELIVERED_INSTRUMENT_ID} ", DELIVERED_INSTRUMENT_ID],
        dirty_from=date(2026, 9, 1),
    )
    assert [item["portfolio_id"] for item in queued] == [PORTFOLIO_ID]
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        assert state is not None
        assert state.daily_snapshot_status == "stale"
        assert state.refresh_request_id
