"""Persistence boundary for a sealed Portfolio Daily typed input manifest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from portfolio_app.calculations.numeric import (
    CalculationNumericError,
    EXACT_QUANTITY_STORAGE_PRECISION,
    EXACT_QUANTITY_STORAGE_SCALE,
    METHOD_EVIDENCE_STORAGE_PRECISION,
    METHOD_EVIDENCE_STORAGE_SCALE,
    DERIVED_RATE_STORAGE_PRECISION,
    DERIVED_RATE_STORAGE_SCALE,
    SOURCE_PRICE_STORAGE_PRECISION,
    SOURCE_PRICE_STORAGE_SCALE,
    SOURCE_RATE_STORAGE_PRECISION,
    SOURCE_RATE_STORAGE_SCALE,
    exact_decimal_product,
    exact_decimal_subtract,
    method_decimal_divide,
    require_decimal,
    require_exact_numeric_typmod,
    require_method_decimal,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    FX_RATE_MATH_PRECISION,
    FX_RATE_ROUNDING_MODE,
    INPUT_SCHEMA_VERSION,
)
from portfolio_app.calculations.portfolio_daily.db_models import DEPENDENCY_TABLES
from portfolio_app.calculations.portfolio_daily.hashing import (
    canonical_manifest_hash,
)
from portfolio_ops_calculation_core.lifecycle import ManifestSeal
from portfolio_ops_instrument_core.canonical_fx import (
    CanonicalFxResolverError,
    effective_fx_leg_rate,
)


DEPENDENCY_COUNT_KEYS: Final[dict[str, str]] = {
    "portfolio_daily_config_input": "portfolio_config",
    "portfolio_daily_account_input": "account",
    "portfolio_daily_transaction_input": "transaction_revision",
    "portfolio_daily_instrument_input": "instrument_snapshot",
    "portfolio_daily_corp_action_window": "corporate_action_window",
    "portfolio_daily_corp_action_input": "corporate_action_snapshot",
    "portfolio_daily_quote_window": "quote_window",
    "portfolio_daily_quote_candidate": "quote_candidate",
    "portfolio_daily_fx_path": "fx_path",
    "portfolio_daily_fx_leg": "fx_leg",
    "portfolio_daily_prior_publication": "prior_publication",
}

DEPENDENCY_NATURAL_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "portfolio_daily_config_input": ("portfolio_id",),
    "portfolio_daily_account_input": ("account_id",),
    "portfolio_daily_transaction_input": ("transaction_id",),
    "portfolio_daily_instrument_input": ("instrument_id",),
    "portfolio_daily_corp_action_window": ("instrument_id",),
    "portfolio_daily_corp_action_input": ("corporate_action_event_id",),
    "portfolio_daily_quote_window": (
        "instrument_id",
        "quote_role",
        "valuation_date",
    ),
    "portfolio_daily_quote_candidate": (
        "quote_window_id",
        "candidate_rank",
    ),
    "portfolio_daily_fx_path": (
        "valuation_date",
        "from_currency",
        "to_currency",
    ),
    "portfolio_daily_fx_leg": ("fx_path_id", "leg_order"),
    "portfolio_daily_prior_publication": ("publication_id",),
}


class ManifestPersistenceError(RuntimeError):
    pass


_DEPENDENCY_LOGICAL_NUMERIC_DOMAINS: Final[
    Mapping[tuple[str, str], tuple[int, int]]
] = MappingProxyType(
    {
        **{
            ("portfolio_daily_instrument_input", column): (
                EXACT_QUANTITY_STORAGE_PRECISION,
                EXACT_QUANTITY_STORAGE_SCALE,
            )
            for column in ("contract_multiplier", "price_factor")
        },
        **{
            ("portfolio_daily_corp_action_input", column): (
                SOURCE_RATE_STORAGE_PRECISION,
                SOURCE_RATE_STORAGE_SCALE,
            )
            for column in ("new_units", "old_units")
        },
        **{
            ("portfolio_daily_transaction_input", column): (38, 12)
            for column in ("quantity", "price")
        },
        **{
            ("portfolio_daily_transaction_input", column): (38, 8)
            for column in ("gross_amount", "counter_amount", "fees", "taxes")
        },
        ("portfolio_daily_transaction_input", "quoted_fx_rate"): (38, 18),
        **{
            ("portfolio_daily_transaction_input", column): (84, 26)
            for column in (
                "consideration_terms_difference_exact",
                "quoted_terms_difference_exact",
            )
        },
        ("portfolio_daily_transaction_input", "effective_fx_rate_method50"): (
            132,
            100,
        ),
        ("portfolio_daily_fx_path", "resolved_rate"): (
            DERIVED_RATE_STORAGE_PRECISION,
            DERIVED_RATE_STORAGE_SCALE,
        ),
        ("portfolio_daily_fx_path", "rate_derivation_residual_exact"): (
            METHOD_EVIDENCE_STORAGE_PRECISION,
            METHOD_EVIDENCE_STORAGE_SCALE,
        ),
        ("portfolio_daily_fx_leg", "effective_rate"): (
            DERIVED_RATE_STORAGE_PRECISION,
            DERIVED_RATE_STORAGE_SCALE,
        ),
        ("portfolio_daily_fx_leg", "rate_derivation_residual_exact"): (
            METHOD_EVIDENCE_STORAGE_PRECISION,
            METHOD_EVIDENCE_STORAGE_SCALE,
        ),
    }
)

_RAW_CANONICAL_NUMERIC_COLUMNS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("portfolio_daily_quote_candidate", "quote_value"),
        ("portfolio_daily_fx_leg", "quoted_rate"),
    }
)

_ADOPTED_RAW_NUMERIC_DOMAINS: Final[Mapping[tuple[str, str], tuple[int, int]]] = (
    MappingProxyType(
        {
            ("portfolio_daily_quote_candidate", "quote_value"): (
                SOURCE_PRICE_STORAGE_PRECISION,
                SOURCE_PRICE_STORAGE_SCALE,
            ),
            ("portfolio_daily_fx_leg", "quoted_rate"): (
                SOURCE_RATE_STORAGE_PRECISION,
                SOURCE_RATE_STORAGE_SCALE,
            ),
        }
    )
)

_METHOD50_DEPENDENCY_COLUMNS = frozenset(
    {
        ("portfolio_daily_fx_leg", "effective_rate"),
        (
            "portfolio_daily_transaction_input",
            "effective_fx_rate_method50",
        ),
    }
)

_TRANSACTION_NUMERIC_MAX_SCALES: Final[Mapping[str, int]] = MappingProxyType(
    {
        "quantity": 12,
        "price": 12,
        "gross_amount": 8,
        "counter_amount": 8,
        "quoted_fx_rate": 18,
        "fees": 8,
        "taxes": 8,
    }
)


def _require_logical_numeric(
    row: Mapping[str, object],
    *,
    table_name: str,
    column_name: str,
    precision: int,
    scale: int,
    method50: bool = False,
) -> None:
    value = row.get(column_name)
    if value is None:
        raise ManifestPersistenceError(
            f"{table_name}.{column_name} is required at the adopted boundary"
        )
    try:
        if method50:
            require_method_decimal(
                value,  # type: ignore[arg-type]
                field_name=f"{table_name}.{column_name}",
            )
        else:
            require_exact_numeric_typmod(
                value,  # type: ignore[arg-type]
                precision=precision,
                scale=scale,
                field_name=f"{table_name}.{column_name}",
            )
    except CalculationNumericError as exc:
        raise ManifestPersistenceError(str(exc)) from exc


def _validate_adopted_numeric_boundaries(
    table_name: str,
    row: Mapping[str, object],
) -> None:
    """Validate exact Decimal types before applying adopted storage domains."""

    for raw_table, column_name in _RAW_CANONICAL_NUMERIC_COLUMNS:
        if raw_table != table_name or row.get(column_name) is None:
            continue
        try:
            require_decimal(
                row[column_name],
                field_name=f"{table_name}.{column_name}",
            )
        except CalculationNumericError as exc:
            raise ManifestPersistenceError(str(exc)) from exc

    required: set[str] = set()
    if table_name == "portfolio_daily_transaction_input":
        required.update(("gross_amount", "fees", "taxes"))
        if row.get("consideration_evidence_state") == "complete":
            required.add("consideration_terms_difference_exact")
        if row.get("fx_evidence_state") in {"complete", "partial"}:
            required.add("effective_fx_rate_method50")
        if row.get("fx_evidence_state") == "complete":
            required.add("quoted_terms_difference_exact")
    elif (
        table_name == "portfolio_daily_instrument_input"
        and row.get("valuation_contract_state") == "available"
    ):
        required.update(("contract_multiplier", "price_factor"))
    elif table_name == "portfolio_daily_corp_action_input":
        required.update(("new_units", "old_units"))
    elif (
        table_name == "portfolio_daily_fx_path"
        and row.get("resolution_status") == "resolved"
    ):
        required.update(("resolved_rate", "rate_derivation_residual_exact"))
    elif (
        table_name == "portfolio_daily_fx_leg"
        and row.get("leg_resolution_status") == "resolved"
    ):
        required.update(("effective_rate", "rate_derivation_residual_exact"))

    for (domain_table, column_name), (
        precision,
        scale,
    ) in _DEPENDENCY_LOGICAL_NUMERIC_DOMAINS.items():
        if domain_table != table_name:
            continue
        if row.get(column_name) is None and column_name not in required:
            continue
        _require_logical_numeric(
            row,
            table_name=table_name,
            column_name=column_name,
            precision=precision,
            scale=scale,
            method50=(table_name, column_name) in _METHOD50_DEPENDENCY_COLUMNS,
        )

    adopted_raw_column: str | None = None
    if (
        table_name == "portfolio_daily_quote_candidate"
        and row.get("decision") == "adopted"
    ):
        adopted_raw_column = "quote_value"
    elif (
        table_name == "portfolio_daily_fx_leg"
        and row.get("leg_resolution_status") == "resolved"
    ):
        adopted_raw_column = "quoted_rate"
    if adopted_raw_column is not None:
        precision, scale = _ADOPTED_RAW_NUMERIC_DOMAINS[
            (table_name, adopted_raw_column)
        ]
        _require_logical_numeric(
            row,
            table_name=table_name,
            column_name=adopted_raw_column,
            precision=precision,
            scale=scale,
        )


def _validate_fx_leg_derivation(row: Mapping[str, object]) -> None:
    """Replay the versioned inverse operation before a leg reaches PostgreSQL."""

    if row.get("leg_resolution_status") != "resolved":
        return
    if (
        row.get("rate_math_precision") != FX_RATE_MATH_PRECISION
        or row.get("rate_rounding_mode") != FX_RATE_ROUNDING_MODE
    ):
        raise ManifestPersistenceError(
            "portfolio_daily_fx_leg decimal context is not the versioned FX contract"
        )
    inverted = row.get("is_inverted")
    if type(inverted) is not bool:
        raise ManifestPersistenceError(
            "portfolio_daily_fx_leg.is_inverted must be a bool"
        )
    quoted_rate = row.get("quoted_rate")
    effective_rate = row.get("effective_rate")
    residual = row.get("rate_derivation_residual_exact")
    try:
        expected_rate = effective_fx_leg_rate(
            quoted_rate,  # type: ignore[arg-type]
            inverted=inverted,
        )
        expected_residual = (
            exact_decimal_subtract(
                exact_decimal_product(
                    effective_rate,  # type: ignore[arg-type]
                    quoted_rate,  # type: ignore[arg-type]
                ),
                Decimal("1"),
            )
            if inverted
            else Decimal("0")
        )
    except (CalculationNumericError, CanonicalFxResolverError) as exc:
        raise ManifestPersistenceError(
            "portfolio_daily_fx_leg derivation evidence is invalid"
        ) from exc
    if effective_rate != expected_rate:
        raise ManifestPersistenceError(
            "portfolio_daily_fx_leg.effective_rate does not match the versioned "
            "Decimal50/HALF_EVEN FX operation"
        )
    if residual != expected_residual:
        raise ManifestPersistenceError(
            "portfolio_daily_fx_leg.rate_derivation_residual_exact does not close"
        )


def _validate_transaction_precision_and_evidence(
    row: Mapping[str, object],
) -> None:
    tombstone = row.get("is_tombstone") is True
    scale_state = row.get("numeric_scale_state")
    if tombstone:
        if scale_state is not None:
            raise ManifestPersistenceError(
                "tombstone transaction numeric_scale_state must be null"
            )
    elif scale_state not in {"declared", "legacy_inferred"}:
        raise ManifestPersistenceError(
            "live transaction numeric_scale_state is invalid"
        )
    for field_name, max_scale in _TRANSACTION_NUMERIC_MAX_SCALES.items():
        value = row.get(field_name)
        scale_field = f"{field_name}_input_scale"
        input_scale = row.get(scale_field)
        if value is None:
            if input_scale is not None:
                raise ManifestPersistenceError(
                    f"{scale_field} must be null when {field_name} is null"
                )
            continue
        if (
            isinstance(input_scale, bool)
            or not isinstance(input_scale, int)
            or not 0 <= input_scale <= max_scale
        ):
            raise ManifestPersistenceError(
                f"{scale_field} must be between 0 and {max_scale}"
            )
        try:
            require_exact_numeric_typmod(
                value,  # type: ignore[arg-type]
                precision=(38 - max_scale) + input_scale,
                scale=input_scale,
                field_name=f"portfolio_daily_transaction_input.{field_name}",
            )
        except CalculationNumericError as exc:
            raise ManifestPersistenceError(
                f"{field_name} cannot be represented at its declared input scale"
            ) from exc

    basis = row.get("consideration_basis")
    consideration_state = row.get("consideration_evidence_state")
    difference = row.get("consideration_terms_difference_exact")
    if basis == "exact_quantity_price" and (
        consideration_state != "complete" or difference != 0
    ):
        raise ManifestPersistenceError(
            "exact_quantity_price consideration evidence must close at zero"
        )
    if row.get("transaction_type") != "fx_conversion":
        return
    if row.get("fx_evidence_state") == "unavailable":
        return
    gross_amount = row.get("gross_amount")
    counter_amount = row.get("counter_amount")
    effective_rate = row.get("effective_fx_rate_method50")
    try:
        expected_effective_rate = require_method_decimal(
            method_decimal_divide(
                counter_amount,  # type: ignore[arg-type]
                gross_amount,  # type: ignore[arg-type]
            ),
            field_name="effective_fx_rate_method50",
        )
    except CalculationNumericError as exc:
        raise ManifestPersistenceError(
            "FX actual cash amounts cannot produce valid method50 evidence"
        ) from exc
    if effective_rate != expected_effective_rate:
        raise ManifestPersistenceError(
            "effective_fx_rate_method50 does not match counter_amount / gross_amount"
        )
    quoted_rate = row.get("quoted_fx_rate")
    if quoted_rate is None:
        return
    expected_difference = exact_decimal_subtract(
        counter_amount,  # type: ignore[arg-type]
        exact_decimal_product(
            gross_amount,  # type: ignore[arg-type]
            quoted_rate,  # type: ignore[arg-type]
        ),
    )
    if row.get("quoted_terms_difference_exact") != expected_difference:
        raise ManifestPersistenceError(
            "quoted_terms_difference_exact does not match actual and quoted FX terms"
        )


@dataclass(frozen=True, slots=True)
class ManifestDependencies:
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]]

    def __post_init__(self) -> None:
        expected = {table.name for table in DEPENDENCY_TABLES}
        actual = set(self.rows_by_table)
        if actual != expected:
            raise ManifestPersistenceError(
                "manifest dependency table set mismatch: missing="
                f"{sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
            )

    @property
    def counts(self) -> dict[str, int]:
        return {
            DEPENDENCY_COUNT_KEYS[table.name]: len(self.rows_by_table[table.name])
            for table in DEPENDENCY_TABLES
        }


def insert_manifest_dependencies(
    session: Session,
    *,
    manifest_id: UUID,
    run_id: UUID,
    dependencies: ManifestDependencies,
) -> None:
    """Bulk-insert all typed rows in the caller's open capture transaction."""

    for table in DEPENDENCY_TABLES:
        source_rows = dependencies.rows_by_table[table.name]
        if not source_rows:
            continue
        rows: list[dict[str, object]] = []
        for source_row in source_rows:
            row = dict(source_row)
            if row.get("manifest_id", manifest_id) != manifest_id:
                raise ManifestPersistenceError(
                    f"{table.name} row has a foreign manifest_id"
                )
            if row.get("run_id", run_id) != run_id:
                raise ManifestPersistenceError(f"{table.name} row has a foreign run_id")
            row["manifest_id"] = manifest_id
            row["run_id"] = run_id
            _validate_adopted_numeric_boundaries(table.name, row)
            if table.name == "portfolio_daily_transaction_input":
                _validate_transaction_precision_and_evidence(row)
            elif table.name == "portfolio_daily_fx_leg":
                _validate_fx_leg_derivation(row)
            rows.append(row)
        session.execute(insert(table), rows)


