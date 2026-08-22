from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Mapping

from pydantic import ValidationError

from portfolio_app.api.contracts import (
    DerivativeContractCreate,
    TransactionCreateRequest,
    TransactionImportInternalTransferRequest,
    TransactionImportPreviewRequest,
)


ASSET_TYPE_VALUES = ("security", "fcn", "option", "cash")
TRANSACTION_ACTIONS: dict[str, tuple[str, ...]] = {
    "security": (
        "buy",
        "sell",
        "dividend",
        "dividend_reinvestment",
        "return_of_capital",
        "fee",
        "tax",
        "transfer_out",
        "transfer_in",
        "opening_balance",
    ),
    "fcn": (
        "entry",
        "early_exit",
        "coupon",
        "knock_in_close",
        "knock_out_close",
        "maturity_close",
        "fee",
        "tax",
        "opening_balance",
    ),
    "option": (
        "buy_to_open",
        "sell_to_close",
        "sell_to_open",
        "buy_to_close",
        "expire_long",
        "cash_settle_long",
        "expire_written",
        "cash_settle_written",
        "fee",
        "tax",
        "opening_balance",
    ),
    "cash": (
        "deposit",
        "withdrawal",
        "interest",
        "fx_conversion",
        "fee",
        "tax",
        "transfer_out",
        "transfer_in",
        "opening_balance",
    ),
}
TRANSACTION_ACTION_MAP: dict[tuple[str, str], tuple[str, str | None]] = {
    ("security", "buy"): ("buy", None),
    ("security", "sell"): ("sell", None),
    ("security", "dividend"): ("dividend", None),
    ("security", "dividend_reinvestment"): ("dividend_reinvestment", None),
    ("security", "return_of_capital"): ("return_of_capital", None),
    ("security", "fee"): ("fee", None),
    ("security", "tax"): ("tax", None),
    ("security", "opening_balance"): ("opening_balance", None),
    ("fcn", "entry"): ("buy", None),
    ("fcn", "early_exit"): ("sell", None),
    ("fcn", "coupon"): ("coupon", None),
    ("fcn", "knock_in_close"): ("maturity_redemption", "fcn_knock_in"),
    ("fcn", "knock_out_close"): ("maturity_redemption", "fcn_knock_out"),
    ("fcn", "maturity_close"): ("maturity_redemption", "fcn_maturity"),
    ("fcn", "fee"): ("fee", None),
    ("fcn", "tax"): ("tax", None),
    ("fcn", "opening_balance"): ("opening_balance", None),
    ("option", "buy_to_open"): ("buy", None),
    ("option", "sell_to_close"): ("sell", None),
    ("option", "sell_to_open"): ("option_write", None),
    ("option", "buy_to_close"): ("option_buy_to_close", None),
    ("option", "expire_long"): ("maturity_redemption", "option_long_expiry"),
    ("option", "cash_settle_long"): (
        "maturity_redemption",
        "option_long_cash_settlement",
    ),
    ("option", "expire_written"): ("lifecycle_event", "option_writer_expiry"),
    ("option", "cash_settle_written"): (
        "lifecycle_event",
        "option_writer_cash_settlement",
    ),
    ("option", "fee"): ("fee", None),
    ("option", "tax"): ("tax", None),
    ("option", "opening_balance"): ("opening_balance", None),
    ("cash", "deposit"): ("deposit", None),
    ("cash", "withdrawal"): ("withdrawal", None),
    ("cash", "interest"): ("interest", None),
    ("cash", "fx_conversion"): ("fx_conversion", None),
    ("cash", "fee"): ("fee", None),
    ("cash", "tax"): ("tax", None),
    ("cash", "opening_balance"): ("opening_balance", None),
}
TRANSFER_ACTIONS = frozenset(
    {
        ("security", "transfer_out"),
        ("security", "transfer_in"),
        ("cash", "transfer_out"),
        ("cash", "transfer_in"),
    }
)
DERIVATIVE_DEFINITION_ACTIONS: dict[str, frozenset[str]] = {
    "fcn": frozenset({"entry", "opening_balance"}),
    "option": frozenset({"buy_to_open", "sell_to_open", "opening_balance"}),
}
TRANSFER_FORBIDDEN_FIELDS = frozenset(
    {
        "position_effective_date",
        "entitlement_date",
        "acquisition_date",
        "settlement_cash_account_id",
        "derivative_contract_id",
        "derivative_contract",
        "price",
        "counter_amount",
        "fx_rate",
        "fees",
        "fee_category",
        "taxes",
    }
)


@dataclass(frozen=True)
class ParsedTransactionCommand:
    transaction: TransactionCreateRequest | None
    internal_transfer: TransactionImportInternalTransferRequest | None
    errors: tuple[str, ...]


def render_transaction_validation_errors(
    error: ValidationError,
) -> tuple[str, ...]:
    rendered: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc") or ())
        message = str(item.get("msg") or "Invalid value")
        rendered.append(f"{location}: {message}" if location else message)
    return tuple(rendered)


def resolve_transaction_import_action(
    values: Mapping[str, object],
) -> tuple[str, str, str | None]:
    asset_type = str(values.get("asset_type") or "").strip().lower()
    transaction_action = str(values.get("transaction_action") or "").strip().lower()
    if asset_type not in ASSET_TYPE_VALUES:
        raise ValueError("asset_type must be security, fcn, option, or cash.")
    allowed_actions = TRANSACTION_ACTIONS[asset_type]
    if transaction_action not in allowed_actions:
        raise ValueError(
            f"transaction_action '{transaction_action}' is not supported for "
            f"asset_type '{asset_type}'; choose one of: "
            + ", ".join(allowed_actions)
            + "."
        )
    if (asset_type, transaction_action) in TRANSFER_ACTIONS:
        return asset_type, "internal_transfer", None
    transaction_type, lifecycle_event_type = TRANSACTION_ACTION_MAP[
        (asset_type, transaction_action)
    ]
    return asset_type, transaction_type, lifecycle_event_type


