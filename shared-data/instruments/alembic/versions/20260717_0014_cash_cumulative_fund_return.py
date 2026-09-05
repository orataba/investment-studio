"""Allow provider cash-cumulative evidence to continue fund total return.

Revision ID: 20260717_0014
Revises: 20260716_0013
"""

from __future__ import annotations

from collections.abc import Callable

from alembic import op
import sqlalchemy as sa


revision = "20260717_0014"
down_revision = "20260716_0013"
branch_labels = None
depends_on = None


TABLE = "fund_nav_adjustment_factor"
NEW_EVIDENCE = "provider_cash_cumulative"
EVIDENCE_CHECK = (
    "evidence_kind IN ('provider_total_return', 'provider_cash_cumulative', "
    "'fund_nav_event', 'zero_cash_anchor', 'window_normalized_anchor')"
)
SOURCE_CHECK = (
    "(factor_kind = 'provider_implied' AND fund_nav_event_id IS NULL "
    "AND fund_nav_reinvestment_evidence_id IS NULL "
    "AND previous_fund_nav_adjustment_factor_id IS NULL "
    "AND evidence_kind IN "
    "('provider_total_return', 'provider_cash_cumulative') "
    "AND anchor_date = as_of_date) OR "
    "(factor_kind = 'event_derived' AND ("
    "(evidence_kind IN ('zero_cash_anchor', 'window_normalized_anchor') "
    "AND fund_nav_event_id IS NULL "
    "AND CAST(factor_level AS NUMERIC) = 1 "
    "AND fund_nav_reinvestment_evidence_id IS NULL "
    "AND previous_fund_nav_adjustment_factor_id IS NULL "
    "AND anchor_date = as_of_date) OR "
    "(fund_nav_event_id IS NOT NULL "
    "AND previous_fund_nav_adjustment_factor_id IS NOT NULL "
    "AND evidence_kind = 'fund_nav_event')))"
)


def _check_constraints(connection: sa.Connection) -> list[dict[str, object]]:
    return list(sa.inspect(connection).get_check_constraints(TABLE))


def _constraint_sql(constraint: dict[str, object]) -> str:
    return str(constraint.get("sqltext") or "").lower()


def _is_evidence_contract(constraint: dict[str, object]) -> bool:
    sql = _constraint_sql(constraint)
    return (
        "evidence_kind" in sql
        and "provider_total_return" in sql
        and "fund_nav_event" in sql
        and "factor_kind" not in sql
    )


def _is_source_contract(constraint: dict[str, object]) -> bool:
    sql = _constraint_sql(constraint)
    return (
        "factor_kind" in sql
        and "evidence_kind" in sql
        and "previous_fund_nav_adjustment_factor_id" in sql
    )


def _constraint_name(
    constraints: list[dict[str, object]],
    *,
    predicate: Callable[[dict[str, object]], bool],
) -> str:
    matches = [
        str(constraint.get("name") or "")
        for constraint in constraints
        if predicate(constraint)
    ]
    if len(matches) != 1 or not matches[0]:
        raise RuntimeError(
            f"Expected exactly one {TABLE} contract; found {matches!r}."
        )
    return matches[0]


def _already_upgraded(connection: sa.Connection) -> bool:
    relevant = [
        constraint
        for constraint in _check_constraints(connection)
        if _is_evidence_contract(constraint) or _is_source_contract(constraint)
    ]
    return len(relevant) == 2 and all(
        NEW_EVIDENCE in _constraint_sql(constraint)
        for constraint in relevant
    )


def _sqlite_related_trigger_sql(
    connection: sa.Connection,
) -> list[tuple[str, str]]:
    return [
        (str(row["name"]), str(row["sql"]))
        for row in connection.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND sql IS NOT NULL "
                "AND (tbl_name = :table_name "
                "OR lower(sql) LIKE :table_reference) ORDER BY name"
            ),
            {
                "table_name": TABLE,
                "table_reference": f"%{TABLE.lower()}%",
            },
        ).mappings()
    ]


def _replace_constraints(connection: sa.Connection) -> None:
    constraints = _check_constraints(connection)
    evidence_name = _constraint_name(
        constraints,
        predicate=_is_evidence_contract,
    )
    source_name = _constraint_name(
        constraints,
        predicate=_is_source_contract,
    )
    if connection.dialect.name == "postgresql":
        op.drop_constraint(op.f(evidence_name), TABLE, type_="check")
        op.drop_constraint(op.f(source_name), TABLE, type_="check")
        op.create_check_constraint(op.f(evidence_name), TABLE, EVIDENCE_CHECK)
        op.create_check_constraint(op.f(source_name), TABLE, SOURCE_CHECK)
        return

    triggers = _sqlite_related_trigger_sql(connection)
    for name, _ in triggers:
        connection.execute(sa.text(f'DROP TRIGGER IF EXISTS "{name}"'))
    with op.batch_alter_table(TABLE, recreate="always") as batch_op:
        batch_op.drop_constraint(op.f(evidence_name), type_="check")
        batch_op.drop_constraint(op.f(source_name), type_="check")
        batch_op.create_check_constraint(op.f(evidence_name), EVIDENCE_CHECK)
        batch_op.create_check_constraint(op.f(source_name), SOURCE_CHECK)
    for _, trigger_sql in triggers:
        connection.execute(sa.text(trigger_sql))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            f"Unsupported instrument-registry dialect {connection.dialect.name!r}."
        )
    if not _already_upgraded(connection):
        _replace_constraints(connection)


def downgrade() -> None:
    raise RuntimeError(
        "20260717_0014 is intentionally irreversible: immutable projection "
        "history may contain provider cash-cumulative factors."
    )
