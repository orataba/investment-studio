"""Add the supported European equity exchanges to Watchlist taxonomy.

Revision ID: 20260822_0043
Revises: 20260818_0042
"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260822_0043"
down_revision = "20260818_0042"
branch_labels = None
depends_on = None


EUROPE_EXCHANGES = (
    ("XLON", "equity-exchange-xlon", "London Stock Exchange"),
    ("XETR", "equity-exchange-xetr", "Xetra"),
    ("XPAR", "equity-exchange-xpar", "Euronext Paris"),
    ("XAMS", "equity-exchange-xams", "Euronext Amsterdam"),
    ("XMIL", "equity-exchange-xmil", "Borsa Italiana"),
    ("XSWX", "equity-exchange-xswx", "SIX Swiss Exchange"),
)


def upgrade() -> None:
    bind = op.get_bind()
    node = sa.table(
        "instrument_taxonomy_node",
        sa.column("node_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("label", sa.String()),
        sa.column("parent_node_id", sa.String()),
        sa.column("level_index", sa.Integer()),
        sa.column("display_order", sa.Integer()),
        sa.column("is_leaf", sa.Boolean()),
        sa.column("path_labels_json", sa.JSON()),
        sa.column("path_node_ids_json", sa.JSON()),
    )
    detail = sa.table(
        "instrument_detail",
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("metadata_json", sa.JSON()),
    )
    assignment = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
        sa.column("assigned_at", sa.DateTime(timezone=True)),
        sa.column("source_record_id", sa.String()),
    )
    history = sa.table(
        "instrument_taxonomy_assignment_history",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
        sa.column("path_labels_json", sa.JSON()),
        sa.column("assigned_at", sa.DateTime(timezone=True)),
        sa.column("source_record_id", sa.String()),
    )

    parent_id = "equity-market-eu"
    bind.execute(
        sa.insert(node).values(
            node_id=parent_id,
            taxonomy_code="instrument_taxonomy",
            instrument_type="equity",
            label="欧洲股市",
            parent_node_id=None,
            level_index=1,
            display_order=4,
            is_leaf=False,
            path_labels_json=["欧洲股市"],
            path_node_ids_json=[parent_id],
        )
    )
    node_by_exchange: dict[str, tuple[str, list[str]]] = {}
    for display_order, (exchange_code, node_id, label) in enumerate(
        EUROPE_EXCHANGES,
        start=1,
    ):
        path_labels = ["欧洲股市", label]
        bind.execute(
            sa.insert(node).values(
                node_id=node_id,
                taxonomy_code="instrument_taxonomy",
                instrument_type="equity",
                label=label,
                parent_node_id=parent_id,
                level_index=2,
                display_order=display_order,
                is_leaf=True,
                path_labels_json=path_labels,
                path_node_ids_json=[parent_id, node_id],
            )
        )
        node_by_exchange[exchange_code] = (node_id, path_labels)

    assigned_at = datetime.now(UTC).replace(microsecond=0)
    for row in bind.execute(
        sa.select(detail.c.instrument_id, detail.c.metadata_json).where(
            detail.c.instrument_type == "equity"
        )
    ).mappings():
        metadata = row["metadata_json"]
        if not isinstance(metadata, dict):
            continue
        exchange_code = str(metadata.get("exchange_code") or "").strip().upper()
        target = node_by_exchange.get(exchange_code)
        if target is None:
            continue
        node_id, path_labels = target
        instrument_id = str(row["instrument_id"])
        current = bind.execute(
            sa.select(assignment.c.node_id).where(
                assignment.c.instrument_id == instrument_id,
                assignment.c.taxonomy_code == "instrument_taxonomy",
            )
        ).scalar_one_or_none()
        values = {
            "node_id": node_id,
            "assigned_at": assigned_at,
            "source_record_id": f"registry_exchange:{exchange_code}",
        }
        if current is None:
            bind.execute(
                sa.insert(assignment).values(
                    instrument_id=instrument_id,
                    taxonomy_code="instrument_taxonomy",
                    **values,
                )
            )
        elif str(current) != node_id:
            bind.execute(
                sa.update(assignment)
                .where(
                    assignment.c.instrument_id == instrument_id,
                    assignment.c.taxonomy_code == "instrument_taxonomy",
                )
                .values(**values)
            )
        else:
            continue
        bind.execute(
            sa.insert(history).values(
                instrument_id=instrument_id,
                taxonomy_code="instrument_taxonomy",
                node_id=node_id,
                path_labels_json=path_labels,
                assigned_at=assigned_at,
                source_record_id=f"registry_exchange:{exchange_code}",
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "European equity taxonomy assignments are production data; restore the "
        "pre-migration database backup instead of deleting them."
    )
