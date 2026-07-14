from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from portfolio_app.db.models import (
    TransactionCurrentModel,
    TransactionRevisionGroupRecordModel,
    TransactionRevisionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import portfolio_store
from portfolio_app.services.transaction_revisions import (
    TransactionFactPayload,
    TransactionRevisionContext,
    TransactionRevisionPayloadError,
)


TEST_ACTOR = {
    "actor_type": "user",
    "actor_id": "pm:test-manager",
    "display_name": "Test Portfolio Manager",
    "actor_source": "client_asserted",
}


@pytest.mark.parametrize(
    ("actor_type", "actor_source"),
    [
        ("user", "client_asserted"),
        ("user", "authenticated_principal"),
        ("service", "trusted_service"),
        ("migration", "migration"),
    ],
)
def test_transaction_revision_context_accepts_only_canonical_actor_pairings(
    actor_type: str,
    actor_source: str,
) -> None:
    context = TransactionRevisionContext(
        source_kind="manual",
        change_reason="Exercise the canonical audit contract",
        actor_type=actor_type,  # type: ignore[arg-type]
        actor_id="actor:test",
        actor_display_name="Test Actor",
        actor_source=actor_source,  # type: ignore[arg-type]
    )

    assert context.actor_type == actor_type
    assert context.actor_source == actor_source


def test_transaction_revision_context_rejects_unknown_actor_source() -> None:
    with pytest.raises(
        TransactionRevisionPayloadError,
        match="unsupported actor_source: alembic",
    ):
        TransactionRevisionContext(
            source_kind="migration",
            change_reason="Reject implementation-specific provenance",
            actor_type="migration",
            actor_id="system:migration:test",
            actor_display_name="Test Migration",
            actor_source="alembic",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("actor_type", "actor_source", "expected_actor_type"),
    [
        ("service", "client_asserted", "user"),
        ("migration", "authenticated_principal", "user"),
        ("user", "trusted_service", "service"),
        ("service", "migration", "migration"),
    ],
)
def test_transaction_revision_context_rejects_actor_source_type_mismatch(
    actor_type: str,
    actor_source: str,
    expected_actor_type: str,
) -> None:
    with pytest.raises(
        TransactionRevisionPayloadError,
        match=(
            f"actor_source {actor_source} requires actor_type "
            f"{expected_actor_type}, not {actor_type}"
        ),
    ):
        TransactionRevisionContext(
            source_kind="manual",
            change_reason="Reject ambiguous audit provenance",
            actor_type=actor_type,  # type: ignore[arg-type]
            actor_id="actor:test",
            actor_display_name="Test Actor",
            actor_source=actor_source,  # type: ignore[arg-type]
        )


def _deposit_request(
    *,
    gross_amount: str = "100.00000000",
    note: str = "Initial cash fact",
) -> dict[str, object]:
    return {
        "transaction_type": "deposit",
        "trade_date": "2026-04-16",
        "trade_time": "09:30",
        "settlement_date": "2026-04-16",
        "account_id": "cash-usd-main",
        "gross_amount": gross_amount,
        "currency": "USD",
        "note": note,
        "actor": TEST_ACTOR,
    }


def _revision_metadata(record: dict[str, object], *, reason: str) -> dict[str, object]:
    return {
        "expected_revision_id": record["revision_id"],
        "expected_revision_number": record["revision_number"],
        "actor": TEST_ACTOR,
        "change_reason": reason,
    }


def test_transaction_revision_lifecycle_exposes_no_op_conflict_delete_and_history(client) -> None:
    create_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_deposit_request(),
    )
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()
    assert created["revision_number"] == 1
    assert created["lifecycle_status"] == "active"
    assert created["gross_amount"] == "100"
    assert created["gross_amount_input_scale"] == 8
    assert created["numeric_scale_state"] == "declared"
    assert created["last_actor"] == TEST_ACTOR

    transaction_id = created["transaction_id"]
    amended_request = {
        **_deposit_request(gross_amount="125.25000000", note="Corrected cash fact"),
        **_revision_metadata(created, reason="Correct the entered cash amount"),
    }
    amend_response = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json=amended_request,
    )
    assert amend_response.status_code == 200, amend_response.text
    amended = amend_response.json()
    assert amended["revision_number"] == 2
    assert amended["revision_id"] != created["revision_id"]
    assert amended["gross_amount"] == "125.25"
    assert amended["gross_amount_input_scale"] == 8
    assert amended["last_change_reason"] == "Correct the entered cash amount"

    no_op_response = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={
            **amended_request,
            **_revision_metadata(amended, reason="Retry the same correction"),
        },
    )
    assert no_op_response.status_code == 422
    no_op_detail = no_op_response.json()["detail"]
    assert no_op_detail["code"] == "transaction_revision_no_op"
    assert no_op_detail["revision_id"] == amended["revision_id"]
    assert no_op_detail["revision_number"] == 2

    stale_response = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={
            **_deposit_request(gross_amount="130.00000000", note="Stale overwrite"),
            **_revision_metadata(created, reason="Attempt a stale overwrite"),
        },
    )
    assert stale_response.status_code == 409
    stale_detail = stale_response.json()["detail"]
    assert stale_detail["code"] == "transaction_revision_conflict"
    assert stale_detail["reason_code"] == "stale_revision"
    assert stale_detail["expected_revision_id"] == created["revision_id"]
    assert stale_detail["actual_revision_id"] == amended["revision_id"]
    assert stale_detail["actual_revision_number"] == 2

    delete_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json=_revision_metadata(amended, reason="Remove the erroneous cash fact"),
    )
    assert delete_response.status_code == 200, delete_response.text
    deleted = delete_response.json()
    assert deleted["deleted_count"] == 1
    assert deleted["deleted_transaction_ids"] == [transaction_id]
    assert deleted["revisions"][0]["operation"] == "delete"
    assert deleted["revisions"][0]["revision_number"] == 3
    assert deleted["revisions"][0]["snapshot"] is None
    assert portfolio_store.get_transaction("portfolio-ops", transaction_id) is None

    history_response = client.get(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}/revisions"
    )
    assert history_response.status_code == 200, history_response.text
    history = history_response.json()
    assert history["lifecycle_status"] == "deleted"
    assert history["current_revision_number"] == 3
    assert [item["operation"] for item in history["revisions"]] == [
        "create",
        "amend",
        "delete",
    ]
    assert [item["revision_number"] for item in history["revisions"]] == [1, 2, 3]
    assert history["revisions"][1]["previous_revision_id"] == created["revision_id"]
    assert history["revisions"][1]["changed_fields"] == ["gross_amount", "note"]
    assert history["revisions"][2]["previous_revision_id"] == amended["revision_id"]
    assert history["revisions"][2]["snapshot"] is None

    session_factory = get_session_factory()
    with session_factory() as session:
        persisted = session.scalars(
            select(TransactionRevisionRecordModel)
            .where(TransactionRevisionRecordModel.transaction_id == transaction_id)
            .order_by(TransactionRevisionRecordModel.revision_number)
        ).all()
        assert [revision.revision_number for revision in persisted] == [1, 2, 3]
        assert persisted[1].gross_amount == Decimal("125.25000000")
        assert persisted[2].is_tombstone is True
        assert persisted[2].gross_amount is None
        assert session.scalar(
            select(TransactionCurrentModel).where(
                TransactionCurrentModel.transaction_id == transaction_id
            )
        ) is None


