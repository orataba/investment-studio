"""Preserve transaction source precision beside float64 calculation projections.

Revision ID: 20260715_0037
Revises: 20260715_0036

The preflight runs before any DDL. Existing float facts are copied into additive
NUMERIC source columns; the original columns remain the calculation projection,
so the ledger/statistical stack is not converted to arbitrary precision.
"""

from decimal import Decimal, InvalidOperation

from alembic import op
import sqlalchemy as sa


revision = "20260715_0037"
down_revision = "20260715_0036"
branch_labels = None
depends_on = None


_SOURCE_COLUMNS: tuple[tuple[str, sa.Numeric, Decimal], ...] = (
    ("source_quantity", sa.Numeric(28, 12), Decimal("1e16")),
    ("source_price", sa.Numeric(28, 12), Decimal("1e16")),
    ("source_gross_amount", sa.Numeric(28, 8), Decimal("1e20")),
    ("source_counter_amount", sa.Numeric(28, 8), Decimal("1e20")),
    ("source_fx_rate", sa.Numeric(28, 12), Decimal("1e16")),
    ("source_fees", sa.Numeric(28, 8), Decimal("1e20")),
    ("source_taxes", sa.Numeric(28, 8), Decimal("1e20")),
)
_PROJECTION_COLUMNS = (
    "quantity",
    "price",
    "gross_amount",
    "counter_amount",
    "fx_rate",
    "fees",
    "taxes",
)


def _audit_existing_values() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT quantity, price, gross_amount, counter_amount, fx_rate, fees, taxes "
            "FROM transaction_record"
        )
    )
    limits = [item[2] for item in _SOURCE_COLUMNS]
    invalid: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        for column_name, value, limit in zip(
            _PROJECTION_COLUMNS,
            row,
            limits,
            strict=True,
        ):
            if value is None:
                continue
            try:
                resolved = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                invalid.append(f"row {row_number} {column_name}=invalid")
                continue
            if not resolved.is_finite() or abs(resolved) >= limit:
                invalid.append(f"row {row_number} {column_name}={value}")
    if invalid:
        sample = ", ".join(invalid[:5])
        raise RuntimeError(
            "Transaction source-precision preflight failed before schema changes: "
            f"{len(invalid)} value(s) exceed the practical NUMERIC contract; {sample}."
        )


def upgrade() -> None:
    _audit_existing_values()
    for column_name, numeric_type, _limit in _SOURCE_COLUMNS:
        op.add_column(
            "transaction_record",
            sa.Column(column_name, numeric_type, nullable=True),
        )

    assignments = ", ".join(
        f"{source_name} = {projection_name}"
        for (source_name, _numeric_type, _limit), projection_name in zip(
            _SOURCE_COLUMNS,
            _PROJECTION_COLUMNS,
            strict=True,
        )
    )
    op.execute(sa.text(f"UPDATE transaction_record SET {assignments}"))


def downgrade() -> None:
    for column_name, _numeric_type, _limit in reversed(_SOURCE_COLUMNS):
        op.drop_column("transaction_record", column_name)
