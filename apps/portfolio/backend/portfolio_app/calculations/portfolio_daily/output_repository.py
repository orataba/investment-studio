"""Attempt-fenced persistence for exact Portfolio Daily financial outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy import Numeric, insert
from sqlalchemy.orm import Session

from portfolio_app.calculations.numeric import (
    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
    CalculationNumericError,
    EXACT_QUANTITY_STORAGE_PRECISION,
    EXACT_QUANTITY_STORAGE_SCALE,
    METHOD_EVIDENCE_STORAGE_PRECISION,
    METHOD_EVIDENCE_STORAGE_SCALE,
    DERIVED_RATE_STORAGE_PRECISION,
    DERIVED_RATE_STORAGE_SCALE,
    SOURCE_PRICE_STORAGE_PRECISION,
    SOURCE_PRICE_STORAGE_SCALE,
    require_decimal,
    require_exact_numeric_typmod,
    require_method_decimal,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    INPUT_SCHEMA_VERSION,
    METHODOLOGY_VERSION,
    OUTPUT_SCHEMA_VERSION,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    OUTPUT_TABLES,
    portfolio_daily_balance_output,
    portfolio_daily_contribution_output,
    portfolio_daily_holding_output,
    portfolio_daily_lot_disposition_output,
    portfolio_daily_lot_output,
    portfolio_daily_run_output,
    portfolio_daily_snapshot_output,
)
from portfolio_app.calculations.portfolio_daily.hashing import (
    canonical_financial_output_hash,
)
from portfolio_ops_calculation_core import ActiveJobLease


OUTPUT_NATURAL_KEYS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        portfolio_daily_run_output.name: ("portfolio_id",),
        portfolio_daily_snapshot_output.name: ("as_of_date",),
        portfolio_daily_holding_output.name: (
            "as_of_date",
            "account_id",
            "instrument_id",
        ),
        portfolio_daily_balance_output.name: (
            "as_of_date",
            "account_id",
            "component_type",
            "component_key",
            "currency",
        ),
        portfolio_daily_lot_output.name: (
            "as_of_date",
            "account_id",
            "instrument_id",
            "lot_id",
        ),
        portfolio_daily_lot_disposition_output.name: (
            "as_of_date",
            "account_id",
            "instrument_id",
            "lot_id",
            "disposition_transaction_id",
            "match_sequence",
        ),
        portfolio_daily_contribution_output.name: (
            "as_of_date",
            "axis",
            "group_key",
        ),
    }
)

_EXECUTION_COLUMNS = frozenset(
    {"run_id", "output_fencing_token", "worker_id", "calculated_at"}
)

# Physical PostgreSQL columns are intentionally unbounded NUMERIC so a typmod
# cannot round before its CHECK runs.  Keep the same logical domain at the
# application boundary for prepared rows that have not reached PostgreSQL yet.
_LOGICAL_NUMERIC_DOMAINS: Final[Mapping[tuple[str, str], tuple[int, int]]] = (
    MappingProxyType(
        {
            **{
                (portfolio_daily_run_output.name, column): (
                    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
                    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
                )
                for column in (
                    "ledger_balance_residual_exact",
                    "nav_bridge_residual_exact",
                    "pnl_residual_exact",
                    "lot_residual_exact",
                )
            },
            (portfolio_daily_run_output.name, "twr_residual_exact"): (
                METHOD_EVIDENCE_STORAGE_PRECISION,
                METHOD_EVIDENCE_STORAGE_SCALE,
            ),
            **{
                (portfolio_daily_snapshot_output.name, column): (
                    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
                    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
                )
                for column in (
                    "position_attribution_residual_exact",
                    "reliable_anchor_nav_exact",
                )
            },
            (
                portfolio_daily_snapshot_output.name,
                "wealth_chain_rounding_adjustment_exact",
            ): (
                METHOD_EVIDENCE_STORAGE_PRECISION,
                METHOD_EVIDENCE_STORAGE_SCALE,
            ),
            (portfolio_daily_holding_output.name, "adopted_price_exact"): (
                SOURCE_PRICE_STORAGE_PRECISION,
                SOURCE_PRICE_STORAGE_SCALE,
            ),
            **{
                (portfolio_daily_holding_output.name, column): (
                    EXACT_QUANTITY_STORAGE_PRECISION,
                    EXACT_QUANTITY_STORAGE_SCALE,
                )
                for column in (
                    "quantity_exact",
                    "contract_multiplier_exact",
                    "price_factor_exact",
                )
            },
            (portfolio_daily_holding_output.name, "adopted_fx_rate_exact"): (
                DERIVED_RATE_STORAGE_PRECISION,
                DERIVED_RATE_STORAGE_SCALE,
            ),
            **{
                (portfolio_daily_holding_output.name, column): (
                    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
                    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
                )
                for column in (
                    "market_value_local_exact",
                    "market_value_base_exact",
                    "position_attribution_residual_exact",
                )
            },
            (portfolio_daily_balance_output.name, "adopted_fx_rate_exact"): (
                DERIVED_RATE_STORAGE_PRECISION,
                DERIVED_RATE_STORAGE_SCALE,
            ),
            (portfolio_daily_balance_output.name, "base_amount_exact"): (
                ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
                ACCOUNTING_EVIDENCE_STORAGE_SCALE,
            ),
            (portfolio_daily_lot_output.name, "open_quantity_exact"): (
                EXACT_QUANTITY_STORAGE_PRECISION,
                EXACT_QUANTITY_STORAGE_SCALE,
            ),
            (portfolio_daily_lot_output.name, "acquisition_fx_rate_exact"): (
                DERIVED_RATE_STORAGE_PRECISION,
                DERIVED_RATE_STORAGE_SCALE,
            ),
            **{
                (portfolio_daily_lot_output.name, column): (
                    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
                    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
                )
                for column in (
                    "cost_basis_local_exact",
                    "unit_cost_local_rounding_residual_exact",
                    "cost_basis_base_exact",
                    "unit_cost_base_rounding_residual_exact",
                )
            },
            (
                portfolio_daily_lot_disposition_output.name,
                "disposed_quantity_exact",
            ): (
                EXACT_QUANTITY_STORAGE_PRECISION,
                EXACT_QUANTITY_STORAGE_SCALE,
            ),
            (
                portfolio_daily_lot_disposition_output.name,
                "disposition_fx_rate_exact",
            ): (
                DERIVED_RATE_STORAGE_PRECISION,
                DERIVED_RATE_STORAGE_SCALE,
            ),
            **{
                (portfolio_daily_lot_disposition_output.name, column): (
                    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
                    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
                )
                for column in (
                    "proceeds_local_exact",
                    "allocated_cost_local_exact",
                    "realized_pnl_local_exact",
                    "proceeds_base_exact",
                    "allocated_cost_base_exact",
                    "realized_pnl_base_exact",
                )
            },
            **{
                (portfolio_daily_contribution_output.name, column): (
                    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
                    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
                )
                for column in (
                    "opening_nav_exact",
                    "closing_nav_exact",
                    "external_flow_in_exact",
                    "external_flow_out_exact",
                    "internal_flow_in_exact",
                    "internal_flow_out_exact",
                    "economic_pnl_exact",
                    "closure_residual_exact",
                )
            },
            (
                portfolio_daily_contribution_output.name,
                "contribution_division_adjustment_exact",
            ): (
                METHOD_EVIDENCE_STORAGE_PRECISION,
                METHOD_EVIDENCE_STORAGE_SCALE,
            ),
        }
    )
)


class PortfolioDailyOutputError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioDailyFinancialOutputs:
    """Financial rows before worker-attempt identity is attached."""

    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]]

    def __post_init__(self) -> None:
        expected = {table.name for table in OUTPUT_TABLES}
        actual = set(self.rows_by_table)
        if actual != expected:
            raise PortfolioDailyOutputError(
                "Portfolio Daily output table set mismatch: missing="
                f"{sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
            )


@dataclass(frozen=True, slots=True)
class PreparedPortfolioDailyOutputAttempt:
    run_id: UUID
    output_fencing_token: int
    canonical_output_hash: str
    rows_by_table: Mapping[str, tuple[Mapping[str, object], ...]]


def _assert_exact_value(value: object, *, path: str) -> None:
    if isinstance(value, float):
        raise PortfolioDailyOutputError(f"{path} must not contain a float")
    if isinstance(value, Decimal):
        try:
            require_decimal(value, field_name=path)
        except ValueError as exc:
            raise PortfolioDailyOutputError(str(exc)) from exc
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _assert_exact_value(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_exact_value(item, path=f"{path}[{index}]")


def _canonical_reason_codes(row: Mapping[str, object], *, table_name: str) -> None:
    for column_name, value in row.items():
        if column_name != "reason_codes" and not column_name.endswith("_reason_codes"):
            continue
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(reason, str) or not reason or reason != reason.strip()
            for reason in value
        ):
            raise PortfolioDailyOutputError(
                f"{table_name}.{column_name} must be canonical reason codes"
            )
        if list(value) != sorted(set(value)):
            raise PortfolioDailyOutputError(
                f"{table_name}.{column_name} must be sorted and unique"
            )


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _validate_source_row(table, source: Mapping[str, object]) -> dict[str, object]:
    row = dict(source)
    forbidden = _EXECUTION_COLUMNS.intersection(row)
    if forbidden:
        raise PortfolioDailyOutputError(
            f"{table.name} source row contains execution identity: {sorted(forbidden)}"
        )
    expected = {column.name for column in table.c} - _EXECUTION_COLUMNS
    if table is portfolio_daily_run_output:
        expected.remove("canonical_output_hash")
        if "canonical_output_hash" in row:
            raise PortfolioDailyOutputError(
                "run output hash is assigned only by the output repository"
            )
    actual = set(row)
    if actual != expected:
        raise PortfolioDailyOutputError(
            f"{table.name} columns mismatch: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    for key, value in row.items():
        _assert_exact_value(value, path=f"{table.name}.{key}")
        column_type = table.c[key].type
        if value is not None and isinstance(column_type, Numeric):
            if type(value) is not Decimal:
                raise PortfolioDailyOutputError(
                    f"{table.name}.{key} Numeric value must be a Decimal"
                )
            try:
                if key.endswith("_method50"):
                    require_method_decimal(
                        value,
                        field_name=f"{table.name}.{key}",
                    )
                elif (table.name, key) in _LOGICAL_NUMERIC_DOMAINS:
                    precision, scale = _LOGICAL_NUMERIC_DOMAINS[(table.name, key)]
                    require_exact_numeric_typmod(
                        value,
                        precision=precision,
                        scale=scale,
                        field_name=f"{table.name}.{key}",
                    )
                elif (
                    column_type.precision is not None and column_type.scale is not None
                ):
                    require_exact_numeric_typmod(
                        value,
                        precision=column_type.precision,
                        scale=column_type.scale,
                        field_name=f"{table.name}.{key}",
                    )
            except CalculationNumericError as exc:
                raise PortfolioDailyOutputError(str(exc)) from exc
    _canonical_reason_codes(row, table_name=table.name)
    return {key: _freeze_value(value) for key, value in row.items()}


def _validate_run_summary(
    rows: Mapping[str, list[dict[str, object]]],
) -> None:
    run_rows = rows[portfolio_daily_run_output.name]
    if len(run_rows) != 1:
        raise PortfolioDailyOutputError("exactly one run output row is required")
    run = run_rows[0]
    if (
        run["methodology_version"] != METHODOLOGY_VERSION
        or run["input_schema_version"] != INPUT_SCHEMA_VERSION
        or run["output_schema_version"] != OUTPUT_SCHEMA_VERSION
        or run["closure_status"] != "passed"
    ):
        raise PortfolioDailyOutputError("run output version/closure contract failed")
    count_contract = {
        "snapshot_count": len(rows[portfolio_daily_snapshot_output.name]),
        "holding_count": len(rows[portfolio_daily_holding_output.name]),
        "balance_count": len(rows[portfolio_daily_balance_output.name]),
        "lot_count": len(rows[portfolio_daily_lot_output.name]),
        "lot_disposition_count": len(rows[portfolio_daily_lot_disposition_output.name]),
        "contribution_count": len(rows[portfolio_daily_contribution_output.name]),
    }
    for field_name, actual_count in count_contract.items():
        if run[field_name] != actual_count:
            raise PortfolioDailyOutputError(
                f"{field_name} does not match typed output rows"
            )
    if count_contract["snapshot_count"] <= 0:
        raise PortfolioDailyOutputError("a passed run requires at least one snapshot")
    for field_name in (
        "ledger_balance_residual_exact",
        "nav_bridge_residual_exact",
        "pnl_residual_exact",
        "twr_residual_exact",
        "lot_residual_exact",
    ):
        if run[field_name] != Decimal("0"):
            raise PortfolioDailyOutputError(
                f"passed run requires exact zero {field_name}"
            )


def prepare_portfolio_daily_output_attempt(
    *,
    lease: ActiveJobLease,
    portfolio_id: str,
    calculated_at: datetime,
    financial_outputs: PortfolioDailyFinancialOutputs,
) -> PreparedPortfolioDailyOutputAttempt:
    if calculated_at.tzinfo is None or calculated_at.utcoffset() is None:
        raise PortfolioDailyOutputError("calculated_at must be timezone-aware")
    normalized: dict[str, list[dict[str, object]]] = {}
    for table in OUTPUT_TABLES:
        normalized[table.name] = [
            _validate_source_row(table, source)
            for source in financial_outputs.rows_by_table[table.name]
        ]
        if any(
            row.get("portfolio_id") != portfolio_id for row in normalized[table.name]
        ):
            raise PortfolioDailyOutputError(
                f"{table.name} contains a different portfolio_id"
            )
        natural_key = OUTPUT_NATURAL_KEYS[table.name]
        keys = [
            tuple(row[column] for column in natural_key)
            for row in normalized[table.name]
        ]
        if len(set(keys)) != len(keys):
            raise PortfolioDailyOutputError(
                f"{table.name} contains duplicate natural keys"
            )
    _validate_run_summary(normalized)

    rows_with_identity: dict[str, list[dict[str, object]]] = {}
    for table in OUTPUT_TABLES:
        rows_with_identity[table.name] = []
        for source in normalized[table.name]:
            row = dict(source)
            row.update(
                {
                    "run_id": lease.run_id,
                    "output_fencing_token": lease.fencing_token,
                    "worker_id": lease.lease_owner,
                    "calculated_at": calculated_at,
                }
            )
            rows_with_identity[table.name].append(row)

    digest = canonical_financial_output_hash(
        {
            table.name: (
                OUTPUT_NATURAL_KEYS[table.name],
                rows_with_identity[table.name],
            )
            for table in OUTPUT_TABLES
        },
        methodology_version=METHODOLOGY_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
    )
    rows_with_identity[portfolio_daily_run_output.name][0]["canonical_output_hash"] = (
        digest
    )
    return PreparedPortfolioDailyOutputAttempt(
        run_id=lease.run_id,
        output_fencing_token=lease.fencing_token,
        canonical_output_hash=digest,
        rows_by_table=MappingProxyType(
            {
                table.name: tuple(
                    MappingProxyType(
                        {key: _freeze_value(value) for key, value in row.items()}
                    )
                    for row in rows_with_identity[table.name]
                )
                for table in OUTPUT_TABLES
            }
        ),
    )


def insert_portfolio_daily_output_attempt(
    session: Session,
    *,
    prepared: PreparedPortfolioDailyOutputAttempt,
) -> None:
    """Insert every attempt row without committing the caller's transaction."""

    for table in OUTPUT_TABLES:
        rows = prepared.rows_by_table[table.name]
        if rows:
            session.execute(insert(table), [dict(row) for row in rows])


__all__ = [
    "OUTPUT_NATURAL_KEYS",
    "PortfolioDailyFinancialOutputs",
    "PortfolioDailyOutputError",
    "PreparedPortfolioDailyOutputAttempt",
    "insert_portfolio_daily_output_attempt",
    "prepare_portfolio_daily_output_attempt",
]