def test_internal_transfer_legs_share_groups_and_delete_atomically(client) -> None:
    create_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-16",
            "trade_time": "10:15",
            "settlement_date": "2026-04-16",
            "from_account_id": "cash-usd-main",
            "to_account_id": "cash-usd-reserve",
            "transfer_object_type": "cash",
            "gross_amount": "250.00000000",
            "note": "Reserve sweep",
            "actor": TEST_ACTOR,
            "change_reason": "Sweep cash to the reserve account",
        },
    )
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()
    assert created["created_count"] == 2
    assert len(created["transactions"]) == 2
    assert {item["transaction_type"] for item in created["transactions"]} == {
        "transfer_in",
        "transfer_out",
    }
    assert {item["last_mutation_id"] for item in created["transactions"]} == {
        created["mutation_id"]
    }
    assert {item["transfer_group_id"] for item in created["transactions"]} == {
        created["transfer_group_id"]
    }

    target = created["transactions"][0]
    stale_delete_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{target['transaction_id']}",
        json={
            "expected_revision_id": "stale-revision-id",
            "expected_revision_number": 1,
            "actor": TEST_ACTOR,
            "change_reason": "Attempt deletion from stale state",
        },
    )
    assert stale_delete_response.status_code == 409
    assert stale_delete_response.json()["detail"]["reason_code"] == "stale_revision"
    for transaction in created["transactions"]:
        assert (
            portfolio_store.get_transaction("portfolio-ops", transaction["transaction_id"])
            is not None
        )

    delete_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{target['transaction_id']}",
        json=_revision_metadata(target, reason="Reverse the complete reserve sweep"),
    )
    assert delete_response.status_code == 200, delete_response.text
    deleted = delete_response.json()
    assert deleted["deleted_count"] == 2
    assert deleted["transfer_group_id"] == created["transfer_group_id"]
    assert len({item["mutation_id"] for item in deleted["revisions"]}) == 1
    assert {item["operation"] for item in deleted["revisions"]} == {"delete"}

    transaction_ids = [item["transaction_id"] for item in created["transactions"]]
    session_factory = get_session_factory()
    with session_factory() as session:
        revisions = session.scalars(
            select(TransactionRevisionRecordModel)
            .where(TransactionRevisionRecordModel.transaction_id.in_(transaction_ids))
            .order_by(
                TransactionRevisionRecordModel.revision_number,
                TransactionRevisionRecordModel.transaction_id,
            )
        ).all()
        create_revisions = [revision for revision in revisions if revision.revision_number == 1]
        delete_revisions = [revision for revision in revisions if revision.revision_number == 2]
        assert len({revision.revision_group_id for revision in create_revisions}) == 1
        assert len({revision.revision_group_id for revision in delete_revisions}) == 1
        assert create_revisions[0].revision_group_id != delete_revisions[0].revision_group_id
        assert all(revision.is_tombstone for revision in delete_revisions)
        assert session.scalar(
            select(func.count())
            .select_from(TransactionCurrentModel)
            .where(TransactionCurrentModel.transaction_id.in_(transaction_ids))
        ) == 0


