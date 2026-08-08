from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from typing import Iterable

from pydantic import ValidationError

from portfolio_app.api.contracts import TransactionCreateRequest


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
            transaction = TransactionCreateRequest.model_validate(values)
        except ValidationError as error:
            parsed_rows.append(
                ParsedTransactionCsvRow(
                    row_number=row_number,
                    transaction=None,
                    errors=_validation_errors(error),
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
        row: dict[str, object] = {}
        for column in EXPORT_COLUMNS:
            value = record.get(column)
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
