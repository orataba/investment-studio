from decimal import Decimal

import pytest

from portfolio_app.api.contracts import PhysicalOptionDelivery, TransactionCreateRequest
from portfolio_app.db.models import TransactionRecordModel
from portfolio_app.db.session import get_session_factory


def _buy(**changes):
    return {"transaction_type": "buy", "trade_date": "2026-04-16",
            "account_id": "broker-us-core", "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg", "quantity": "1", "price": "100",
            "gross_amount": "100", "currency": "USD", **changes}


COMPONENTS = [{"category": "performance_fee", "amount": "10.12345678"},
              {"category": "transaction_cost", "amount": "2.12000000"}]


def test_categorized_fee_components_sum_exactly_and_survive_edit_and_export(client):
    result = client.post("/api/portfolios/investment-studio/transactions", json=_buy(fee_components=COMPONENTS))
    assert result.status_code == 200, result.text
    created = result.json()
    assert created["source_fees"] == "12.24345678"
    assert created["fee_components"] == COMPONENTS
    assert created["fee_category"] == "unknown"
    with get_session_factory()() as session:
        record = session.get(TransactionRecordModel, created["transaction_id"])
        assert record.source_fees == Decimal("12.24345678")
        assert record.fee_components_json == COMPONENTS
    updated = client.put(f"/api/portfolios/investment-studio/transactions/{created['transaction_id']}", json=_buy(
        fee_components=[{"category": "performance_fee", "amount": "11"}, COMPONENTS[1]],
        expected_row_version=created["row_version"],
    ))
    assert updated.status_code == 200, updated.text
    assert Decimal(updated.json()["source_fees"]) == Decimal("13.12")
    exported = client.get("/api/portfolios/investment-studio/transactions.csv")
    assert exported.status_code == 200
    assert "fee_components_json" in exported.text
    assert "performance_fee" in exported.text and "transaction_cost" in exported.text
    from portfolio_app.services.transaction_csv import parse_transaction_csv
    _, rows = parse_transaction_csv(exported.text, default_source_system="test-roundtrip")
    matching = [row for row in rows if row.transaction and row.transaction.fee_components]
    assert matching
    imported = next(row.transaction for row in matching if row.transaction.fees == Decimal("13.12"))
    assert [item.category for item in imported.fee_components] == ["performance_fee", "transaction_cost"]
    assert [item.amount for item in imported.fee_components] == [Decimal("11"), Decimal("2.12")]



@pytest.mark.parametrize("changes,match", [
    ({"fees": "20"}, "sum of fee_components"),
    ({"fees": "0"}, "sum of fee_components"),
    ({"fee_category": "management_fee"}, "conflicts"),
    ({"transaction_type": "fee", "quantity": None, "price": None}, "nested fees"),
])
def test_fee_components_reject_conflicting_or_double_counted_totals(changes, match):
    with pytest.raises(ValueError, match=match):
        TransactionCreateRequest.model_validate(_buy(fee_components=COMPONENTS, **changes))


def test_reinvestment_still_requires_only_withheld_performance_fees():
    with pytest.raises(ValueError, match="withheld performance_fee"):
        TransactionCreateRequest.model_validate(_buy(transaction_type="dividend_reinvestment", settlement_cash_account_id=None, fee_components=COMPONENTS))


def test_physical_delivery_fee_total_uses_the_same_source_precision_as_its_components():
    delivery = PhysicalOptionDelivery.model_validate({
        "stock_account_id": "broker-us-core", "settlement_cash_account_id": "cash-usd-main",
        "fees": 0.1 + 0.2,
        "fee_components": [{"category": "performance_fee", "amount": "0.1"},
                           {"category": "transaction_cost", "amount": "0.2"}],
    })
    assert delivery.fees == Decimal("0.30000000")
    assert delivery.fee_category == "unknown"


@pytest.mark.migration_base_revision("20260924_0069")
def test_fee_component_migration_preserves_source_precision_and_original_facts():
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    import sqlalchemy as sa
    from portfolio_app.db.session import get_engine
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    with get_engine().begin() as connection:
        transaction_id = connection.scalar(sa.text("SELECT transaction_id FROM transaction_record LIMIT 1"))
        connection.execute(sa.text("UPDATE transaction_record SET fees=12.24345678, source_fees=12.24345678, fee_category='performance_fee' WHERE transaction_id=:id"), {"id": transaction_id})
        before = connection.execute(sa.text("SELECT transaction_id,fees,source_fees,fee_category,row_version FROM transaction_record WHERE transaction_id=:id"), {"id": transaction_id}).one()
    command.upgrade(config, "head")
    with get_session_factory()() as session:
        record = session.get(TransactionRecordModel, transaction_id)
        assert record.fee_components_json == [{"category": "performance_fee", "amount": "12.24345678"}]
    with get_engine().connect() as connection:
        after = connection.execute(sa.text("SELECT transaction_id,fees,source_fees,fee_category,row_version FROM transaction_record WHERE transaction_id=:id"), {"id": transaction_id}).one()
    assert after == before
    with pytest.raises(RuntimeError, match="pre-migration database backup"):
        command.downgrade(config, "20260924_0069")
