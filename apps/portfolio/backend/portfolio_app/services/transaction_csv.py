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
    MAX_TRANSACTION_IMPORT_RECORDS,
    OptionContractTerms,
    TransactionCreateRequest,
    TransactionImportInternalTransferRequest,
)
from portfolio_app.services.transaction_import import (
    ASSET_TYPE_VALUES,
    DERIVATIVE_DEFINITION_ACTIONS,
    TRANSACTION_ACTION_MAP,
    TRANSFER_FORBIDDEN_FIELDS,
    parse_transaction_import_command,
    render_transaction_validation_errors,
    resolve_transaction_import_action,
    validate_transaction_import_asset_fields,
)


MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = MAX_TRANSACTION_IMPORT_RECORDS

IMPORT_COLUMNS = (
    "asset_type",
    "transaction_action",
    "trade_date",
    "trade_time",
    "settlement_date",
    "position_effective_date",
    "entitlement_date",
    "acquisition_date",
    "account_id",
    "counterparty_account_id",
    "settlement_cash_account_id",
    "instrument_id",
    "derivative_contract_id",
    "derivative_contract_name",
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
    "derivative_additional_terms_json",
    "asset_deliveries_json",
    "lot_selections_json",
    "record_reference",
    "option_delivery_json",
    "quantity",
    "price",
    "gross_amount",
    "counter_amount",
    "fx_rate",
    "fees",
    "fee_category",
    "taxes",
    "currency",
    "source_system",
    "external_reference",
    "note",
)
REQUIRED_COLUMNS = frozenset(
    {
        "asset_type",
        "transaction_action",
        "trade_date",
        "account_id",
        "gross_amount",
        "currency",
    }
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
CORE_DERIVATIVE_TERM_KEYS = {
    "option": {"underlying_instrument_id", "option_type", "expiry_date", "strike", "contract_multiplier"},
    "fcn": {"notional", "annual_coupon_rate_pct", "issue_date", "final_observation_date", "maturity_date", "issuer", "counterparty", "underlyings"},
}
DERIVATIVE_DEFINITION_COLUMNS = frozenset(
    {
        "derivative_contract_name",
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
        "derivative_additional_terms_json",
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
TRANSFER_FORBIDDEN_COLUMNS = (
    TRANSFER_FORBIDDEN_FIELDS - {"derivative_contract"}
) | DERIVATIVE_DEFINITION_COLUMNS


@dataclass(frozen=True)
class ParsedTransactionCsvRow:
    row_number: int
    transaction: TransactionCreateRequest | None
    internal_transfer: TransactionImportInternalTransferRequest | None
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
    *,
    contract_type: str,
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
    additional_terms = json.loads(str(values.get("derivative_additional_terms_json") or "{}"))
    if not isinstance(additional_terms, dict):
        raise ValueError("derivative_additional_terms_json must be a JSON object.")
    core_terms = CORE_DERIVATIVE_TERM_KEYS[contract_type]
    if core_terms.intersection(additional_terms):
        raise ValueError("Use the dedicated columns for core derivative terms; JSON must not override them.")
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
                **additional_terms,
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
                **additional_terms,
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
        raise ValueError("asset_type must be fcn or option for derivative terms.")
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
            if values.get("lot_selections_json"):
                values["lot_selections"] = json.loads(str(values["lot_selections_json"]))
            values.pop("lot_selections_json", None)
            asset_type, _transaction_type, _lifecycle_event_type = (
                resolve_transaction_import_action(values)
            )
            transaction_action = str(
                values.get("transaction_action") or ""
            ).strip().lower()
            definition_supplied = any(
                values.get(field) is not None
                for field in DERIVATIVE_DEFINITION_COLUMNS
            )
            validate_transaction_import_asset_fields(
                values,
                asset_type=asset_type,
                transaction_action=transaction_action,
                derivative_contract=None,
                derivative_definition_supplied=definition_supplied,
            )
            derivative_contract = (
                _build_derivative_contract(values, contract_type=asset_type)
                if asset_type in {"fcn", "option"}
                else None
            )
        except (ValidationError, ValueError) as error:
            errors = (
                render_transaction_validation_errors(error)
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
        try:
            if values.get("option_delivery_json"):
                values["option_delivery"] = json.loads(str(values["option_delivery_json"]))
            values.pop("option_delivery_json", None)
            if values.get("asset_deliveries_json"):
                values["asset_deliveries"] = json.loads(str(values["asset_deliveries_json"]))
            values.pop("asset_deliveries_json", None)
        except (ValueError, TypeError):
            parsed_rows.append(ParsedTransactionCsvRow(row_number=row_number, transaction=None, internal_transfer=None, errors=("Delivery and lot-selection fields must contain valid JSON.",)))
            continue
        parsed_command = parse_transaction_import_command(
            values,
            default_source_system=default_source_system,
            derivative_contract=derivative_contract,
            adapter_only_fields=DERIVATIVE_DEFINITION_COLUMNS,
        )
        parsed_rows.append(
            ParsedTransactionCsvRow(
                row_number=row_number,
                transaction=parsed_command.transaction,
                internal_transfer=parsed_command.internal_transfer,
                errors=parsed_command.errors,
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
    if transfer_in.get("source_system") or transfer_in.get("external_reference"):
        raise ValueError(
            f"Internal transfer group '{transfer_group_id}' must carry source identity "
            "on the transfer_out leg only."
        )
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
        "record_reference": transfer_in.get("transaction_id"),
        "transfer_object_type": transfer_out.get("transfer_object_type"),
        "account_id": from_account_id,
        "counterparty_account_id": to_account_id,
    }
    for column in TRANSFER_FORBIDDEN_COLUMNS:
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


def _record_asset_type(
    record: dict[str, object],
    derivative_contract: dict[str, object] | None,
) -> str:
    explicit_asset_type = str(record.get("asset_type") or "").strip().lower()
    if explicit_asset_type in ASSET_TYPE_VALUES:
        return explicit_asset_type
    if derivative_contract is not None:
        contract_type = str(derivative_contract.get("contract_type") or "").strip().lower()
        if contract_type in {"fcn", "option"}:
            return contract_type
    instrument_ref = record.get("instrument_ref")
    if isinstance(instrument_ref, dict):
        instrument_type = str(instrument_ref.get("instrument_type") or "").strip().lower()
        if instrument_type in {"fcn", "option"}:
            return instrument_type
        if instrument_type:
            return "security"
    if record.get("instrument_id"):
        return "security"
    transaction_type = str(record.get("transaction_type") or "").strip().lower()
    if transaction_type in {"deposit", "withdrawal", "interest", "fx_conversion"}:
        return "cash"
    if not record.get("derivative_contract_id"):
        return "cash"
    raise ValueError("Cannot export a derivative transaction without its contract type.")


def _record_transaction_action(
    record: dict[str, object],
    *,
    asset_type: str,
) -> str:
    transaction_type = str(record.get("transaction_type") or "").strip().lower()
    lifecycle_event_type = str(record.get("lifecycle_event_type") or "").strip().lower()
    if transaction_type == "internal_transfer":
        return "transfer_out"
    if transaction_type in {"transfer_out", "transfer_in"}:
        return "transfer_out" if transaction_type == "transfer_out" else "transfer_in"
    reverse_map = {
        (asset, transaction_type, lifecycle_event_type or ""): action
        for (asset, action), (
            transaction_type,
            lifecycle_event_type,
        ) in TRANSACTION_ACTION_MAP.items()
    }
    try:
        return reverse_map[(asset_type, transaction_type, lifecycle_event_type)]
    except KeyError as error:
        raise ValueError(
            f"Cannot export unsupported transaction combination: {asset_type}/"
            f"{transaction_type}/{lifecycle_event_type}."
        ) from error


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
        asset_type = _record_asset_type(record, derivative_contract)
        transaction_action = _record_transaction_action(record, asset_type=asset_type)
        export_record = dict(record)
        flattened_derivative: dict[str, object] = {}
        if transaction_action not in DERIVATIVE_DEFINITION_ACTIONS.get(asset_type, ()):
            contract_type = ""
        elif derivative_contract is not None:
            flattened_derivative.update(
                {
                    "derivative_contract_name": derivative_contract.get("contract_name"),
                    "derivative_contract_external_reference": derivative_contract.get(
                        "external_reference"
                    ),
                }
            )
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
        if contract_type:
            core_terms = CORE_DERIVATIVE_TERM_KEYS[contract_type]
            additional_terms = {
                key: value for key, value in derivative_terms.items()
                if key not in core_terms and value is not None
            }
            flattened_derivative["derivative_additional_terms_json"] = (
                json.dumps(additional_terms, ensure_ascii=False, separators=(",", ":"))
                if additional_terms else ""
            )
        row: dict[str, object] = {}
        for column in IMPORT_COLUMNS:
            if column == "trade_time" and record.get("trade_time_is_estimated"):
                row[column] = ""
                continue
            if column == "record_reference":
                row[column] = record.get("record_reference") or record.get("transaction_id") or ""
                continue
            if column == "lot_selections_json":
                row[column] = json.dumps(record["lot_selections"], separators=(",", ":")) if record.get("lot_selections") else ""
                continue
            if column == "option_delivery_json":
                row[column] = json.dumps(record["option_delivery"], ensure_ascii=False, separators=(",", ":")) if record.get("option_delivery") else ""
                continue
            if column == "asset_deliveries_json":
                deliveries = [{key: value for key, value in leg.items() if key != "instrument_ref"} for leg in record.get("asset_deliveries") or []]
                row[column] = json.dumps(deliveries, ensure_ascii=False, separators=(",", ":")) if deliveries else ""
                continue
            if column == "asset_type":
                row[column] = asset_type
                continue
            if column == "transaction_action":
                row[column] = transaction_action
                continue
            value = flattened_derivative.get(column, export_record.get(column))
            if column not in flattened_derivative:
                value = _source_value(export_record, column)
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