def test_equal_decimal_values_preserve_distinct_declared_input_scale(client) -> None:
    created_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_deposit_request(gross_amount="12.3400", note="Four-place source fact"),
    )
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    assert created["gross_amount"] == "12.34"
    assert created["gross_amount_input_scale"] == 4

    amended_response = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{created['transaction_id']}",
        json={
            **_deposit_request(gross_amount="12.34", note="Four-place source fact"),
            **_revision_metadata(
                created,
                reason="Correct the declared source precision",
            ),
        },
    )
    assert amended_response.status_code == 200, amended_response.text
    amended = amended_response.json()
    assert amended["gross_amount"] == "12.34"
    assert amended["gross_amount_input_scale"] == 2
    assert amended["revision_id"] != created["revision_id"]

    history_response = client.get(
        f"/api/portfolios/portfolio-ops/transactions/{created['transaction_id']}/revisions"
    )
    assert history_response.status_code == 200, history_response.text
    history = history_response.json()["revisions"]
    assert history[1]["changed_fields"] == ["gross_amount_input_scale"]
    with get_session_factory()() as session:
        payload_hashes = session.scalars(
            select(TransactionRevisionRecordModel.payload_hash)
            .where(
                TransactionRevisionRecordModel.transaction_id
                == created["transaction_id"]
            )
            .order_by(TransactionRevisionRecordModel.revision_number)
        ).all()
    assert len(payload_hashes) == 2
    assert payload_hashes[0] != payload_hashes[1]


