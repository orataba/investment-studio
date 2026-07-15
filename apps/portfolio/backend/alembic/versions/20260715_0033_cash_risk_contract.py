"""Remove cash from risk-budget targets while preserving capital weights.

Revision ID: 20260715_0033
Revises: 20260715_0032r

The data migration deliberately classifies every legacy line before changing
anything.  A cash-like line is either a direct cash member, a taxonomy node
named/coded as cash, or a node whose active assignment subtree contains only
cash buckets.  Weight-enabled rows with valid weight data are retained with a
NULL risk share; pure risk placeholders are deleted.

Downgrade is best-effort: retained cash-like rows in risk-enabled target sets
receive the legacy 0 risk placeholder, but deleted placeholder-only rows
cannot be reconstructed without inventing target-set membership.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
import math

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0033"
down_revision: str | None = "20260715_0032r"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CHECK_NAME = "ck_target_set_line_cash_risk_null"
TARGET_MEMBER_CASH = "cash_bucket"
TARGET_MEMBER_NODE = "taxonomy_node"


target_set = sa.table(
    "target_set_record",
    sa.column("target_set_id", sa.String()),
    sa.column("taxonomy_id", sa.String()),
    sa.column("weight_enabled", sa.Boolean()),
    sa.column("risk_budget_enabled", sa.Boolean()),
)
target_line = sa.table(
    "target_set_line_record",
    sa.column("target_line_id", sa.String()),
    sa.column("target_set_id", sa.String()),
    sa.column("target_member_type", sa.String()),
    sa.column("target_member_id", sa.String()),
    sa.column("target_weight", sa.Float()),
    sa.column("target_risk_share", sa.Float()),
)
taxonomy_node = sa.table(
    "taxonomy_node_record",
    sa.column("taxonomy_node_id", sa.String()),
    sa.column("taxonomy_id", sa.String()),
    sa.column("parent_taxonomy_node_id", sa.String()),
    sa.column("node_name", sa.String()),
    sa.column("node_code", sa.String()),
    sa.column("status", sa.String()),
)
taxonomy_assignment = sa.table(
    "taxonomy_assignment_record",
    sa.column("taxonomy_id", sa.String()),
    sa.column("taxonomy_node_id", sa.String()),
    sa.column("target_scope", sa.String()),
    sa.column("status", sa.String()),
)


def _is_cash_label(node_name: object, node_code: object) -> bool:
    normalized_name = str(node_name or "").strip().lower()
    normalized_code = str(node_code or "").strip().lower()
    return normalized_code == "cash" or normalized_name in {"cash", "现金"}


def _cash_like_line_ids(connection: sa.Connection) -> tuple[set[str], dict[str, dict[str, object]]]:
    target_sets = {
        str(row["target_set_id"]): dict(row)
        for row in connection.execute(sa.select(target_set)).mappings()
    }
    nodes = [dict(row) for row in connection.execute(sa.select(taxonomy_node)).mappings()]
    node_by_key = {
        (str(row["taxonomy_id"]), str(row["taxonomy_node_id"])): row
        for row in nodes
    }
    active_children: dict[tuple[str, str | None], list[str]] = defaultdict(list)
    for row in nodes:
        if str(row.get("status") or "") != "active":
            continue
        taxonomy_id = str(row["taxonomy_id"])
        parent_id = str(row.get("parent_taxonomy_node_id") or "") or None
        active_children[(taxonomy_id, parent_id)].append(str(row["taxonomy_node_id"]))

    active_assignment_scopes: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in connection.execute(
        sa.select(taxonomy_assignment).where(taxonomy_assignment.c.status == "active")
    ).mappings():
        active_assignment_scopes[
            (str(row["taxonomy_id"]), str(row["taxonomy_node_id"]))
        ].append(str(row["target_scope"]))

    def node_is_cash_like(taxonomy_id: str, node_id: str) -> bool:
        node = node_by_key.get((taxonomy_id, node_id))
        if node is not None and _is_cash_label(node.get("node_name"), node.get("node_code")):
            return True

        pending = [node_id]
        subtree_ids: set[str] = set()
        while pending:
            current_id = pending.pop()
            if current_id in subtree_ids:
                continue
            subtree_ids.add(current_id)
            pending.extend(active_children.get((taxonomy_id, current_id), []))
        assignment_scopes = [
            scope
            for subtree_id in subtree_ids
            for scope in active_assignment_scopes.get((taxonomy_id, subtree_id), [])
        ]
        return bool(assignment_scopes) and all(scope == TARGET_MEMBER_CASH for scope in assignment_scopes)

    cash_like_ids: set[str] = set()
    for row in connection.execute(sa.select(target_line)).mappings():
        target_set_row = target_sets.get(str(row["target_set_id"]))
        if target_set_row is None:
            raise RuntimeError(
                f"Cash-risk migration found orphan target line {row['target_line_id']}."
            )
        member_type = str(row.get("target_member_type") or "")
        is_cash_like = member_type == TARGET_MEMBER_CASH
        if member_type == TARGET_MEMBER_NODE:
            is_cash_like = node_is_cash_like(
                str(target_set_row["taxonomy_id"]),
                str(row.get("target_member_id") or ""),
            )
        if is_cash_like:
            cash_like_ids.add(str(row["target_line_id"]))
    return cash_like_ids, target_sets


def _valid_weight(value: object) -> bool:
    if value is None:
        return False
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(resolved) and resolved >= 0.0


def upgrade() -> None:
    connection = op.get_bind()
    cash_like_ids, target_sets = _cash_like_line_ids(connection)
    if cash_like_ids:
        cash_rows = {
            str(row["target_line_id"]): dict(row)
            for row in connection.execute(
                sa.select(target_line).where(target_line.c.target_line_id.in_(sorted(cash_like_ids)))
            ).mappings()
        }
        preserve_ids: set[str] = set()
        delete_ids: set[str] = set()
        invalid_weight_ids: list[str] = []
        for target_line_id in sorted(cash_like_ids):
            row = cash_rows[target_line_id]
            target_set_row = target_sets[str(row["target_set_id"])]
            if bool(target_set_row.get("weight_enabled")):
                if not _valid_weight(row.get("target_weight")):
                    invalid_weight_ids.append(target_line_id)
                else:
                    preserve_ids.add(target_line_id)
            else:
                delete_ids.add(target_line_id)

        if invalid_weight_ids:
            joined_ids = ", ".join(invalid_weight_ids)
            raise RuntimeError(
                "Cash-risk migration cannot preserve weight-enabled cash-like lines "
                f"without valid non-negative target_weight values: {joined_ids}."
            )

        if preserve_ids:
            connection.execute(
                sa.update(target_line)
                .where(target_line.c.target_line_id.in_(sorted(preserve_ids)))
                .values(target_risk_share=None)
            )
        if delete_ids:
            connection.execute(
                sa.delete(target_line).where(target_line.c.target_line_id.in_(sorted(delete_ids)))
            )

    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.create_check_constraint(
            CHECK_NAME,
            "target_member_type != 'cash_bucket' OR target_risk_share IS NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.drop_constraint(CHECK_NAME, type_="check")

    connection = op.get_bind()
    cash_like_ids, target_sets = _cash_like_line_ids(connection)
    if not cash_like_ids:
        return
    risk_enabled_ids = {
        str(row["target_set_id"])
        for row in target_sets.values()
        if bool(row.get("risk_budget_enabled"))
    }
    if not risk_enabled_ids:
        return
    connection.execute(
        sa.update(target_line)
        .where(target_line.c.target_line_id.in_(sorted(cash_like_ids)))
        .where(target_line.c.target_set_id.in_(sorted(risk_enabled_ids)))
        .values(target_risk_share=0.0)
    )
