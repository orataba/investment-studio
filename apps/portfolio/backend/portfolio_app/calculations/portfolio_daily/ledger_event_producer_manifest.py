"""Validation of sealed manifest identity, config, account, and instrument facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import cast
from uuid import UUID

from portfolio_app.calculations.numeric import canonical_decimal, canonical_sha256_hex
from portfolio_app.calculations.portfolio_daily.constants import (
    ACCOUNT_SCHEMA_VERSION,
    CONFIG_SCHEMA_VERSION,
    INSTRUMENT_SCHEMA_VERSION,
)
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    CostBasisMethod,
    LedgerFactKind,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_common import (
    ACCOUNT_TABLE,
    CONFIG_TABLE,
    CORPORATE_ACTION_TABLE,
    CORPORATE_ACTION_WINDOW_TABLE,
    FX_LEG_TABLE,
    FX_PATH_TABLE,
    INSTRUMENT_TABLE,
    TRANSACTION_TABLE,
    RowsByTable,
    _Account,
    _BuildContext,
    _CASH_ACCOUNT_TYPES,
    _Instrument,
    _POSITION_ACCOUNT_TYPES,
    _SHA256_HEX,
    _SHA256_PREFIXED,
    _aware_datetime,
    _boolean,
    _currency,
    _date,
    _fail,
    _json_object,
    _optional_date,
    _optional_fact,
    _optional_text,
    _reason_codes,
    _required,
    _text,
    _uuid,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildErrorCode,
)
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    DEPENDENCY_NATURAL_KEYS,
    ManifestDependencies,
)


def _rows_by_table(
    dependencies: ManifestDependencies | RowsByTable,
) -> RowsByTable:
    rows = (
        dependencies.rows_by_table
        if isinstance(dependencies, ManifestDependencies)
        else dependencies
    )
    if not isinstance(rows, Mapping):
        _fail(
            "dependencies must be ManifestDependencies or a rows-by-table mapping",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table="__manifest__",
            record_id="__manifest__",
        )
    expected = set(DEPENDENCY_NATURAL_KEYS)
    actual = set(rows)
    if actual != expected:
        _fail(
            "manifest dependency table set mismatch: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table="__manifest__",
            record_id="__manifest__",
        )
    normalized: dict[str, tuple[Mapping[str, object], ...]] = {}
    for table in sorted(expected):
        source_rows = rows[table]
        if isinstance(source_rows, (str, bytes)) or not isinstance(
            source_rows, Sequence
        ):
            _fail(
                f"{table} rows must be a sequence",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=table,
                record_id="__table__",
            )
        if any(not isinstance(row, Mapping) for row in source_rows):
            _fail(
                f"{table} contains a non-mapping row",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=table,
                record_id="__table__",
            )
        normalized[table] = tuple(cast(Sequence[Mapping[str, object]], source_rows))
    return normalized


def _transaction_replay_rows(rows: RowsByTable) -> RowsByTable:
    """Normalize the exact common-capture subset used before mutation commit."""

    if not isinstance(rows, Mapping):
        _fail(
            "transaction replay dependencies must be a rows-by-table mapping",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table="__transaction_replay__",
            record_id="__transaction_replay__",
        )
    expected = {
        CONFIG_TABLE,
        ACCOUNT_TABLE,
        TRANSACTION_TABLE,
        INSTRUMENT_TABLE,
        CORPORATE_ACTION_TABLE,
        CORPORATE_ACTION_WINDOW_TABLE,
    }
    actual = set(rows)
    if actual != expected:
        _fail(
            "transaction replay dependency table set mismatch: "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table="__transaction_replay__",
            record_id="__transaction_replay__",
        )
    normalized: dict[str, tuple[Mapping[str, object], ...]] = {}
    for table in sorted(expected):
        source_rows = rows[table]
        if isinstance(source_rows, (str, bytes)) or not isinstance(
            source_rows,
            Sequence,
        ):
            _fail(
                f"{table} rows must be a sequence",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=table,
                record_id="__table__",
            )
        if any(not isinstance(row, Mapping) for row in source_rows):
            _fail(
                f"{table} contains a non-mapping row",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=table,
                record_id="__table__",
            )
        normalized[table] = tuple(
            cast(Sequence[Mapping[str, object]], source_rows)
        )
    return normalized


def _validate_identity_rows(rows: RowsByTable) -> tuple[UUID, UUID, str]:
    config_rows = rows[CONFIG_TABLE]
    if len(config_rows) != 1:
        _fail(
            "sealed manifest must contain exactly one Portfolio Daily config row",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CONFIG_TABLE,
            record_id="__config__",
        )
    config = config_rows[0]
    manifest_id = _uuid(
        _required(config, "manifest_id", table=CONFIG_TABLE, record_id="__config__"),
        table=CONFIG_TABLE,
        record_id="__config__",
        field="manifest_id",
    )
    run_id = _uuid(
        _required(config, "run_id", table=CONFIG_TABLE, record_id="__config__"),
        table=CONFIG_TABLE,
        record_id="__config__",
        field="run_id",
    )
    portfolio_id = _text(
        _required(config, "portfolio_id", table=CONFIG_TABLE, record_id="__config__"),
        table=CONFIG_TABLE,
        record_id="__config__",
        field="portfolio_id",
    )
    for table in sorted(rows):
        for index, row in enumerate(rows[table]):
            record_id = _row_record_id(table, row, fallback=str(index))
            if _uuid(
                _required(row, "manifest_id", table=table, record_id=record_id),
                table=table,
                record_id=record_id,
                field="manifest_id",
            ) != manifest_id:
                _fail(
                    f"{table} row belongs to a different manifest",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=table,
                    record_id=record_id,
                    field="manifest_id",
                )
            if _uuid(
                _required(row, "run_id", table=table, record_id=record_id),
                table=table,
                record_id=record_id,
                field="run_id",
            ) != run_id:
                _fail(
                    f"{table} row belongs to a different run",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=table,
                    record_id=record_id,
                    field="run_id",
                )
            if _text(
                _required(row, "portfolio_id", table=table, record_id=record_id),
                table=table,
                record_id=record_id,
                field="portfolio_id",
            ) != portfolio_id:
                _fail(
                    f"{table} row belongs to a different portfolio",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=table,
                    record_id=record_id,
                    field="portfolio_id",
                )
            _aware_datetime(
                _required(row, "captured_at", table=table, record_id=record_id),
                table=table,
                record_id=record_id,
                field="captured_at",
            )
    return manifest_id, run_id, portfolio_id


def _row_record_id(
    table: str,
    row: Mapping[str, object],
    *,
    fallback: str,
) -> str:
    candidates = {
        CONFIG_TABLE: "portfolio_id",
        ACCOUNT_TABLE: "account_id",
        TRANSACTION_TABLE: "transaction_id",
        INSTRUMENT_TABLE: "instrument_id",
        CORPORATE_ACTION_TABLE: "corporate_action_event_id",
        FX_PATH_TABLE: "fx_path_id",
        FX_LEG_TABLE: "fx_path_id",
    }
    field = candidates.get(table)
    value = row.get(field) if field is not None else None
    resolved = str(value) if value is not None else fallback
    return resolved.strip() or fallback


def _hash_matches(
    *,
    value: object,
    expected_payload: object,
    prefixed: bool,
    table: str,
    record_id: str,
    field: str,
) -> None:
    digest = _text(value, table=table, record_id=record_id, field=field)
    pattern = _SHA256_PREFIXED if prefixed else _SHA256_HEX
    if pattern.fullmatch(digest) is None:
        _fail(
            f"{table}.{field} is not a canonical SHA-256 digest",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    calculated = canonical_sha256_hex(expected_payload)
    expected = f"sha256:{calculated}" if prefixed else calculated
    if digest != expected:
        _fail(
            f"{table}.{field} does not match the captured canonical payload",
            code=LedgerEventBuildErrorCode.HASH_MISMATCH,
            table=table,
            record_id=record_id,
            field=field,
        )


def _validate_config(
    rows: RowsByTable,
    *,
    portfolio_id: str,
) -> tuple[str, date, date, datetime]:
    row = rows[CONFIG_TABLE][0]
    record_id = portfolio_id
    schema_version = _text(
        _required(row, "config_schema_version", table=CONFIG_TABLE, record_id=record_id),
        table=CONFIG_TABLE,
        record_id=record_id,
        field="config_schema_version",
    )
    if schema_version != CONFIG_SCHEMA_VERSION:
        _fail(
            f"unsupported config schema version {schema_version}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CONFIG_TABLE,
            record_id=record_id,
            field="config_schema_version",
        )
    canonical = _json_object(
        _required(row, "canonical_config", table=CONFIG_TABLE, record_id=record_id),
        table=CONFIG_TABLE,
        record_id=record_id,
        field="canonical_config",
    )
    _hash_matches(
        value=_required(row, "config_hash", table=CONFIG_TABLE, record_id=record_id),
        expected_payload=canonical,
        prefixed=False,
        table=CONFIG_TABLE,
        record_id=record_id,
        field="config_hash",
    )
    base_currency = _currency(
        _required(row, "base_currency", table=CONFIG_TABLE, record_id=record_id),
        table=CONFIG_TABLE,
        record_id=record_id,
        field="base_currency",
    )
    if canonical.get("portfolio_id") != portfolio_id or canonical.get(
        "base_currency"
    ) != base_currency:
        _fail(
            "canonical config identity/currency differs from typed columns",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CONFIG_TABLE,
            record_id=record_id,
            field="canonical_config",
        )
    effective_as_of = _date(
        _required(row, "effective_as_of", table=CONFIG_TABLE, record_id=record_id),
        table=CONFIG_TABLE,
        record_id=record_id,
        field="effective_as_of",
    )
    range_start = _date(
        _required(row, "range_start", table=CONFIG_TABLE, record_id=record_id),
        table=CONFIG_TABLE,
        record_id=record_id,
        field="range_start",
    )
    if range_start > effective_as_of:
        _fail(
            "Portfolio Daily range_start follows effective_as_of",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CONFIG_TABLE,
            record_id=record_id,
            field="range_start",
        )
    knowledge_cutoff_at = _aware_datetime(
        _required(
            row,
            "knowledge_cutoff_at",
            table=CONFIG_TABLE,
            record_id=record_id,
        ),
        table=CONFIG_TABLE,
        record_id=record_id,
        field="knowledge_cutoff_at",
    )
    return base_currency, effective_as_of, range_start, knowledge_cutoff_at


def _validate_accounts(rows: RowsByTable) -> dict[str, _Account]:
    accounts: dict[str, _Account] = {}
    for row in rows[ACCOUNT_TABLE]:
        record_id = _text(
            _required(row, "account_id", table=ACCOUNT_TABLE, record_id="__row__"),
            table=ACCOUNT_TABLE,
            record_id="__row__",
            field="account_id",
        )
        if record_id in accounts:
            _fail(
                f"duplicate account_id {record_id}",
                code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                table=ACCOUNT_TABLE,
                record_id=record_id,
                field="account_id",
            )
        schema_version = _text(
            _required(
                row,
                "account_schema_version",
                table=ACCOUNT_TABLE,
                record_id=record_id,
            ),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="account_schema_version",
        )
        if schema_version != ACCOUNT_SCHEMA_VERSION:
            _fail(
                f"unsupported account schema version {schema_version}",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=record_id,
                field="account_schema_version",
            )
        canonical = _json_object(
            _required(
                row,
                "canonical_account",
                table=ACCOUNT_TABLE,
                record_id=record_id,
            ),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="canonical_account",
        )
        _hash_matches(
            value=_required(
                row,
                "account_hash",
                table=ACCOUNT_TABLE,
                record_id=record_id,
            ),
            expected_payload=canonical,
            prefixed=False,
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="account_hash",
        )
        account_type = _text(
            _required(row, "account_type", table=ACCOUNT_TABLE, record_id=record_id),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="account_type",
        )
        if account_type not in _CASH_ACCOUNT_TYPES | _POSITION_ACCOUNT_TYPES:
            _fail(
                f"unsupported account_type {account_type}",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=record_id,
                field="account_type",
            )
        currency = _currency(
            _required(row, "currency", table=ACCOUNT_TABLE, record_id=record_id),
            table=ACCOUNT_TABLE,
            record_id=record_id,
        )
        default_cash = _optional_text(
            _required(
                row,
                "default_settlement_cash_account_id",
                table=ACCOUNT_TABLE,
                record_id=record_id,
            ),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="default_settlement_cash_account_id",
        )
        raw_method = _optional_text(
            _required(
                row,
                "cost_basis_method",
                table=ACCOUNT_TABLE,
                record_id=record_id,
            ),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="cost_basis_method",
        )
        method: CostBasisMethod | None = None
        if raw_method is not None:
            try:
                method = CostBasisMethod(raw_method)
            except ValueError:
                _fail(
                    f"unsupported cost_basis_method {raw_method}",
                    code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                    table=ACCOUNT_TABLE,
                    record_id=record_id,
                    field="cost_basis_method",
                )
        if account_type in _CASH_ACCOUNT_TYPES and (
            default_cash is not None or method is not None
        ):
            _fail(
                "cash account must not carry settlement-account or cost-basis terms",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=record_id,
            )
        if account_type in _POSITION_ACCOUNT_TYPES and method is None:
            _fail(
                "securities account requires an explicit cost_basis_method",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=record_id,
                field="cost_basis_method",
            )
        opened_at = _optional_date(
            _required(row, "opened_at", table=ACCOUNT_TABLE, record_id=record_id),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="opened_at",
        )
        closed_at = _optional_date(
            _required(row, "closed_at", table=ACCOUNT_TABLE, record_id=record_id),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="closed_at",
        )
        if opened_at is not None and closed_at is not None and closed_at < opened_at:
            _fail(
                "account closed_at precedes opened_at",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=record_id,
                field="closed_at",
            )
        _text(
            _required(
                row,
                "account_status",
                table=ACCOUNT_TABLE,
                record_id=record_id,
            ),
            table=ACCOUNT_TABLE,
            record_id=record_id,
            field="account_status",
        )
        if (
            canonical.get("account_id") != record_id
            or canonical.get("account_type") != account_type
            or canonical.get("currency") != currency
            or canonical.get("default_settlement_cash_account_id") != default_cash
            or canonical.get("cost_basis_method") != raw_method
        ):
            _fail(
                "canonical account differs from typed account columns",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=record_id,
                field="canonical_account",
            )
        accounts[record_id] = _Account(
            account_id=record_id,
            account_type=account_type,
            currency=currency,
            default_settlement_cash_account_id=default_cash,
            cost_basis_method=method,
            opened_at=opened_at,
            closed_at=closed_at,
        )
    if not accounts:
        _fail(
            "Portfolio Daily manifest contains no accounts",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=ACCOUNT_TABLE,
            record_id="__table__",
        )
    for account in accounts.values():
        if account.default_settlement_cash_account_id is None:
            continue
        settlement = accounts.get(account.default_settlement_cash_account_id)
        if settlement is None:
            _fail(
                "default settlement cash account is absent from the manifest",
                code=LedgerEventBuildErrorCode.MISSING_REFERENCE,
                table=ACCOUNT_TABLE,
                record_id=account.account_id,
                field="default_settlement_cash_account_id",
            )
        if settlement.account_type not in _CASH_ACCOUNT_TYPES:
            _fail(
                "default settlement account is not a cash account",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=account.account_id,
                field="default_settlement_cash_account_id",
            )
        if settlement.currency != account.currency:
            _fail(
                "securities and default settlement accounts have different currencies",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=ACCOUNT_TABLE,
                record_id=account.account_id,
                field="default_settlement_cash_account_id",
            )
    return accounts


def _validate_instruments(rows: RowsByTable) -> dict[str, _Instrument]:
    instruments: dict[str, _Instrument] = {}
    for row in rows[INSTRUMENT_TABLE]:
        record_id = _text(
            _required(row, "instrument_id", table=INSTRUMENT_TABLE, record_id="__row__"),
            table=INSTRUMENT_TABLE,
            record_id="__row__",
            field="instrument_id",
        )
        if record_id in instruments:
            _fail(
                f"duplicate instrument_id {record_id}",
                code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                table=INSTRUMENT_TABLE,
                record_id=record_id,
                field="instrument_id",
            )
        schema_version = _text(
            _required(
                row,
                "instrument_schema_version",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="instrument_schema_version",
        )
        if schema_version != INSTRUMENT_SCHEMA_VERSION:
            _fail(
                f"unsupported instrument schema version {schema_version}",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=INSTRUMENT_TABLE,
                record_id=record_id,
                field="instrument_schema_version",
            )
        canonical = _json_object(
            _required(
                row,
                "canonical_instrument",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="canonical_instrument",
        )
        _hash_matches(
            value=_required(
                row,
                "instrument_hash",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            expected_payload=canonical,
            prefixed=False,
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="instrument_hash",
        )
        instrument_type = _text(
            _required(
                row,
                "instrument_type",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="instrument_type",
        )
        if instrument_type != instrument_type.lower():
            _fail(
                "instrument_type must be lowercase canonical text",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=INSTRUMENT_TABLE,
                record_id=record_id,
                field="instrument_type",
            )
        currency = _currency(
            _required(row, "currency", table=INSTRUMENT_TABLE, record_id=record_id),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
        )
        _boolean(
            _required(
                row,
                "requires_valuation",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="requires_valuation",
        )
        state = _text(
            _required(
                row,
                "valuation_contract_state",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="valuation_contract_state",
        )
        if state not in {"available", "unavailable"}:
            _fail(
                f"unsupported valuation_contract_state {state}",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=INSTRUMENT_TABLE,
                record_id=record_id,
                field="valuation_contract_state",
            )
        price_unit = _optional_text(
            _required(row, "price_unit", table=INSTRUMENT_TABLE, record_id=record_id),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="price_unit",
        )
        multiplier = _optional_fact(
            _required(
                row,
                "contract_multiplier",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            kind=LedgerFactKind.RATIO,
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="contract_multiplier",
            strictly_positive=True,
        )
        factor = _optional_fact(
            _required(
                row,
                "price_factor",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            kind=LedgerFactKind.RATIO,
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="price_factor",
            strictly_positive=True,
        )
        reasons = _reason_codes(
            _required(
                row,
                "valuation_contract_reason_codes",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="valuation_contract_reason_codes",
        )
        source = _optional_text(
            _required(
                row,
                "valuation_factor_source",
                table=INSTRUMENT_TABLE,
                record_id=record_id,
            ),
            table=INSTRUMENT_TABLE,
            record_id=record_id,
            field="valuation_factor_source",
        )
        if state == "available":
            if price_unit is None or multiplier is None or factor is None or reasons:
                _fail(
                    "available valuation contract is incomplete",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=INSTRUMENT_TABLE,
                    record_id=record_id,
                    field="valuation_contract_state",
                )
            if source not in {"instrument", "methodology"}:
                _fail(
                    "available valuation contract has invalid factor source",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=INSTRUMENT_TABLE,
                    record_id=record_id,
                    field="valuation_factor_source",
                )
        elif not reasons or (
            price_unit is not None and multiplier is not None and factor is not None
        ):
            _fail(
                "unavailable valuation contract has inconsistent terms/reasons",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=INSTRUMENT_TABLE,
                record_id=record_id,
                field="valuation_contract_state",
            )
        canonical_contract = canonical.get("valuation_contract")
        if not isinstance(canonical_contract, Mapping):
            _fail(
                "canonical instrument lacks valuation_contract object",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=INSTRUMENT_TABLE,
                record_id=record_id,
                field="canonical_instrument",
            )
        expected_contract = {
            "price_unit": price_unit,
            "contract_multiplier": (
                None
                if multiplier is None
                else canonical_decimal(multiplier, field_name="contract_multiplier")
            ),
            "price_factor": (
                None
                if factor is None
                else canonical_decimal(factor, field_name="price_factor")
            ),
            "state": state,
            "reason_codes": list(reasons),
            "source": source,
        }
        for key, expected_value in expected_contract.items():
            if canonical_contract.get(key) != expected_value:
                _fail(
                    f"canonical valuation contract differs at {key}",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=INSTRUMENT_TABLE,
                    record_id=record_id,
                    field="canonical_instrument",
                )
        if (
            canonical.get("instrument_id") != record_id
            or canonical.get("instrument_type") != instrument_type
            or canonical.get("currency") != currency
        ):
            _fail(
                "canonical instrument identity differs from typed columns",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=INSTRUMENT_TABLE,
                record_id=record_id,
                field="canonical_instrument",
            )
        instruments[record_id] = _Instrument(
            instrument_id=record_id,
            instrument_type=instrument_type,
            currency=currency,
            price_unit=price_unit,
            contract_multiplier=multiplier,
            price_factor=factor,
            valuation_contract_state=state,
        )
    return instruments


def _build_context(dependencies: ManifestDependencies | RowsByTable) -> _BuildContext:
    rows = _rows_by_table(dependencies)
    manifest_id, run_id, portfolio_id = _validate_identity_rows(rows)
    base_currency, effective_as_of, range_start, cutoff = _validate_config(
        rows,
        portfolio_id=portfolio_id,
    )
    return _BuildContext(
        rows=rows,
        manifest_id=manifest_id,
        run_id=run_id,
        portfolio_id=portfolio_id,
        base_currency=base_currency,
        effective_as_of=effective_as_of,
        range_start=range_start,
        knowledge_cutoff_at=cutoff,
        accounts=_validate_accounts(rows),
        instruments=_validate_instruments(rows),
    )

def _build_transaction_replay_context(rows_by_table: RowsByTable) -> _BuildContext:
    """Build the same typed event context without sealed-manifest identity rows."""

    rows = _transaction_replay_rows(rows_by_table)
    config_rows = rows[CONFIG_TABLE]
    if len(config_rows) != 1:
        _fail(
            "transaction replay requires exactly one Portfolio Daily config row",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CONFIG_TABLE,
            record_id="__config__",
        )
    portfolio_id = _text(
        _required(
            config_rows[0],
            "portfolio_id",
            table=CONFIG_TABLE,
            record_id="__config__",
        ),
        table=CONFIG_TABLE,
        record_id="__config__",
        field="portfolio_id",
    )
    base_currency, effective_as_of, range_start, cutoff = _validate_config(
        rows,
        portfolio_id=portfolio_id,
    )
    return _BuildContext(
        rows=rows,
        # Prospective validation is not a publication artifact.  Stable nil
        # identities make that boundary explicit while source lineage remains
        # anchored to the real transaction/corporate-action revisions.
        manifest_id=UUID(int=0),
        run_id=UUID(int=0),
        portfolio_id=portfolio_id,
        base_currency=base_currency,
        effective_as_of=effective_as_of,
        range_start=range_start,
        knowledge_cutoff_at=cutoff,
        accounts=_validate_accounts(rows),
        instruments=_validate_instruments(rows),
    )
