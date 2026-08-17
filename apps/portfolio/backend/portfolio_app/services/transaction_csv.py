from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from typing import Iterable

from pydantic import ValidationError

from portfolio_app.api.contracts import (
    DerivativeContractCreate,
    FCNContractTerms,
    OptionContractTerms,
    TransactionCreateRequest,
    TransactionCsvInternalTransferRequest,
)


MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 5_000

IMPORT_COLUMNS = (
    "transaction_type",
    "lifecycle_event_type",
    "trade_date",
    "trade_time",
    "settlement_date",
    "position_effective_date",
    "entitlement_date",
    "acquisition_date",
    "transfer_object_type",
    "from_account_id",
    "to_account_id",
    "account_id",
    "settlement_cash_account_id",
    "instrument_id",
    "derivative_contract_id",
    "derivative_contract_name",
    "derivative_contract_type",
    "derivative_contract_external_reference",
    "option_underlying_instrument_id",
    "option_type",
    "option_expiry_date",
    "option_strike",
    "option_contract_multiplier",
    "fcn_notional",
    "fcn_annual_coupon_rate_pct",
    "fcn_issue_date",
    "fcn_final_observation_date",
    "fcn_maturity_date",
    "fcn_issuer",
    "fcn_counterparty",
    "fcn_underlyings_json",
    "quantity",
    "price",
    "gross_amount",
    "counter_amount",
    "fx_rate",
    "fees",
    "fee_category",
    "taxes",
    "currency",
    "counterparty_account_id",
    "source_system",
    "external_reference",
    "note",
)
REQUIRED_COLUMNS = frozenset(
    {"transaction_type", "trade_date", "account_id", "gross_amount", "currency"}
)
TRANSFER_COMMAND_COLUMNS = frozenset(
    {"transfer_object_type", "from_account_id", "to_account_id"}
)
NUMERIC_SOURCE_FIELDS = {
    "quantity": "source_quantity",
    "price": "source_price",
    "gross_amount": "source_gross_amount",
    "counter_amount": "source_counter_amount",
    "fx_rate": "source_fx_rate",
    "fees": "source_fees",
    "taxes": "source_taxes",
}
SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@")
DERIVATIVE_DEFINITION_COLUMNS = frozenset(
    {
        "derivative_contract_name",
        "derivative_contract_type",
        "derivative_contract_external_reference",
        "option_underlying_instrument_id",
        "option_type",
        "option_expiry_date",
        "option_strike",
        "option_contract_multiplier",
        "fcn_notional",
        "fcn_annual_coupon_rate_pct",
        "fcn_issue_date",
        "fcn_final_observation_date",
        "fcn_maturity_date",
        "fcn_issuer",
        "fcn_counterparty",
        "fcn_underlyings_json",
    }
)
OPTION_TERM_COLUMNS = frozenset(
    {
        "option_underlying_instrument_id",
        "option_type",
        "option_expiry_date",
        "option_strike",
        "option_contract_multiplier",
    }
)
FCN_TERM_COLUMNS = frozenset(
    {
        "fcn_notional",
        "fcn_annual_coupon_rate_pct",
        "fcn_issue_date",
        "fcn_final_observation_date",
        "fcn_maturity_date",
        "fcn_issuer",
        "fcn_counterparty",
        "fcn_underlyings_json",
    }
)
INTERNAL_TRANSFER_FORBIDDEN_COLUMNS = frozenset(
    {
        "lifecycle_event_type",
        "position_effective_date",
        "entitlement_date",
        "acquisition_date",
        "account_id",
        "settlement_cash_account_id",
        "derivative_contract_id",
        *DERIVATIVE_DEFINITION_COLUMNS,
        "price",
        "counter_amount",
        "fx_rate",
        "fees",
        "fee_category",
        "taxes",
        "counterparty_account_id",
        "source_system",
        "external_reference",
    }
)


@dataclass(frozen=True)
class ParsedTransactionCsvRow:
    row_number: int
    transaction: TransactionCreateRequest | None
    internal_transfer: TransactionCsvInternalTransferRequest | None
    errors: tuple[str, ...]


