"""Canonicalize reserved cash labels to the system cash target member.

Revision ID: 20260715_0039
Revises: 20260715_0038

Instrument taxonomies represent portfolio cash with the root-level
``cash_bucket::__cash__`` member.  This migration removes obsolete root nodes
whose name or code reserved the Cash label, merges their capital weights into
the canonical member, and rejects any shape that cannot be transformed without
guessing.

Downgrade is intentionally refused.  Recreating deleted taxonomy nodes,
assignments, or split target weights would fabricate business facts, so an
operator must restore the pre-upgrade backup instead.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import math

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0039"
down_revision: str | None = "20260715_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TARGET_MEMBER_CASH = "cash_bucket"
TARGET_MEMBER_NODE = "taxonomy_node"
SYSTEM_CASH_TARGET_MEMBER_ID = "__cash__"


taxonomy = sa.table(
    "taxonomy_record",
    sa.column("taxonomy_id", sa.String()),
    sa.column("primary_assignment_scope", sa.String()),
)
taxonomy_node = sa.table(
    "taxonomy_node_record",
    sa.column("taxonomy_node_id", sa.String()),
    sa.column("taxonomy_id", sa.String()),
    sa.column("parent_taxonomy_node_id", sa.String()),
    sa.column("node_name", sa.String()),
    sa.column("node_code", sa.String()),
)
taxonomy_assignment = sa.table(
    "taxonomy_assignment_record",
    sa.column("assignment_id", sa.String()),
    sa.column("taxonomy_id", sa.String()),
    sa.column("target_scope", sa.String()),
    sa.column("taxonomy_node_id", sa.String()),
)
target_set = sa.table(
    "target_set_record",
    sa.column("target_set_id", sa.String()),
    sa.column("taxonomy_id", sa.String()),
    sa.column("comparator_taxonomy_node_id", sa.String()),
    sa.column("weight_enabled", sa.Boolean()),
)
target_line = sa.table(
    "target_set_line_record",
    sa.column("target_line_id", sa.String()),
    sa.column("target_set_id", sa.String()),
    sa.column("taxonomy_node_id", sa.String()),
    sa.column("target_member_type", sa.String()),
    sa.column("target_member_id", sa.String()),
    sa.column("target_weight", sa.Float()),
    sa.column("target_risk_share", sa.Float()),
    sa.column("notes", sa.String()),
)
research_settings = sa.table(
    "research_settings_record",
    sa.column("portfolio_id", sa.String()),
    sa.column("comparator_taxonomy_node_id", sa.String()),
    sa.column("frozen_taxonomy_node_ids_json", sa.JSON()),
    sa.column("top_sleeve_weight_bounds_json", sa.JSON()),
)


class _TargetLineMerge:
    __slots__ = (
        "keeper_id",
        "redundant_ids",
        "target_weight",
        "notes",
    )

    def __init__(
        self,
        *,
        keeper_id: str,
        redundant_ids: tuple[str, ...],
        target_weight: float,
        notes: str | None,
    ) -> None:
        self.keeper_id = keeper_id
        self.redundant_ids = redundant_ids
        self.target_weight = target_weight
        self.notes = notes


def _lock_related_tables(connection: sa.Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(
        "LOCK TABLE "
        "taxonomy_record, taxonomy_node_record, taxonomy_assignment_record, "
        "target_set_record, target_set_line_record, research_settings_record "
        "IN SHARE ROW EXCLUSIVE MODE"
    )


def _is_reserved_cash_label(node_name: object, node_code: object) -> bool:
    normalized_name = str(node_name or "").strip().lower()
    normalized_code = str(node_code or "").strip().lower()
    return normalized_code == "cash" or normalized_name in {"cash", "现金"}


def _reserved_nodes(connection: sa.Connection) -> list[dict[str, object]]:
    instrument_taxonomy_ids = {
        str(row["taxonomy_id"])
        for row in connection.execute(
            sa.select(taxonomy).where(
                taxonomy.c.primary_assignment_scope == "instrument"
            )
        ).mappings()
    }
    return [
        dict(row)
        for row in connection.execute(sa.select(taxonomy_node)).mappings()
        if str(row["taxonomy_id"]) in instrument_taxonomy_ids
        and _is_reserved_cash_label(row.get("node_name"), row.get("node_code"))
    ]


def _preflight(
    connection: sa.Connection,
    nodes: list[dict[str, object]],
) -> tuple[set[str], dict[str, str]]:
    node_ids = {str(row["taxonomy_node_id"]) for row in nodes}
    taxonomy_by_node_id = {
        str(row["taxonomy_node_id"]): str(row["taxonomy_id"])
        for row in nodes
    }
    if not node_ids:
        return node_ids, taxonomy_by_node_id

    nested_ids = sorted(
        node_id
        for node_id, row in (
            (str(item["taxonomy_node_id"]), item) for item in nodes
        )
        if row.get("parent_taxonomy_node_id") is not None
    )
    if nested_ids:
        raise RuntimeError(
            "Canonical cash migration only accepts root Cash nodes; nested nodes "
            f"require an explicit taxonomy redesign: {', '.join(nested_ids)}."
        )

    child_rows = list(
        connection.execute(
            sa.select(taxonomy_node.c.taxonomy_node_id).where(
                taxonomy_node.c.parent_taxonomy_node_id.in_(sorted(node_ids))
            )
        ).mappings()
    )
    if child_rows:
        child_ids = ", ".join(sorted(str(row["taxonomy_node_id"]) for row in child_rows))
        raise RuntimeError(
            "Canonical cash migration cannot delete Cash nodes with children: "
            f"{child_ids}."
        )

    scoped_target_sets = list(
        connection.execute(
            sa.select(target_set.c.target_set_id).where(
                target_set.c.comparator_taxonomy_node_id.in_(sorted(node_ids))
            )
        ).mappings()
    )
    if scoped_target_sets:
        target_set_ids = ", ".join(
            sorted(str(row["target_set_id"]) for row in scoped_target_sets)
        )
        raise RuntimeError(
            "Canonical cash migration cannot remove a configured comparator scope: "
            f"{target_set_ids}."
        )

    settings_rows = list(connection.execute(sa.select(research_settings)).mappings())
    referenced_settings: set[str] = set()
    for row in settings_rows:
        portfolio_id = str(row["portfolio_id"])
        frozen_ids = row.get("frozen_taxonomy_node_ids_json")
        frozen_node_ids = {
            str(item)
            for item in (frozen_ids if isinstance(frozen_ids, list) else [])
            if str(item)
        }
        bounds = row.get("top_sleeve_weight_bounds_json")
        if bounds is not None and not isinstance(bounds, list):
            raise RuntimeError(
                "Canonical cash migration found malformed top-sleeve weight "
                f"bounds for portfolio {portfolio_id}."
            )
        bound_node_ids: set[str] = set()
        for item in bounds or []:
            if not isinstance(item, Mapping):
                raise RuntimeError(
                    "Canonical cash migration found malformed top-sleeve weight "
                    f"bounds for portfolio {portfolio_id}."
                )
            node_id = str(item.get("taxonomy_node_id") or "").strip()
            if node_id:
                bound_node_ids.add(node_id)
        if (
            str(row.get("comparator_taxonomy_node_id") or "") in node_ids
            or node_ids.intersection(frozen_node_ids)
            or node_ids.intersection(bound_node_ids)
        ):
            referenced_settings.add(portfolio_id)
    if referenced_settings:
        raise RuntimeError(
            "Canonical cash migration found Research settings that reference Cash "
            f"nodes: {', '.join(sorted(referenced_settings))}."
        )

    assignments = list(
        connection.execute(
            sa.select(taxonomy_assignment).where(
                taxonomy_assignment.c.taxonomy_node_id.in_(sorted(node_ids))
            )
        ).mappings()
    )
    invalid_assignments = sorted(
        str(row["assignment_id"])
        for row in assignments
        if str(row.get("target_scope") or "") != TARGET_MEMBER_CASH
        or taxonomy_by_node_id[str(row["taxonomy_node_id"])]
        != str(row["taxonomy_id"])
    )
    if invalid_assignments:
        raise RuntimeError(
            "Canonical cash migration found non-cash or cross-taxonomy assignments: "
            f"{', '.join(invalid_assignments)}."
        )

    return node_ids, taxonomy_by_node_id


def _valid_weight(value: object) -> bool:
    if value is None:
        return False
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(resolved) and resolved >= 0.0


def _build_target_line_merge_plan(
    connection: sa.Connection,
    *,
    node_ids: set[str],
    taxonomy_by_node_id: dict[str, str],
) -> list[_TargetLineMerge]:
    if not node_ids:
        return []

    all_target_sets = {
        str(row["target_set_id"]): dict(row)
        for row in connection.execute(sa.select(target_set)).mappings()
    }
    all_lines = [
        dict(row)
        for row in connection.execute(sa.select(target_line)).mappings()
    ]
    node_lines = [
        row
        for row in all_lines
        if str(row.get("taxonomy_node_id") or "") in node_ids
        or (
            str(row.get("target_member_type") or "") == TARGET_MEMBER_NODE
            and str(row.get("target_member_id") or "") in node_ids
        )
    ]
    invalid_line_ids: list[str] = []
    for row in node_lines:
        node_id = str(row.get("target_member_id") or "")
        target_set_row = all_target_sets.get(str(row["target_set_id"]))
        if (
            str(row.get("target_member_type") or "") != TARGET_MEMBER_NODE
            or node_id not in node_ids
            or str(row.get("taxonomy_node_id") or "") != node_id
            or target_set_row is None
            or str(target_set_row["taxonomy_id"]) != taxonomy_by_node_id[node_id]
            or row.get("target_risk_share") is not None
            or not _valid_weight(row.get("target_weight"))
        ):
            invalid_line_ids.append(str(row["target_line_id"]))
    if invalid_line_ids:
        raise RuntimeError(
            "Canonical cash migration found inconsistent Cash target lines: "
            f"{', '.join(sorted(invalid_line_ids))}."
        )

    node_lines_by_target_set: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in node_lines:
        node_lines_by_target_set[str(row["target_set_id"])].append(row)

    canonical_lines_by_target_set: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in all_lines:
        if (
            str(row.get("target_member_type") or "") == TARGET_MEMBER_CASH
            and str(row.get("target_member_id") or "") == SYSTEM_CASH_TARGET_MEMBER_ID
        ):
            canonical_lines_by_target_set[str(row["target_set_id"])].append(row)

    merge_plan: list[_TargetLineMerge] = []
    for target_set_id, reserved_lines in sorted(node_lines_by_target_set.items()):
        target_set_row = all_target_sets[target_set_id]
        if not bool(target_set_row.get("weight_enabled")):
            line_ids = ", ".join(
                sorted(str(row["target_line_id"]) for row in reserved_lines)
            )
            raise RuntimeError(
                "Canonical cash migration found reserved Cash weights in a "
                f"non-weight-enabled target set {target_set_id}: {line_ids}."
            )

        canonical_lines = canonical_lines_by_target_set.get(target_set_id, [])
        if len(canonical_lines) > 1:
            raise RuntimeError(
                "Canonical cash migration found duplicate system cash target lines in "
                f"{target_set_id}."
            )
        candidate_rows = canonical_lines + reserved_lines
        invalid_canonical_ids = [
            str(row["target_line_id"])
            for row in canonical_lines
            if row.get("taxonomy_node_id") is not None
            or row.get("target_risk_share") is not None
            or not _valid_weight(row.get("target_weight"))
        ]
        if invalid_canonical_ids:
            raise RuntimeError(
                "Canonical cash migration found invalid system cash target lines: "
                f"{', '.join(sorted(invalid_canonical_ids))}."
            )

        notes = {
            str(row["notes"]).strip()
            for row in candidate_rows
            if str(row.get("notes") or "").strip()
        }
        if len(notes) > 1:
            raise RuntimeError(
                "Canonical cash migration cannot merge conflicting target notes in "
                f"{target_set_id}."
            )

        try:
            merged_weight = math.fsum(
                float(row["target_weight"]) for row in candidate_rows
            )
        except (OverflowError, ValueError) as exc:
            raise RuntimeError(
                "Canonical cash migration cannot represent the merged target "
                f"weight in {target_set_id}."
            ) from exc
        if not math.isfinite(merged_weight):
            raise RuntimeError(
                "Canonical cash migration cannot represent the merged target "
                f"weight in {target_set_id}."
            )

        keeper = (
            canonical_lines[0]
            if canonical_lines
            else min(reserved_lines, key=lambda row: str(row["target_line_id"]))
        )
        keeper_id = str(keeper["target_line_id"])
        redundant_ids = tuple(sorted(
            str(row["target_line_id"])
            for row in candidate_rows
            if str(row["target_line_id"]) != keeper_id
        ))
        merge_plan.append(
            _TargetLineMerge(
                keeper_id=keeper_id,
                redundant_ids=redundant_ids,
                target_weight=merged_weight,
                notes=next(iter(notes), None),
            )
        )

    return merge_plan


def _apply_target_line_merge_plan(
    connection: sa.Connection,
    merge_plan: list[_TargetLineMerge],
) -> None:
    for item in merge_plan:
        if item.redundant_ids:
            deleted = connection.execute(
                sa.delete(target_line).where(
                    target_line.c.target_line_id.in_(item.redundant_ids)
                )
            )
            if deleted.rowcount != len(item.redundant_ids):
                raise RuntimeError(
                    "Canonical cash migration target lines changed after preflight."
                )

        updated = connection.execute(
            sa.update(target_line)
            .where(target_line.c.target_line_id == item.keeper_id)
            .values(
                taxonomy_node_id=None,
                target_member_type=TARGET_MEMBER_CASH,
                target_member_id=SYSTEM_CASH_TARGET_MEMBER_ID,
                target_weight=item.target_weight,
                target_risk_share=None,
                notes=item.notes,
            )
        )
        if updated.rowcount != 1:
            raise RuntimeError(
                "Canonical cash migration target lines changed after preflight."
            )


def _upgrade(connection: sa.Connection) -> None:
    _lock_related_tables(connection)
    nodes = _reserved_nodes(connection)
    node_ids, taxonomy_by_node_id = _preflight(connection, nodes)
    if not node_ids:
        return

    merge_plan = _build_target_line_merge_plan(
        connection,
        node_ids=node_ids,
        taxonomy_by_node_id=taxonomy_by_node_id,
    )
    _apply_target_line_merge_plan(connection, merge_plan)
    connection.execute(
        sa.delete(taxonomy_assignment).where(
            taxonomy_assignment.c.taxonomy_node_id.in_(sorted(node_ids))
        )
    )
    deleted_nodes = connection.execute(
        sa.delete(taxonomy_node).where(
            taxonomy_node.c.taxonomy_node_id.in_(sorted(node_ids))
        )
    )
    if deleted_nodes.rowcount != len(node_ids):
        raise RuntimeError(
            "Canonical cash migration taxonomy nodes changed after preflight."
        )


def upgrade() -> None:
    _upgrade(op.get_bind())


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260715_0039 deletes and merges business data and cannot be "
        "downgraded safely. Restore the pre-upgrade database backup instead."
    )