def test_transaction_decimal_contract_requires_strings_and_rejects_precision_loss(client) -> None:
    numeric_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={**_deposit_request(), "gross_amount": 1.25},
    )
    assert numeric_response.status_code == 422
    assert "must be JSON strings" in numeric_response.text

    excessive_precision_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_deposit_request(gross_amount="1.000000001"),
    )
    assert excessive_precision_response.status_code == 422
    assert "declares more than 8 decimal places" in excessive_precision_response.text

    accepted_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_deposit_request(gross_amount="1.12345678"),
    )
    assert accepted_response.status_code == 200, accepted_response.text
    accepted = accepted_response.json()
    assert accepted["gross_amount"] == "1.12345678"
    assert accepted["gross_amount_input_scale"] == 8
    assert accepted["numeric_scale_state"] == "declared"

    session_factory = get_session_factory()
    with session_factory() as session:
        revision = session.scalar(
            select(TransactionRevisionRecordModel).where(
                TransactionRevisionRecordModel.transaction_id == accepted["transaction_id"]
            )
        )
        assert revision is not None
        assert revision.gross_amount == Decimal("1.12345678")
        assert isinstance(revision.gross_amount, Decimal)
        assert len(revision.payload_hash) == 71


def test_transaction_fact_boundary_rejects_binary_float_financial_values() -> None:
    common_facts = {
        "transaction_type": "buy",
        "trade_date": date(2026, 7, 14),
        "trade_time": time(9, 30),
        "trade_at": datetime(2026, 7, 14, 9, 30, tzinfo=UTC),
        "trade_timezone": "UTC",
        "trade_time_is_estimated": False,
        "settlement_date": date(2026, 7, 16),
        "account_id": "brokerage-main",
        "instrument_id": "instrument-exact",
        "instrument_snapshot_json": {
            "instrument_id": "instrument-exact",
            "instrument_type": "equity",
        },
        "quantity": "1.250000000000",
        "price": "80.000000000000",
        "gross_amount": "100.00000000",
        "consideration_basis": "exact_quantity_price",
        "fees": "0.00000000",
        "taxes": "0.00000000",
        "currency": "USD",
    }

    with pytest.raises(
        TransactionRevisionPayloadError,
        match="gross_amount must not cross the fact boundary as a binary float",
    ):
        TransactionFactPayload(**{**common_facts, "gross_amount": 100.0})

    with pytest.raises(
        TransactionRevisionPayloadError,
        match=r"instrument_snapshot_json\.valuation_contract\.contract_multiplier contains a binary float",
    ):
        TransactionFactPayload(
            **{
                **common_facts,
                "instrument_snapshot_json": {
                    "instrument_id": "instrument-exact",
                    "instrument_type": "equity",
                    "valuation_contract": {
                        "contract_multiplier": 1.0,
                        "price_factor": "1",
                    },
                },
            }
        )

    accepted = TransactionFactPayload(
        **{
            **common_facts,
            "gross_amount": Decimal("100.00000000"),
            "fees": 0,
        }
    )
    assert accepted.gross_amount.as_tuple().exponent == 0
    assert accepted.gross_amount_input_scale == 8
    assert accepted.fees.as_tuple().exponent == 0
    assert accepted.fees_input_scale == 0

    with pytest.raises(
        TransactionRevisionPayloadError,
        match="gross_amount must exactly equal the amount implied",
    ):
        TransactionFactPayload(**{**common_facts, "gross_amount": "99.99999999"})

    bond_without_a_valuation_contract = TransactionFactPayload(
        **{
            **common_facts,
            "instrument_id": "bond-without-factor",
            "instrument_snapshot_json": {
                "instrument_id": "bond-without-factor",
                "instrument_type": "bond",
            },
            "quantity": "1000.000000000000",
            "price": "98.500000000000",
            "gross_amount": "985.00000000",
            "consideration_basis": "source_reported",
        }
    )
    assert bond_without_a_valuation_contract.gross_amount == Decimal("985")

    source_reported_down = TransactionFactPayload(
        **{
            **common_facts,
            "quantity": "1.000000000000",
            "price": "1.000000005000",
            "gross_amount": "1.00000000",
            "consideration_basis": "source_reported",
        }
    )
    source_reported_up = TransactionFactPayload(
        **{
            **common_facts,
            "quantity": "1.000000000000",
            "price": "1.000000015000",
            "gross_amount": "1.00000002",
            "consideration_basis": "source_reported",
        }
    )
    assert source_reported_down.gross_amount == Decimal("1")
    assert source_reported_up.gross_amount == Decimal("1.00000002")