def validate_transaction_import_asset_fields(
    values: Mapping[str, object],
    *,
    asset_type: str,
    transaction_action: str,
    derivative_contract: DerivativeContractCreate | None,
    derivative_definition_supplied: bool,
) -> None:
    instrument_id = values.get("instrument_id")
    derivative_contract_id = values.get("derivative_contract_id")
    if asset_type == "security":
        if not instrument_id:
            raise ValueError("Security actions require instrument_id.")
        if derivative_contract_id or derivative_definition_supplied:
            raise ValueError(
                "Security actions must not carry derivative contract fields."
            )
        return
    if asset_type in {"fcn", "option"}:
        if instrument_id:
            raise ValueError(
                f"{asset_type.upper()} actions must not carry instrument_id."
            )
        if not derivative_contract_id:
            raise ValueError(
                f"{asset_type.upper()} actions require derivative_contract_id."
            )
        if derivative_contract is not None and derivative_contract.contract_type != asset_type:
            raise ValueError(
                "derivative_contract.contract_type must match asset_type."
            )
        if derivative_definition_supplied:
            opening_actions = DERIVATIVE_DEFINITION_ACTIONS[asset_type]
            if transaction_action not in opening_actions:
                raise ValueError(
                    f"A new {asset_type.upper()} contract can only be defined on: "
                    + ", ".join(sorted(opening_actions))
                    + "."
                )
        return
    if instrument_id or derivative_contract_id or derivative_definition_supplied:
        raise ValueError("Cash actions must not carry security or derivative fields.")


def parse_transaction_import_command(
    values: Mapping[str, object],
    *,
    default_source_system: str | None = None,
    derivative_contract: DerivativeContractCreate | None = None,
    adapter_only_fields: Iterable[str] = (),
) -> ParsedTransactionCommand:
    command_values = dict(values)
    adapter_fields = frozenset(adapter_only_fields)
    try:
        asset_type, transaction_type, lifecycle_event_type = (
            resolve_transaction_import_action(command_values)
        )
        transaction_action = str(
            command_values.get("transaction_action") or ""
        ).strip().lower()
        derivative_definition_supplied = derivative_contract is not None or any(
            command_values.get(field) is not None for field in adapter_fields
        )
        validate_transaction_import_asset_fields(
            command_values,
            asset_type=asset_type,
            transaction_action=transaction_action,
            derivative_contract=derivative_contract,
            derivative_definition_supplied=derivative_definition_supplied,
        )
        if not command_values.get("source_system") and default_source_system:
            command_values["source_system"] = default_source_system.strip() or None

        if transaction_type == "internal_transfer":
            unexpected = sorted(
                field
                for field in TRANSFER_FORBIDDEN_FIELDS | adapter_fields
                if command_values.get(field) is not None
            )
            if unexpected:
                raise ValueError(
                    "Transfer actions must not carry unrelated transaction fields: "
                    + ", ".join(unexpected)
                    + "."
                )
            account_id = str(command_values.get("account_id") or "").strip()
            counterparty_account_id = str(
                command_values.get("counterparty_account_id") or ""
            ).strip()
            transfer_out = transaction_action == "transfer_out"
            internal_transfer = TransactionImportInternalTransferRequest.model_validate(
                {
                    "trade_date": command_values.get("trade_date"),
                    "trade_time": command_values.get("trade_time"),
                    "settlement_date": command_values.get("settlement_date"),
                    "transfer_object_type": (
                        "cash" if asset_type == "cash" else "position"
                    ),
                    "from_account_id": (
                        account_id if transfer_out else counterparty_account_id
                    ),
                    "to_account_id": (
                        counterparty_account_id if transfer_out else account_id
                    ),
                    "instrument_id": command_values.get("instrument_id"),
                    "quantity": command_values.get("quantity"),
                    "gross_amount": command_values.get("gross_amount"),
                    "currency": command_values.get("currency"),
                    "source_system": command_values.get("source_system"),
                    "external_reference": command_values.get("external_reference"),
                    "note": command_values.get("note"),
                }
            )
            return ParsedTransactionCommand(
                transaction=None,
                internal_transfer=internal_transfer,
                errors=(),
            )

        if command_values.get("fees") is None:
            command_values["fees"] = "0"
        if command_values.get("taxes") is None:
            command_values["taxes"] = "0"
        if command_values.get("fee_category") is None:
            command_values["fee_category"] = "unknown"
        transaction_values = {
            key: value
            for key, value in command_values.items()
            if key not in adapter_fields
            and key not in {"asset_type", "transaction_action", "derivative_contract"}
        }
        transaction_values["transaction_type"] = transaction_type
        transaction_values["lifecycle_event_type"] = lifecycle_event_type
        transaction_values["derivative_contract"] = derivative_contract
        transaction = TransactionCreateRequest.model_validate(transaction_values)
        return ParsedTransactionCommand(
            transaction=transaction,
            internal_transfer=None,
            errors=(),
        )
    except (ValidationError, ValueError) as error:
        errors = (
            render_transaction_validation_errors(error)
            if isinstance(error, ValidationError)
            else (str(error),)
        )
        return ParsedTransactionCommand(
            transaction=None,
            internal_transfer=None,
            errors=errors,
        )


def transaction_import_digest(payload: TransactionImportPreviewRequest) -> str:
    canonical_payload = payload.model_dump(
        mode="json",
        exclude={"preview_digest"},
        exclude_none=False,
    )
    serialized = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()