def transaction_csv_digest(csv_text: str, *, default_source_system: str | None) -> str:
    payload = (
        csv_text.replace("\r\n", "\n").replace("\r", "\n")
        + "\0"
        + str(default_source_system or "").strip()
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _desanitize_cell(value: str) -> str:
    if len(value) >= 2 and value[0] == "'" and value[1] in SPREADSHEET_FORMULA_PREFIXES:
        return value[1:]
    return value


def _sanitize_cell(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    if value[0] in SPREADSHEET_FORMULA_PREFIXES:
        return "'" + value
    return value


def _validation_errors(error: ValidationError) -> tuple[str, ...]:
    rendered: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc") or ())
        message = str(item.get("msg") or "Invalid value")
        rendered.append(f"{location}: {message}" if location else message)
    return tuple(rendered)


def _parse_fcn_underlyings(value: object) -> list[object]:
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as error:
        raise ValueError("fcn_underlyings_json must contain valid JSON.") from error
    if not isinstance(parsed, list):
        raise ValueError("fcn_underlyings_json must be a JSON array.")
    return parsed


def _build_derivative_contract(
    values: dict[str, object],
) -> DerivativeContractCreate | None:
    supplied_definition_columns = {
        column for column in DERIVATIVE_DEFINITION_COLUMNS if values.get(column) is not None
    }
    if not supplied_definition_columns:
        return None
    derivative_contract_id = values.get("derivative_contract_id")
    if derivative_contract_id is None:
        raise ValueError(
            "derivative_contract_id is required when defining a derivative contract."
        )
    contract_type = str(values.get("derivative_contract_type") or "").strip().lower()
    if contract_type == "option":
        unexpected = sorted(
            column for column in FCN_TERM_COLUMNS if values.get(column) is not None
        )
        if unexpected:
            raise ValueError(
                "Option contract rows must not carry FCN columns: "
                + ", ".join(unexpected)
                + "."
            )
        terms = OptionContractTerms.model_validate(
            {
                "underlying_instrument_id": values.get(
                    "option_underlying_instrument_id"
                ),
                "option_type": values.get("option_type"),
                "expiry_date": values.get("option_expiry_date"),
                "strike": values.get("option_strike"),
                "contract_multiplier": values.get("option_contract_multiplier"),
            }
        )
    elif contract_type == "fcn":
        unexpected = sorted(
            column for column in OPTION_TERM_COLUMNS if values.get(column) is not None
        )
        if unexpected:
            raise ValueError(
                "FCN contract rows must not carry option columns: "
                + ", ".join(unexpected)
                + "."
            )
        terms = FCNContractTerms.model_validate(
            {
                "notional": values.get("fcn_notional"),
                "annual_coupon_rate_pct": values.get(
                    "fcn_annual_coupon_rate_pct"
                ),
                "issue_date": values.get("fcn_issue_date"),
                "final_observation_date": values.get(
                    "fcn_final_observation_date"
                ),
                "maturity_date": values.get("fcn_maturity_date"),
                "issuer": values.get("fcn_issuer"),
                "counterparty": values.get("fcn_counterparty"),
                "underlyings": _parse_fcn_underlyings(
                    values.get("fcn_underlyings_json")
                ),
            }
        )
    else:
        raise ValueError("derivative_contract_type must be fcn or option.")
    return DerivativeContractCreate.model_validate(
        {
            "derivative_contract_id": derivative_contract_id,
            "contract_name": values.get("derivative_contract_name"),
            "contract_type": contract_type,
            "external_reference": values.get(
                "derivative_contract_external_reference"
            ),
            "terms": terms,
        }
    )


def parse_transaction_csv(
    csv_text: str,
    *,
    default_source_system: str | None = None,
) -> tuple[tuple[str, ...], list[ParsedTransactionCsvRow]]:
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ValueError("CSV content is required.")
    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("CSV content exceeds the 5 MB limit.")

    normalized_text = csv_text.lstrip("\ufeff")
    reader = csv.DictReader(StringIO(normalized_text, newline=""))
    if reader.fieldnames is None:
        raise ValueError("CSV header is required.")
    headers = tuple(str(header or "").strip() for header in reader.fieldnames)
    if any(not header for header in headers):
        raise ValueError("CSV header contains a blank column name.")
    if len(headers) != len(set(headers)):
        raise ValueError("CSV header contains duplicate column names.")
    unknown = sorted(set(headers) - set(IMPORT_COLUMNS))
    if unknown:
        raise ValueError("Unsupported CSV columns: " + ", ".join(unknown) + ".")
    missing = sorted(REQUIRED_COLUMNS - set(headers))
    if missing:
        raise ValueError("Missing required CSV columns: " + ", ".join(missing) + ".")

    parsed_rows: list[ParsedTransactionCsvRow] = []
    for row_number, raw_row in enumerate(reader, start=2):
        if len(parsed_rows) >= MAX_CSV_ROWS:
            raise ValueError("CSV content exceeds the 5,000 row limit.")
        if None in raw_row:
            parsed_rows.append(
                ParsedTransactionCsvRow(
                    row_number=row_number,
                    transaction=None,
                    internal_transfer=None,
                    errors=("Row contains more values than the CSV header.",),
                )
            )
            continue
        if not any(str(value or "").strip() for value in raw_row.values()):
            continue

        values: dict[str, object] = {}
        for column in IMPORT_COLUMNS:
            if column not in raw_row:
                continue
            normalized = _desanitize_cell(str(raw_row.get(column) or "").strip())
            values[column] = normalized if normalized else None
        try:
            transaction_type = str(values.get("transaction_type") or "").strip()
            if transaction_type in {"transfer_in", "transfer_out"}:
                raise ValueError(
                    "Import internal transfers as one internal_transfer command, not as "
                    "system-generated transfer_in or transfer_out legs."
                )
            if transaction_type == "internal_transfer":
                unexpected = sorted(
                    column
                    for column in INTERNAL_TRANSFER_FORBIDDEN_COLUMNS
                    if values.get(column) is not None
                )
                if unexpected:
                    raise ValueError(
                        "internal_transfer rows must not carry ordinary transaction columns: "
                        + ", ".join(unexpected)
                        + "."
                    )
                internal_transfer = TransactionCsvInternalTransferRequest.model_validate(
                    {
                        "trade_date": values.get("trade_date"),
                        "trade_time": values.get("trade_time"),
                        "settlement_date": values.get("settlement_date"),
                        "transfer_object_type": values.get("transfer_object_type"),
                        "from_account_id": values.get("from_account_id"),
                        "to_account_id": values.get("to_account_id"),
                        "instrument_id": values.get("instrument_id"),
                        "quantity": values.get("quantity"),
                        "gross_amount": values.get("gross_amount"),
                        "currency": values.get("currency"),
                        "note": values.get("note"),
                    }
                )
                transaction = None
            else:
                unexpected = sorted(
                    column for column in TRANSFER_COMMAND_COLUMNS if values.get(column) is not None
                )
                if unexpected:
                    raise ValueError(
                        "Transfer command columns require transaction_type=internal_transfer: "
                        + ", ".join(unexpected)
                        + "."
                    )
                if not values.get("source_system") and default_source_system:
                    values["source_system"] = default_source_system.strip() or None
                if values.get("fees") is None:
                    values["fees"] = "0"
                if values.get("taxes") is None:
                    values["taxes"] = "0"
                if values.get("fee_category") is None:
                    values["fee_category"] = "unknown"
                derivative_contract = _build_derivative_contract(values)
                transaction_values = {
                    key: value
                    for key, value in values.items()
                    if key not in DERIVATIVE_DEFINITION_COLUMNS
                    and key not in TRANSFER_COMMAND_COLUMNS
                }
                transaction_values["derivative_contract"] = derivative_contract
                transaction = TransactionCreateRequest.model_validate(transaction_values)
                internal_transfer = None
        except (ValidationError, ValueError) as error:
            errors = (
                _validation_errors(error)
                if isinstance(error, ValidationError)
                else (str(error),)
            )
            parsed_rows.append(
                ParsedTransactionCsvRow(
                    row_number=row_number,
                    transaction=None,
                    internal_transfer=None,
                    errors=errors,
                )
            )
            continue
        parsed_rows.append(
            ParsedTransactionCsvRow(
                row_number=row_number,
                transaction=transaction,
                internal_transfer=internal_transfer,
                errors=(),
            )
        )

    if not parsed_rows:
        raise ValueError("CSV contains no transaction rows.")
    return headers, parsed_rows


def _source_value(record: dict[str, object], column: str) -> object:
    source_field = NUMERIC_SOURCE_FIELDS.get(column)
    if source_field and record.get(source_field) is not None:
        return record.get(source_field)
    return record.get(column)


def _internal_transfer_command(
    transfer_group_id: str,
    records: list[dict[str, object]],
) -> dict[str, object]:
    if len(records) != 2:
        raise ValueError(
            f"Internal transfer group '{transfer_group_id}' must contain exactly two legs."
        )
    by_type = {str(record.get("transaction_type") or ""): record for record in records}
    if set(by_type) != {"transfer_out", "transfer_in"}:
        raise ValueError(
            f"Internal transfer group '{transfer_group_id}' must contain one transfer_out "
            "and one transfer_in leg."
        )
    transfer_out = by_type["transfer_out"]
    transfer_in = by_type["transfer_in"]
    from_account_id = str(transfer_out.get("account_id") or "")
    to_account_id = str(transfer_in.get("account_id") or "")
    if (
        str(transfer_out.get("counterparty_account_id") or "") != to_account_id
        or str(transfer_in.get("counterparty_account_id") or "") != from_account_id
    ):
        raise ValueError(
            f"Internal transfer group '{transfer_group_id}' has inconsistent account legs."
        )
    matching_columns = (
        "trade_date",
        "trade_time",
        "settlement_date",
        "instrument_id",
        "quantity",
        "gross_amount",
        "currency",
        "transfer_object_type",
        "note",
    )
    mismatches = [
        column
        for column in matching_columns
        if str(_source_value(transfer_out, column) or "")
        != str(_source_value(transfer_in, column) or "")
    ]
    if mismatches:
        raise ValueError(
            f"Internal transfer group '{transfer_group_id}' has mismatched legs: "
            + ", ".join(mismatches)
            + "."
        )
    command = {
        **transfer_out,
        "transaction_type": "internal_transfer",
        "transfer_object_type": transfer_out.get("transfer_object_type"),
        "from_account_id": from_account_id,
        "to_account_id": to_account_id,
        "account_id": None,
        "counterparty_account_id": None,
    }
    for column in INTERNAL_TRANSFER_FORBIDDEN_COLUMNS:
        command[column] = None
        source_field = NUMERIC_SOURCE_FIELDS.get(column)
        if source_field:
            command[source_field] = None
    return command


def _canonical_export_records(
    records: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    ordered_commands: list[tuple[int, int, dict[str, object]]] = []
    transfer_groups: dict[str, list[tuple[int, dict[str, object]]]] = {}
    for index, record in enumerate(records):
        transaction_type = str(record.get("transaction_type") or "")
        raw_sequence = record.get("transaction_sequence")
        sequence = int(raw_sequence) if raw_sequence is not None else index + 1
        if transaction_type in {"transfer_out", "transfer_in"}:
            transfer_group_id = str(record.get("transfer_group_id") or "").strip()
            if not transfer_group_id:
                raise ValueError("Internal transfer leg is missing transfer_group_id.")
            transfer_groups.setdefault(transfer_group_id, []).append((sequence, record))
        else:
            ordered_commands.append((sequence, index, record))

    next_index = len(ordered_commands)
    for transfer_group_id, grouped_records in transfer_groups.items():
        sequence = min(item[0] for item in grouped_records)
        command = _internal_transfer_command(
            transfer_group_id,
            [item[1] for item in grouped_records],
        )
        ordered_commands.append((sequence, next_index, command))
        next_index += 1
    return [item[2] for item in sorted(ordered_commands, key=lambda item: (item[0], item[1]))]


def transaction_export_rows(
    records: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in _canonical_export_records(records):
        derivative_contract = (
            record.get("derivative_contract")
            if isinstance(record.get("derivative_contract"), dict)
            else None
        )
        derivative_terms = (
            derivative_contract.get("terms")
            if derivative_contract is not None
            and isinstance(derivative_contract.get("terms"), dict)
            else {}
        )
        contract_type = (
            str(derivative_contract.get("contract_type") or "")
            if derivative_contract is not None
            else ""
        )
        flattened_derivative: dict[str, object] = {
            "derivative_contract_name": (
                derivative_contract.get("contract_name")
                if derivative_contract is not None
                else None
            ),
            "derivative_contract_type": contract_type or None,
            "derivative_contract_external_reference": (
                derivative_contract.get("external_reference")
                if derivative_contract is not None
                else None
            ),
        }
        if contract_type == "option":
            flattened_derivative.update(
                {
                    "option_underlying_instrument_id": derivative_terms.get(
                        "underlying_instrument_id"
                    ),
                    "option_type": derivative_terms.get("option_type"),
                    "option_expiry_date": derivative_terms.get("expiry_date"),
                    "option_strike": derivative_terms.get("strike"),
                    "option_contract_multiplier": derivative_terms.get(
                        "contract_multiplier"
                    ),
                }
            )
        elif contract_type == "fcn":
            flattened_derivative.update(
                {
                    "fcn_notional": derivative_terms.get("notional"),
                    "fcn_annual_coupon_rate_pct": derivative_terms.get(
                        "annual_coupon_rate_pct"
                    ),
                    "fcn_issue_date": derivative_terms.get("issue_date"),
                    "fcn_final_observation_date": derivative_terms.get(
                        "final_observation_date"
                    ),
                    "fcn_maturity_date": derivative_terms.get("maturity_date"),
                    "fcn_issuer": derivative_terms.get("issuer"),
                    "fcn_counterparty": derivative_terms.get("counterparty"),
                    "fcn_underlyings_json": json.dumps(
                        derivative_terms.get("underlyings", []),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        row: dict[str, object] = {}
        for column in IMPORT_COLUMNS:
            value = flattened_derivative.get(column, record.get(column))
            if column not in flattened_derivative:
                value = _source_value(record, column)
            if value is None:
                value = ""
            row[column] = value
        rows.append(row)
    return rows


def render_transaction_csv(records: Iterable[dict[str, object]]) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(IMPORT_COLUMNS), lineterminator="\r\n")
    writer.writeheader()
    for row in transaction_export_rows(records):
        writer.writerow(
            {column: _sanitize_cell(row.get(column, "")) for column in IMPORT_COLUMNS}
        )
    return "\ufeff" + output.getvalue()


def render_transaction_csv_template() -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(IMPORT_COLUMNS)
    return "\ufeff" + output.getvalue()