def read_manifest_dependencies(
    session: Session,
    *,
    manifest_id: UUID,
) -> ManifestDependencies:
    rows_by_table: dict[str, Sequence[RowMapping]] = {}
    for table in DEPENDENCY_TABLES:
        natural_key = DEPENDENCY_NATURAL_KEYS[table.name]
        statement = (
            select(table)
            .where(table.c.manifest_id == manifest_id)
            .order_by(*(table.c[column] for column in natural_key))
        )
        rows_by_table[table.name] = tuple(session.execute(statement).mappings())
    return ManifestDependencies(rows_by_table)


def build_manifest_seal(
    session: Session,
    *,
    manifest_id: UUID,
) -> ManifestSeal:
    """Lock, reread, and hash dependencies in one REPEATABLE READ UoW."""

    session.execute(
        text("SELECT calculation_registry.assert_manifest_building(:manifest_id)"),
        {"manifest_id": manifest_id},
    )
    dependencies = read_manifest_dependencies(session, manifest_id=manifest_id)
    digest = canonical_manifest_hash(
        {
            table.name: (
                DEPENDENCY_NATURAL_KEYS[table.name],
                dependencies.rows_by_table[table.name],
            )
            for table in DEPENDENCY_TABLES
        },
        input_schema_version=INPUT_SCHEMA_VERSION,
    )
    return ManifestSeal(digest, dependencies.counts)


__all__ = [
    "DEPENDENCY_COUNT_KEYS",
    "DEPENDENCY_NATURAL_KEYS",
    "ManifestDependencies",
    "ManifestPersistenceError",
    "build_manifest_seal",
    "insert_manifest_dependencies",
    "read_manifest_dependencies",
]
