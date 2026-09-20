from __future__ import annotations

from datetime import date

from fastapi import HTTPException

from portfolio_app.api.contracts import (
    AccountRecord,
    DerivativeContractRecord,
    InstrumentCoreContract,
    TransactionListSummary,
    TransactionRecord,
)
from portfolio_app.services.transaction_dates import (
    transaction_economic_date,
    transaction_external_flow_date,
    transaction_position_effective_date,
)
from portfolio_app.services.option_actions import resolve_option_action
from portfolio_app.services.transaction_cash import resolve_transaction_net_cash_effect


def resolve_transaction_flow_scope(transaction_type: str) -> str:
    if transaction_type in {"deposit", "withdrawal"}:
        return "external_cash_flow"
    if transaction_type in {"opening_balance", "option_opening_balance", "short_opening_balance"}:
        return "bootstrap"
    return "internal_portfolio"


def resolve_transaction_asset_domain(record: dict[str, object]) -> str:
    if record.get("derivative_contract_id"):
        return "derivative"
    if record.get("instrument_id"):
        return "security"
    return "cash"


def resolve_transaction_asset_subtype(record: dict[str, object]) -> str | None:
    derivative_contract = record.get("derivative_contract")
    if isinstance(derivative_contract, dict) and derivative_contract.get("contract_type"):
        return str(derivative_contract["contract_type"])
    instrument_ref = record.get("instrument_ref")
    if isinstance(instrument_ref, dict) and instrument_ref.get("instrument_type"):
        return str(instrument_ref["instrument_type"])
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
    derivative_contract = record.get("derivative_contract")
    economic_date = transaction_economic_date(record)
    if economic_date is None:
        raise HTTPException(status_code=400, detail="Transaction economic date is invalid")
    external_flow_date = transaction_external_flow_date(record)
    position_effective_date = transaction_position_effective_date(record)
    return TransactionRecord(
        transaction_id=str(record.get("transaction_id") or ""),
        transaction_sequence=int(record.get("transaction_sequence") or 0),
        portfolio_id=portfolio_id,
        transaction_type=str(record.get("transaction_type") or ""),
        asset_domain=resolve_transaction_asset_domain(record),
        asset_subtype=resolve_transaction_asset_subtype(record),
        option_action=resolve_option_action(record),
        lifecycle_event_type=(
            str(record.get("lifecycle_event_type"))
            if record.get("lifecycle_event_type")
            else None
        ),
        flow_scope=resolve_transaction_flow_scope(str(record.get("transaction_type") or "")),
        trade_date=date.fromisoformat(str(record.get("trade_date") or date.today().isoformat())),
        trade_time=str(record.get("trade_time") or "12:00"),
        trade_at=str(record.get("trade_at") or ""),
        trade_timezone=str(record.get("trade_timezone") or ""),
        trade_time_is_estimated=bool(record.get("trade_time_is_estimated")),
        settlement_date=date.fromisoformat(str(record.get("settlement_date") or date.today().isoformat())),
        position_effective_date=position_effective_date,
        economic_date=economic_date,
        external_flow_date=external_flow_date,
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
        derivative_contract_id=(
            str(record.get("derivative_contract_id"))
            if record.get("derivative_contract_id")
            else None
        ),
        derivative_contract=(
            DerivativeContractRecord.model_validate(derivative_contract)
            if derivative_contract
            else None
        ),
        quantity=float(record["quantity"]) if record.get("quantity") is not None else None,
        source_quantity=str(record["source_quantity"]) if record.get("source_quantity") is not None else None,
        price=float(record["price"]) if record.get("price") is not None else None,
        source_price=str(record["source_price"]) if record.get("source_price") is not None else None,
        gross_amount=float(record.get("gross_amount") or 0.0),
        source_gross_amount=(
            str(record["source_gross_amount"])
            if record.get("source_gross_amount") is not None
            else None
        ),
        counter_amount=float(record["counter_amount"]) if record.get("counter_amount") is not None else None,
        source_counter_amount=(
            str(record["source_counter_amount"])
            if record.get("source_counter_amount") is not None
            else None
        ),
        fx_rate=float(record["fx_rate"]) if record.get("fx_rate") is not None else None,
        source_fx_rate=str(record["source_fx_rate"]) if record.get("source_fx_rate") is not None else None,
        fees=float(record.get("fees") or 0.0),
        source_fees=str(record["source_fees"]) if record.get("source_fees") is not None else None,
        fee_category=str(record.get("fee_category") or "unknown"),
        taxes=float(record.get("taxes") or 0.0),
        source_taxes=str(record["source_taxes"]) if record.get("source_taxes") is not None else None,
        currency=str(record.get("currency") or ""),
        transfer_scope=str(record.get("transfer_scope")) if record.get("transfer_scope") else None,
        transfer_object_type=(
            str(record.get("transfer_object_type")) if record.get("transfer_object_type") else None
        ),
        transfer_group_id=str(record.get("transfer_group_id")) if record.get("transfer_group_id") else None,
        counterparty_account_id=(
            str(record.get("counterparty_account_id")) if record.get("counterparty_account_id") else None
        ),
        source_system=(
            str(record.get("source_system")) if record.get("source_system") else None
        ),
        asset_deliveries=[
            {key: value for key, value in leg.items() if key != "instrument_ref"}
            for leg in record.get("asset_deliveries") or []
        ],
        settlement_cashflows=record.get("settlement_cashflows") or [],
        lot_selections=record.get("lot_selections") or [],
        external_reference=(
            str(record.get("external_reference"))
            if record.get("external_reference")
            else None
        ),
        net_cash_effect=resolve_transaction_net_cash_effect(record),
        note=str(record.get("note")) if record.get("note") else None,
        created_at=str(record.get("created_at")) if record.get("created_at") else None,
        row_version=int(record.get("row_version") or 1),
    )


def serialize_transactions(
    portfolio_id: str,
    records: list[dict[str, object]],
    account_lookup: dict[str, dict[str, object]],
) -> list[TransactionRecord]:
    return [
        serialize_transaction(portfolio_id, record, account_lookup)
        for record in records
    ]


def summarize_transactions(records: list[dict[str, object]]) -> TransactionListSummary:
    return TransactionListSummary(
        total_transactions=len(records),
        security_transactions=sum(
            1 for item in records if resolve_transaction_asset_domain(item) == "security"
        ),
        derivative_transactions=sum(
            1 for item in records if resolve_transaction_asset_domain(item) == "derivative"
        ),
        fcn_transactions=sum(
            1
            for item in records
            if resolve_transaction_asset_domain(item) == "derivative"
            and resolve_transaction_asset_subtype(item) == "fcn"
        ),
        option_transactions=sum(
            1
            for item in records
            if resolve_transaction_asset_domain(item) == "derivative"
            and resolve_transaction_asset_subtype(item) == "option"
        ),
        cash_transactions=sum(
            1 for item in records if resolve_transaction_asset_domain(item) == "cash"
        ),
        external_cash_flows=sum(1 for item in records if item.get("transaction_type") in {"deposit", "withdrawal"}),
        opening_balance_records=sum(1 for item in records if item.get("transaction_type") in {"opening_balance", "option_opening_balance"}),
    )
