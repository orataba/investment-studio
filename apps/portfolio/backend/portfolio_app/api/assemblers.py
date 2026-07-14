from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from portfolio_app.api.contracts import (
    AccountRecord,
    InstrumentCoreContract,
    TransactionListSummary,
    TransactionRecord,
)


def resolve_transaction_flow_scope(transaction_type: str) -> str:
    if transaction_type in {"deposit", "withdrawal"}:
        return "external_cash_flow"
    if transaction_type == "opening_balance":
        return "bootstrap"
    return "internal_portfolio"


def _decimal_fact(value: object, *, field_name: str) -> Decimal:
    try:
        resolved = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"Persisted transaction {field_name} is not a valid decimal.",
        ) from error
    if not resolved.is_finite():
        raise HTTPException(
            status_code=500,
            detail=f"Persisted transaction {field_name} must be finite.",
        )
    return resolved


def _optional_decimal_fact(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal_fact(value, field_name=field_name)


def resolve_transaction_net_cash_effect(record: dict[str, object]) -> Decimal | None:
    transaction_type = str(record.get("transaction_type") or "")
    gross_amount = _decimal_fact(
        record.get("gross_amount", Decimal("0")),
        field_name="gross_amount",
    )
    fees = _decimal_fact(record.get("fees", Decimal("0")), field_name="fees")
    taxes = _decimal_fact(record.get("taxes", Decimal("0")), field_name="taxes")
    if transaction_type == "buy":
        return -(gross_amount + fees + taxes)
    if transaction_type == "sell":
        return gross_amount - fees - taxes
    if transaction_type in {"dividend", "coupon", "return_of_capital", "maturity_redemption"}:
        return gross_amount - fees - taxes
    if transaction_type == "interest":
        return gross_amount
    if transaction_type == "dividend_reinvestment":
        return Decimal("0")
    if transaction_type in {"fee", "tax"}:
        return -gross_amount
    if transaction_type == "deposit":
        return gross_amount
    if transaction_type == "withdrawal":
        return -gross_amount
    if transaction_type == "fx_conversion":
        return None
    if transaction_type == "transfer_out" and record.get("transfer_object_type") == "cash":
        return -gross_amount
    if transaction_type == "transfer_in" and record.get("transfer_object_type") == "cash":
        return gross_amount
    return None


def serialize_transaction(
    portfolio_id: str,
    record: dict[str, object],
    account_lookup: dict[str, dict[str, object]],
) -> TransactionRecord:
    account_id = str(record.get("account_id") or "")
    account = account_lookup.get(account_id)
    if account is None:
        raise HTTPException(status_code=400, detail=f"Account '{account_id}' not found")

    settlement_id = record.get("settlement_cash_account_id")
    settlement_account = None
    if isinstance(settlement_id, str):
        settlement_account = account_lookup.get(settlement_id)

    instrument_ref = record.get("instrument_ref")
    return TransactionRecord(
        transaction_id=str(record.get("transaction_id") or ""),
        portfolio_id=portfolio_id,
        transaction_type=str(record.get("transaction_type") or ""),
        flow_scope=resolve_transaction_flow_scope(str(record.get("transaction_type") or "")),
        trade_date=date.fromisoformat(str(record.get("trade_date") or date.today().isoformat())),
        trade_time=str(record.get("trade_time") or "12:00"),
        trade_at=str(record.get("trade_at") or ""),
        trade_timezone=str(record.get("trade_timezone") or ""),
        trade_time_is_estimated=bool(record.get("trade_time_is_estimated")),
        settlement_date=date.fromisoformat(str(record.get("settlement_date") or date.today().isoformat())),
        entitlement_date=(
            date.fromisoformat(str(record.get("entitlement_date")))
            if record.get("entitlement_date")
            else None
        ),
        acquisition_date=(
            date.fromisoformat(str(record.get("acquisition_date")))
            if record.get("acquisition_date")
            else None
        ),
        account=AccountRecord.model_validate(account),
        settlement_cash_account=(
            AccountRecord.model_validate(settlement_account) if settlement_account is not None else None
        ),
        instrument_id=str(record.get("instrument_id")) if record.get("instrument_id") else None,
        instrument_ref=InstrumentCoreContract.model_validate(instrument_ref) if instrument_ref else None,
        quantity=_optional_decimal_fact(record.get("quantity"), field_name="quantity"),
        price=_optional_decimal_fact(record.get("price"), field_name="price"),
        gross_amount=_decimal_fact(record.get("gross_amount"), field_name="gross_amount"),
        counter_amount=_optional_decimal_fact(
            record.get("counter_amount"),
            field_name="counter_amount",
        ),
        quoted_fx_rate=_optional_decimal_fact(
            record.get("quoted_fx_rate"),
            field_name="quoted_fx_rate",
        ),
        fees=_decimal_fact(record.get("fees", Decimal("0")), field_name="fees"),
        taxes=_decimal_fact(record.get("taxes", Decimal("0")), field_name="taxes"),
        consideration_basis=(
            str(record.get("consideration_basis"))
            if record.get("consideration_basis") is not None
            else None
        ),
        numeric_scale_state=str(record.get("numeric_scale_state") or ""),
        quantity_input_scale=record.get("quantity_input_scale"),
        price_input_scale=record.get("price_input_scale"),
        gross_amount_input_scale=record.get("gross_amount_input_scale"),
        counter_amount_input_scale=record.get("counter_amount_input_scale"),
        quoted_fx_rate_input_scale=record.get("quoted_fx_rate_input_scale"),
        fees_input_scale=record.get("fees_input_scale"),
        taxes_input_scale=record.get("taxes_input_scale"),
        currency=str(record.get("currency") or ""),
        transfer_scope=str(record.get("transfer_scope")) if record.get("transfer_scope") else None,
        transfer_object_type=(
            str(record.get("transfer_object_type")) if record.get("transfer_object_type") else None
        ),
        transfer_group_id=str(record.get("transfer_group_id")) if record.get("transfer_group_id") else None,
        counterparty_account_id=(
            str(record.get("counterparty_account_id")) if record.get("counterparty_account_id") else None
        ),
        net_cash_effect=resolve_transaction_net_cash_effect(record),
        note=str(record.get("note")) if record.get("note") else None,
        created_at=str(record.get("created_at")) if record.get("created_at") else None,
        revision_id=str(record.get("revision_id") or ""),
        revision_number=int(record.get("revision_number") or 0),
        lifecycle_status=str(record.get("lifecycle_status") or "active"),
        last_mutation_id=str(record.get("last_mutation_id") or ""),
        last_changed_at=record.get("last_changed_at"),
        last_actor=record.get("last_actor"),
        last_change_reason=(
            str(record.get("last_change_reason"))
            if record.get("last_change_reason")
            else None
        ),
    )


def summarize_transactions(records: list[dict[str, object]]) -> TransactionListSummary:
    return TransactionListSummary(
        total_transactions=len(records),
        instrument_transactions=sum(1 for item in records if item.get("instrument_id")),
        external_cash_flows=sum(1 for item in records if item.get("transaction_type") in {"deposit", "withdrawal"}),
        opening_balance_records=sum(1 for item in records if item.get("transaction_type") == "opening_balance"),
    )
