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
EXPORT_ONLY_COLUMNS = (
    "transaction_id",
    "row_version",
    "created_at",
    "asset_domain",
    "asset_subtype",
    # Derived read-only semantic; imports use the persisted transaction_type
    # and the backend resolver remains authoritative.
    "option_action",
)
EXPORT_COLUMNS = (*EXPORT_ONLY_COLUMNS, *IMPORT_COLUMNS)
REQUIRED_COLUMNS = frozenset(
    {"transaction_type", "trade_date", "account_id", "gross_amount", "currency"}
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


@dataclass(frozen=True)
class ParsedTransactionCsvRow:
    row_number: int
    transaction: TransactionCreateRequest | None
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
    unknown = sorted(set(headers) - set(EXPORT_COLUMNS))
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
        if not values.get("source_system") and default_source_system:
            values["source_system"] = default_source_system.strip() or None
        if values.get("fees") is None:
            values["fees"] = "0"
        if values.get("taxes") is None:
            values["taxes"] = "0"
        if values.get("fee_category") is None:
            values["fee_category"] = "unknown"
        try:
            derivative_contract = _build_derivative_contract(values)
            transaction_values = {
                key: value
                for key, value in values.items()
                if key not in DERIVATIVE_DEFINITION_COLUMNS
            }
            transaction_values["derivative_contract"] = derivative_contract
            transaction = TransactionCreateRequest.model_validate(transaction_values)
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
                    errors=errors,
                )
            )
            continue
        parsed_rows.append(
            ParsedTransactionCsvRow(
                row_number=row_number,
                transaction=transaction,
                errors=(),
            )
        )

    if not parsed_rows:
        raise ValueError("CSV contains no transaction rows.")
    return headers, parsed_rows


def render_transaction_csv(records: Iterable[dict[str, object]]) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(EXPORT_COLUMNS), lineterminator="\r\n")
    writer.writeheader()
    for record in records:
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
        instrument_ref = (
            record.get("instrument_ref")
            if isinstance(record.get("instrument_ref"), dict)
            else None
        )
        if record.get("derivative_contract_id"):
            asset_domain = "derivative"
            asset_subtype = contract_type or None
        elif record.get("instrument_id"):
            asset_domain = "security"
            asset_subtype = (
                instrument_ref.get("instrument_type")
                if instrument_ref is not None
                else None
            )
        else:
            asset_domain = "cash"
            asset_subtype = None
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
            "asset_domain": asset_domain,
            "asset_subtype": asset_subtype,
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
        for column in EXPORT_COLUMNS:
            value = flattened_derivative.get(column, record.get(column))
            source_field = NUMERIC_SOURCE_FIELDS.get(column)
            if source_field and record.get(source_field) is not None:
                value = record.get(source_field)
            if value is None:
                value = ""
            row[column] = _sanitize_cell(value)
        writer.writerow(row)
    return "\ufeff" + output.getvalue()


def render_transaction_csv_template() -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(IMPORT_COLUMNS)
    return "\ufeff" + output.getvalue()
