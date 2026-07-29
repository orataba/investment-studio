from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import portfolio_store


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _deposit_payload(*, amount: float = 1000.0, note: str = "Audited deposit") -> dict[str, object]:
    return {
        "transaction_type": "deposit",
        "trade_date": "2026-04-16",
        "account_id": "cash-usd-main",
        "gross_amount": amount,
        "currency": "USD",
        "note": note,
    }


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_transaction_post_idempotency_replays_same_result_and_rejects_payload_reuse(client) -> None:
    before_ids = {
        item["transaction_id"]
        for item in portfolio_store.list_transactions("portfolio-ops")
    }
    headers = {"Idempotency-Key": "deposit-request-001"}

    first = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        headers=headers,
        json=_deposit_payload(),
    )
    replay = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        headers=headers,
        json=_deposit_payload(),
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    transaction_id = first.json()["transaction_id"]
    assert first.json()["row_version"] == 1
    after_ids = {
        item["transaction_id"]
        for item in portfolio_store.list_transactions("portfolio-ops")
    }
    assert after_ids - before_ids == {transaction_id}

    conflict = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        headers=headers,
        json=_deposit_payload(amount=1001.0),
    )
    assert conflict.status_code == 409
    assert "different payload" in conflict.json()["detail"].lower()

    change_log = client.get(
        "/api/portfolios/portfolio-ops/transactions/change-log",
        params={"transaction_id": transaction_id},
    )
    assert change_log.status_code == 200
    assert change_log.json()["summary"]["change_count"] == 1
    [created] = change_log.json()["changes"]
    assert created["change_type"] == "create"
    assert created["row_version"] == 1
    assert created["before"] is None
    assert created["after"]["transaction_id"] == transaction_id
    assert created["after"]["row_version"] == 1
    assert created["request_idempotency_key"] == "deposit-request-001"

    workspace = client.get(
        "/api/portfolios/portfolio-ops/transactions/workspace",
        params={"transaction_id": transaction_id},
    )
    assert workspace.status_code == 200
    assert workspace.json()["change_log_summary"] == {"change_count": 1}
    assert workspace.json()["change_log"] == [created]


def test_transaction_update_uses_optimistic_version_and_audits_delete_tombstone(client) -> None:
    created = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_deposit_payload(),
    )
    assert created.status_code == 200
    transaction_id = created.json()["transaction_id"]
    assert created.json()["row_version"] == 1

    missing_version = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json=_deposit_payload(amount=1250.0, note="Unversioned correction"),
    )
    assert missing_version.status_code == 422
    assert "expected_row_version" in missing_version.text

    update_payload = {
        **_deposit_payload(amount=1250.0, note="Corrected deposit"),
        "trade_date": "2026-04-17",
        "expected_row_version": 1,
    }
    updated = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json=update_payload,
    )
    assert updated.status_code == 200
    assert updated.json()["row_version"] == 2
    assert updated.json()["gross_amount"] == 1250.0

    stale = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={**update_payload, "gross_amount": 1300.0, "expected_row_version": 1},
    )
    assert stale.status_code == 409
    assert "row version" in stale.json()["detail"].lower()
    assert portfolio_store.get_transaction("portfolio-ops", transaction_id)["row_version"] == 2

    stale_delete = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={"expected_row_versions": {transaction_id: 1}},
    )
    assert stale_delete.status_code == 409
    assert "row version" in stale_delete.json()["detail"].lower()
    assert portfolio_store.get_transaction("portfolio-ops", transaction_id)["row_version"] == 2

    deleted = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={"expected_row_versions": {transaction_id: 2}},
    )
    assert deleted.status_code == 200

    change_log = client.get(
        "/api/portfolios/portfolio-ops/transactions/change-log",
        params={"transaction_id": transaction_id},
    )
    assert change_log.status_code == 200
    changes = change_log.json()["changes"]
    assert [change["change_type"] for change in changes] == ["create", "update", "delete"]
    assert [change["row_version"] for change in changes] == [1, 2, 3]
    assert changes[1]["before"]["row_version"] == 1
    assert changes[1]["after"]["row_version"] == 2
    assert changes[2]["before"]["row_version"] == 2
    assert changes[2]["after"] is None


def test_internal_transfer_post_idempotency_replays_the_original_pair(client) -> None:
    payload = {
        "trade_date": "2026-04-16",
        "from_account_id": "cash-usd-main",
        "to_account_id": "cash-usd-reserve",
        "transfer_object_type": "cash",
        "gross_amount": 250.0,
        "note": "Idempotent sweep",
    }
    headers = {"Idempotency-Key": "cash-sweep-request-001"}

    first = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["created_count"] == 2
    assert {item["row_version"] for item in first.json()["transactions"]} == {1}

    conflict = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        headers=headers,
        json={**payload, "gross_amount": 251.0},
    )
    assert conflict.status_code == 409


def test_reset_round_trip_preserves_versions_and_copy_starts_new_facts_at_version_one(client) -> None:
    created = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_deposit_payload(),
    )
    transaction_id = created.json()["transaction_id"]
    updated = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={**_deposit_payload(amount=1100.0), "expected_row_version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["row_version"] == 2

    session_factory = get_session_factory()
    with session_factory() as session:
        loaded = portfolio_store._load_store_from_db(session)
    portfolio_store.reset_store(deepcopy(loaded))
    assert portfolio_store.get_transaction("portfolio-ops", transaction_id)["row_version"] == 2

    copied = portfolio_store.copy_portfolio("portfolio-ops")
    assert copied is not None
    copied_transactions = portfolio_store.list_transactions(str(copied["portfolio_id"]))
    assert copied_transactions
    assert {item["row_version"] for item in copied_transactions} == {1}


@pytest.mark.migration_base_revision("20260715_0038")
def test_transaction_audit_migration_is_additive_backfills_versions_and_is_reversible() -> None:
    engine = get_engine()
    config = _alembic_config()
    with engine.connect() as connection:
        transaction_count = connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record"))

    command.downgrade(config, "20260715_0035")
    inspector = sa.inspect(engine)
    assert "row_version" not in {
        column["name"] for column in inspector.get_columns("transaction_record")
    }
    assert "transaction_change_log" not in inspector.get_table_names()
    assert "transaction_idempotency_record" not in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record")) == transaction_count

    command.upgrade(config, "20260715_0036")
    inspector = sa.inspect(engine)
    assert "row_version" in {
        column["name"] for column in inspector.get_columns("transaction_record")
    }
    assert "transaction_change_log" in inspector.get_table_names()
    assert "transaction_idempotency_record" in inspector.get_table_names()
    with engine.connect() as connection:
        versions = connection.scalars(sa.text("SELECT row_version FROM transaction_record")).all()
    assert versions
    assert set(versions) == {1}
