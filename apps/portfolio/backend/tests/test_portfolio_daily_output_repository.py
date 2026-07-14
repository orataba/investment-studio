from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import ARRAY, Boolean, Date, DateTime, Integer, Numeric, String

from portfolio_app.calculations.portfolio_daily.constants import (
    INPUT_SCHEMA_VERSION,
    METHODOLOGY_VERSION,
    OUTPUT_SCHEMA_VERSION,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    OUTPUT_TABLES,
    portfolio_daily_run_output,
    portfolio_daily_snapshot_output,
)
from portfolio_app.calculations.portfolio_daily.hashing import (
    canonical_financial_output_hash,
)
from portfolio_app.calculations.portfolio_daily.output_repository import (
    _LOGICAL_NUMERIC_DOMAINS,
    OUTPUT_NATURAL_KEYS,
    PortfolioDailyFinancialOutputs,
    PortfolioDailyOutputError,
    prepare_portfolio_daily_output_attempt,
)
from portfolio_ops_calculation_core import ActiveJobLease


pytestmark = pytest.mark.no_database


def _generic_value(column) -> object:
    if column.nullable:
        return None
    if isinstance(column.type, ARRAY):
        return []
    if isinstance(column.type, Boolean):
        return False
    if isinstance(column.type, DateTime):
        return datetime(2026, 7, 14, 8, tzinfo=UTC)
    if isinstance(column.type, Date):
        return date(2026, 7, 14)
    if isinstance(column.type, Numeric):
        return Decimal("0")
    if isinstance(column.type, Integer):
        return 0
    if isinstance(column.type, String):
        return "x"
    raise AssertionError(f"unsupported test column {column.name}: {column.type}")


def _source_row(table) -> dict[str, object]:
    row = {
        column.name: _generic_value(column)
        for column in table.c
        if column.name
        not in {
            "run_id",
            "output_fencing_token",
            "worker_id",
            "calculated_at",
            "canonical_output_hash",
        }
    }
    row["portfolio_id"] = "portfolio-exact"
    return row


def _outputs() -> PortfolioDailyFinancialOutputs:
    rows = {table.name: [] for table in OUTPUT_TABLES}
    run = _source_row(portfolio_daily_run_output)
    run.update(
        {
            "range_start": date(2026, 7, 14),
            "range_end": date(2026, 7, 14),
            "methodology_version": METHODOLOGY_VERSION,
            "input_schema_version": INPUT_SCHEMA_VERSION,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "closure_status": "passed",
            "snapshot_count": 1,
            "measured_nav_count": 0,
            "holding_count": 0,
            "balance_count": 0,
            "lot_count": 0,
            "lot_disposition_count": 0,
            "contribution_count": 0,
            "unavailable_component_count": 1,
            "ledger_balance_residual_exact": Decimal("0"),
            "nav_bridge_residual_exact": Decimal("0"),
            "pnl_residual_exact": Decimal("0"),
            "twr_residual_exact": Decimal("0"),
            "lot_residual_exact": Decimal("0"),
            "rounding_adjustment_base": Decimal("0"),
            "coverage_state": "partial",
            "reason_codes": ["component_unavailable"],
        }
    )
    snapshot = _source_row(portfolio_daily_snapshot_output)
    snapshot.update(
        {
            "as_of_date": date(2026, 7, 14),
            "base_currency": "CNY",
            "nav_reason_codes": ["component_unavailable"],
            "book_pnl_reason_codes": ["component_unavailable"],
            "return_reason_codes": ["component_unavailable"],
            "position_attribution_reason_codes": ["component_unavailable"],
            "valuation_reason_codes": ["component_unavailable"],
        }
    )
    rows[portfolio_daily_run_output.name] = [run]
    rows[portfolio_daily_snapshot_output.name] = [snapshot]
    return PortfolioDailyFinancialOutputs(rows)


def _lease(*, token: int, worker: str) -> ActiveJobLease:
    now = datetime(2026, 7, 14, 8, tzinfo=UTC)
    return ActiveJobLease(
        job_id=uuid4(),
        run_id=uuid4(),
        attempt=token,
        fencing_token=token,
        captured_generation=3,
        lease_owner=worker,
        lease_expires_at=now + timedelta(minutes=5),
    )


