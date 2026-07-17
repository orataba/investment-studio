"""Allow auditable window-normalized private-fund return anchors.

Revision ID: 20260716_0013
Revises: 20260715_0012
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "20260716_0013"
down_revision = "20260715_0012"
branch_labels = None
depends_on = None


TABLE = "fund_nav_adjustment_factor"
ANCHOR_INDEX = "uq_fund_nav_adjustment_factor_run_anchor"
CURRENT_FUNCTION = "enforce_fund_nav_current_projection_contract"
WINDOW_EVIDENCE = "window_normalized_anchor"

EVIDENCE_CHECK = (
    "evidence_kind IN ('provider_total_return', 'fund_nav_event', "
    "'zero_cash_anchor', 'window_normalized_anchor')"
)
SOURCE_CHECK = (
    "(factor_kind = 'provider_implied' AND fund_nav_event_id IS NULL "
    "AND fund_nav_reinvestment_evidence_id IS NULL "
    "AND previous_fund_nav_adjustment_factor_id IS NULL "
    "AND evidence_kind = 'provider_total_return' "
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
ANCHOR_PREDICATE = (
    "evidence_kind IN ('zero_cash_anchor', 'window_normalized_anchor')"
)
OLD_CURRENT_ROOT_PATTERN = re.compile(
    r"anchor\.evidence_kind\s*=\s*'zero_cash_anchor'",
    flags=re.IGNORECASE,
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
    contract: str,
) -> str:
    predicates = {
        "evidence": _is_evidence_contract,
        "source": _is_source_contract,
    }
    predicate = predicates[contract]
    matches = [
        str(constraint.get("name") or "")
        for constraint in constraints
        if predicate(constraint)
    ]
    if len(matches) != 1 or not matches[0]:
        raise RuntimeError(
            f"Expected exactly one {TABLE} {contract} contract; found {matches!r}."
        )
    return matches[0]


def _schema_already_supports_window_anchor(connection: sa.Connection) -> bool:
    relevant = [
        constraint
        for constraint in _check_constraints(connection)
        if _is_evidence_contract(constraint) or _is_source_contract(constraint)
    ]
    return len(relevant) == 2 and all(
        WINDOW_EVIDENCE in str(constraint.get("sqltext") or "")
        for constraint in relevant
    )


def _replace_postgres_current_projection_function(
    connection: sa.Connection,
) -> None:
    function_sql = connection.scalar(
        sa.text(
            "SELECT pg_get_functiondef(proc.oid) "
            "FROM pg_proc AS proc "
            "JOIN pg_namespace AS namespace "
            "ON namespace.oid = proc.pronamespace "
            "WHERE proc.proname = :function_name "
            "AND namespace.nspname = current_schema()"
        ),
        {"function_name": CURRENT_FUNCTION},
    )
    if not function_sql:
        raise RuntimeError(f"Missing PostgreSQL function {CURRENT_FUNCTION}.")
    normalized = OLD_CURRENT_ROOT_PATTERN.sub(
        "anchor.evidence_kind IN "
        "('zero_cash_anchor', 'window_normalized_anchor')",
        str(function_sql),
    )
    if normalized != function_sql:
        connection.execute(sa.text(normalized))
    elif WINDOW_EVIDENCE not in str(function_sql):
        raise RuntimeError(
            "Current fund NAV projection function has an unknown root contract."
        )


def _sqlite_trigger_sql(
    connection: sa.Connection,
    table_name: str,
) -> list[tuple[str, str]]:
    return [
        (str(row["name"]), str(row["sql"]))
        for row in connection.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = :table_name "
                "AND sql IS NOT NULL ORDER BY name"
            ),
            {"table_name": table_name},
        ).mappings()
    ]


def _replace_sqlite_current_projection_triggers(
    connection: sa.Connection,
    triggers: list[tuple[str, str]] | None = None,
) -> None:
    captured = triggers or _sqlite_trigger_sql(
        connection, "fund_nav_current_projection"
    )
    for name, _ in captured:
        connection.execute(sa.text(f'DROP TRIGGER IF EXISTS "{name}"'))
    for _, trigger_sql in captured:
        normalized = OLD_CURRENT_ROOT_PATTERN.sub(
            "anchor.evidence_kind IN "
            "('zero_cash_anchor', 'window_normalized_anchor')",
            trigger_sql,
        )
        if (
            "anchor.evidence_kind" in trigger_sql
            and normalized == trigger_sql
            and WINDOW_EVIDENCE not in trigger_sql
        ):
            raise RuntimeError(
                "Current SQLite fund NAV projection trigger has an unknown root contract."
            )
        connection.execute(sa.text(normalized))


def _upgrade_postgres(connection: sa.Connection) -> None:
    if not _schema_already_supports_window_anchor(connection):
        constraints = _check_constraints(connection)
        evidence_name = _constraint_name(constraints, contract="evidence")
        source_name = _constraint_name(constraints, contract="source")
        op.drop_constraint(op.f(evidence_name), TABLE, type_="check")
        op.drop_constraint(op.f(source_name), TABLE, type_="check")
        op.create_check_constraint(op.f(evidence_name), TABLE, EVIDENCE_CHECK)
        op.create_check_constraint(op.f(source_name), TABLE, SOURCE_CHECK)
        op.drop_index(ANCHOR_INDEX, table_name=TABLE)
        op.create_index(
            ANCHOR_INDEX,
            TABLE,
            ["fund_nav_projection_run_id"],
            unique=True,
            postgresql_where=sa.text(ANCHOR_PREDICATE),
        )
    _replace_postgres_current_projection_function(connection)


def _upgrade_sqlite(connection: sa.Connection) -> None:
    if _schema_already_supports_window_anchor(connection):
        _replace_sqlite_current_projection_triggers(connection)
        return

    factor_triggers = _sqlite_trigger_sql(connection, TABLE)
    current_triggers = _sqlite_trigger_sql(
        connection, "fund_nav_current_projection"
    )
    for name, _ in (*factor_triggers, *current_triggers):
        connection.execute(sa.text(f'DROP TRIGGER IF EXISTS "{name}"'))

    constraints = _check_constraints(connection)
    evidence_name = _constraint_name(constraints, contract="evidence")
    source_name = _constraint_name(constraints, contract="source")
    with op.batch_alter_table(TABLE, recreate="always") as batch_op:
        batch_op.drop_constraint(op.f(evidence_name), type_="check")
        batch_op.drop_constraint(op.f(source_name), type_="check")
        batch_op.create_check_constraint(op.f(evidence_name), EVIDENCE_CHECK)
        batch_op.create_check_constraint(op.f(source_name), SOURCE_CHECK)

    op.drop_index(ANCHOR_INDEX, table_name=TABLE)
    op.create_index(
        ANCHOR_INDEX,
        TABLE,
        ["fund_nav_projection_run_id"],
        unique=True,
        sqlite_where=sa.text(ANCHOR_PREDICATE),
    )
    for _, trigger_sql in factor_triggers:
        connection.execute(sa.text(trigger_sql))
    _replace_sqlite_current_projection_triggers(connection, current_triggers)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        _upgrade_postgres(connection)
    elif connection.dialect.name == "sqlite":
        _upgrade_sqlite(connection)
    else:
        raise RuntimeError(
            f"Unsupported instrument-registry dialect {connection.dialect.name!r}."
        )


def downgrade() -> None:
    raise RuntimeError(
        "20260716_0013 is intentionally irreversible: immutable projection "
        "history may contain window-normalized anchors."
    )