@pytest.mark.no_database
def test_fx_conversion_preserves_actual_counter_cash_and_optional_quote() -> None:
    common_facts = {
        "transaction_type": "fx_conversion",
        "trade_date": date(2026, 7, 14),
        "trade_time": time(9, 30),
        "trade_at": datetime(2026, 7, 14, 9, 30, tzinfo=UTC),
        "trade_timezone": "UTC",
        "trade_time_is_estimated": False,
        "settlement_date": date(2026, 7, 14),
        "account_id": "cash-usd",
        "counterparty_account_id": "cash-cny",
        "gross_amount": "3.00000000",
        "counter_amount": "21.11111111",
        "quoted_fx_rate": "7.037037036666666667",
        "fees": "0.00000000",
        "taxes": "0.00000000",
        "currency": "USD",
    }

    accepted = TransactionFactPayload(**common_facts)
    assert accepted.counter_amount == Decimal("21.11111111")
    assert accepted.quoted_fx_rate == Decimal("7.037037036666666667")
    assert accepted.quoted_fx_rate_input_scale == 18

    different_actual = TransactionFactPayload(
        **{**common_facts, "counter_amount": "21.11111110"}
    )
    assert different_actual.counter_amount == Decimal("21.1111111")

    without_quote = TransactionFactPayload(
        **{**common_facts, "quoted_fx_rate": None}
    )
    assert without_quote.quoted_fx_rate is None


def test_conflicting_batch_preflight_does_not_persist_partial_group() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        before_groups = session.scalar(
            select(func.count()).select_from(TransactionRevisionGroupRecordModel)
        )

    try:
        portfolio_store.create_transactions(
            portfolio_id="portfolio-ops",
            records=[
                {
                    "transaction_type": "deposit",
                    "trade_date": date(2026, 4, 16),
                    "trade_time": "09:30",
                    "settlement_date": date(2026, 4, 16),
                    "entitlement_date": None,
                    "acquisition_date": None,
                    "account_id": "cash-usd-main",
                    "settlement_cash_account_id": None,
                    "instrument_id": None,
                    "instrument_ref": None,
                    "quantity": None,
                    "price": None,
                    "gross_amount": "10.00000000",
                    "counter_amount": None,
                    "quoted_fx_rate": None,
                    "consideration_basis": None,
                    "fees": "0.00000000",
                    "taxes": "0.00000000",
                    "currency": "USD",
                    "transfer_scope": None,
                    "transfer_object_type": None,
                    "transfer_group_id": None,
                    "counterparty_account_id": None,
                    "note": "valid first row",
                },
                {
                    "transaction_type": "deposit",
                    "trade_date": date(2026, 4, 16),
                    "trade_time": "09:30",
                    "settlement_date": date(2026, 4, 16),
                    "entitlement_date": None,
                    "acquisition_date": None,
                    "account_id": "cash-usd-main",
                    "settlement_cash_account_id": None,
                    "instrument_id": None,
                    "instrument_ref": None,
                    "quantity": None,
                    "price": None,
                    "counter_amount": None,
                    "quoted_fx_rate": None,
                    "consideration_basis": None,
                    "fees": "0.00000000",
                    "taxes": "0.00000000",
                    "currency": "USD",
                    "transfer_scope": None,
                    "transfer_object_type": None,
                    "transfer_group_id": None,
                    "counterparty_account_id": None,
                    "note": "missing gross amount",
                },
            ],
            actor=TEST_ACTOR,
            change_reason="Test all-or-nothing batch preflight",
        )
    except KeyError as error:
        assert error.args == ("gross_amount",)
    else:
        raise AssertionError("invalid transaction batch was accepted")

    with session_factory() as session:
        after_groups = session.scalar(
            select(func.count()).select_from(TransactionRevisionGroupRecordModel)
        )
        assert after_groups == before_groups