def test_output_hash_excludes_worker_attempt_identity() -> None:
    first = prepare_portfolio_daily_output_attempt(
        lease=_lease(token=1, worker="worker-a"),
        portfolio_id="portfolio-exact",
        calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
        financial_outputs=_outputs(),
    )
    second = prepare_portfolio_daily_output_attempt(
        lease=_lease(token=9, worker="worker-b"),
        portfolio_id="portfolio-exact",
        calculated_at=datetime(2026, 7, 14, 9, tzinfo=UTC),
        financial_outputs=_outputs(),
    )
    assert first.canonical_output_hash == second.canonical_output_hash
    assert (
        first.rows_by_table[portfolio_daily_run_output.name][0]["canonical_output_hash"]
        == first.canonical_output_hash
    )


def test_output_boundary_rejects_binary_float() -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_snapshot_output.name][0]["opening_nav"] = 1.0
    with pytest.raises(PortfolioDailyOutputError, match="float"):
        prepare_portfolio_daily_output_attempt(
            lease=_lease(token=1, worker="worker-a"),
            portfolio_id="portfolio-exact",
            calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            financial_outputs=PortfolioDailyFinancialOutputs(rows),
        )


@pytest.mark.parametrize("invalid", (1, True, "1"))
def test_every_numeric_column_requires_decimal(invalid: object) -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_snapshot_output.name][0]["opening_nav"] = invalid

    with pytest.raises(
        PortfolioDailyOutputError,
        match="opening_nav Numeric value must be a Decimal",
    ):
        prepare_portfolio_daily_output_attempt(
            lease=_lease(token=1, worker="worker-a"),
            portfolio_id="portfolio-exact",
            calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            financial_outputs=PortfolioDailyFinancialOutputs(rows),
        )


def test_output_boundary_preserves_bounded_method50_numeric() -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    method50 = Decimal("0.033333333333333333333333333333333333333333333333333")
    rows[portfolio_daily_snapshot_output.name][0]["cumulative_twr_method50"] = method50

    prepared = prepare_portfolio_daily_output_attempt(
        lease=_lease(token=1, worker="worker-a"),
        portfolio_id="portfolio-exact",
        calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
        financial_outputs=PortfolioDailyFinancialOutputs(rows),
    )

    assert (
        prepared.rows_by_table[portfolio_daily_snapshot_output.name][0][
            "cumulative_twr_method50"
        ]
        == method50
    )


def test_output_boundary_accepts_exact_logical_domain_edges() -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_snapshot_output.name][0].update(
        {
            "wealth_index_method50": Decimal("1E-100"),
            "wealth_chain_rounding_adjustment_exact": Decimal("1E-200"),
        }
    )

    prepared = prepare_portfolio_daily_output_attempt(
        lease=_lease(token=1, worker="worker-a"),
        portfolio_id="portfolio-exact",
        calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
        financial_outputs=PortfolioDailyFinancialOutputs(rows),
    )

    snapshot = prepared.rows_by_table[portfolio_daily_snapshot_output.name][0]
    assert snapshot["wealth_index_method50"] == Decimal("1E-100")
    assert snapshot["wealth_chain_rounding_adjustment_exact"] == Decimal("1E-200")


def test_every_unbounded_output_numeric_has_an_explicit_semantic_domain() -> None:
    unclassified: list[tuple[str, str]] = []
    for table in OUTPUT_TABLES:
        for column in table.c:
            if not isinstance(column.type, Numeric):
                continue
            if column.type.precision is not None or column.type.scale is not None:
                continue
            key = (table.name, column.name)
            if not column.name.endswith("_method50") and key not in (
                _LOGICAL_NUMERIC_DOMAINS
            ):
                unclassified.append(key)
    assert unclassified == []


@pytest.mark.parametrize("boundary", (Decimal("1E+41"), Decimal("1E-200")))
def test_accounting_evidence_exact_domain_boundaries_are_usable(
    boundary: Decimal,
) -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_snapshot_output.name][0]["reliable_anchor_nav_exact"] = (
        boundary
    )
    prepared = prepare_portfolio_daily_output_attempt(
        lease=_lease(token=1, worker="worker-a"),
        portfolio_id="portfolio-exact",
        calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
        financial_outputs=PortfolioDailyFinancialOutputs(rows),
    )
    assert (
        prepared.rows_by_table[portfolio_daily_snapshot_output.name][0][
            "reliable_anchor_nav_exact"
        ]
        == boundary
    )


@pytest.mark.parametrize(
    "invalid",
    (
        Decimal("1E+42"),
        Decimal("1E-201"),
    ),
)
def test_accounting_evidence_fails_closed_instead_of_rounding(
    invalid: Decimal,
) -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_snapshot_output.name][0]["reliable_anchor_nav_exact"] = invalid

    with pytest.raises(PortfolioDailyOutputError, match="cannot be stored exactly"):
        prepare_portfolio_daily_output_attempt(
            lease=_lease(token=1, worker="worker-a"),
            portfolio_id="portfolio-exact",
            calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            financial_outputs=PortfolioDailyFinancialOutputs(rows),
        )


@pytest.mark.parametrize(
    ("column_name", "invalid"),
    (
        ("wealth_index_method50", Decimal("1E-101")),
        ("wealth_index_method50", Decimal("1E+32")),
        ("wealth_chain_rounding_adjustment_exact", Decimal("1E-201")),
        ("wealth_chain_rounding_adjustment_exact", Decimal("1E+32")),
    ),
)
def test_output_boundary_rejects_method_values_outside_logical_numeric_domain(
    column_name: str,
    invalid: Decimal,
) -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_snapshot_output.name][0][column_name] = invalid

    with pytest.raises(PortfolioDailyOutputError, match="cannot be stored exactly"):
        prepare_portfolio_daily_output_attempt(
            lease=_lease(token=1, worker="worker-a"),
            portfolio_id="portfolio-exact",
            calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            financial_outputs=PortfolioDailyFinancialOutputs(rows),
        )


def test_output_boundary_rejects_pseudo_method_value_over_fifty_digits() -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_snapshot_output.name][0]["wealth_index_method50"] = Decimal(
        "1.12345678901234567890123456789012345678901234567891"
    )

    with pytest.raises(PortfolioDailyOutputError, match="significant method digits"):
        prepare_portfolio_daily_output_attempt(
            lease=_lease(token=1, worker="worker-a"),
            portfolio_id="portfolio-exact",
            calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            financial_outputs=PortfolioDailyFinancialOutputs(rows),
        )


def test_prepared_rows_freeze_nested_arrays_and_rehash_after_readback() -> None:
    outputs = _outputs()
    source_reason_codes = outputs.rows_by_table[portfolio_daily_run_output.name][0][
        "reason_codes"
    ]
    assert isinstance(source_reason_codes, list)
    prepared = prepare_portfolio_daily_output_attempt(
        lease=_lease(token=1, worker="worker-a"),
        portfolio_id="portfolio-exact",
        calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
        financial_outputs=outputs,
    )

    source_reason_codes.append("mutated_after_hash")
    prepared_reason_codes = prepared.rows_by_table[portfolio_daily_run_output.name][0][
        "reason_codes"
    ]
    assert prepared_reason_codes == ("component_unavailable",)

    readback: dict[str, list[dict[str, object]]] = {}
    for table in OUTPUT_TABLES:
        readback[table.name] = []
        for prepared_row in prepared.rows_by_table[table.name]:
            row = dict(prepared_row)
            for column in table.c:
                if isinstance(column.type, ARRAY) and isinstance(
                    row[column.name], tuple
                ):
                    row[column.name] = list(row[column.name])
            readback[table.name].append(row)

    rehashed = canonical_financial_output_hash(
        {
            table.name: (
                OUTPUT_NATURAL_KEYS[table.name],
                readback[table.name],
            )
            for table in OUTPUT_TABLES
        },
        methodology_version=METHODOLOGY_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
    )
    assert rehashed == prepared.canonical_output_hash


def test_passed_run_counts_must_match_typed_rows() -> None:
    outputs = _outputs()
    rows = {
        name: [dict(row) for row in values]
        for name, values in outputs.rows_by_table.items()
    }
    rows[portfolio_daily_run_output.name][0]["snapshot_count"] = 2
    with pytest.raises(PortfolioDailyOutputError, match="snapshot_count"):
        prepare_portfolio_daily_output_attempt(
            lease=_lease(token=1, worker="worker-a"),
            portfolio_id="portfolio-exact",
            calculated_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            financial_outputs=PortfolioDailyFinancialOutputs(rows),
        )
