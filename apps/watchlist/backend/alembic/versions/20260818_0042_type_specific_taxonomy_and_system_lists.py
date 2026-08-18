"""Split fund types, make taxonomy type-specific, and replace All Covered.

Revision ID: 20260818_0042
Revises: 20260813_0041
"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260818_0042"
down_revision = "20260813_0041"
branch_labels = None
depends_on = None


SYSTEM_WATCHLISTS = (
    ("index", "Index", "All active indexes in the shared Registry.", "index"),
    (
        "all-public-funds",
        "All 公募",
        "All active public funds in the shared Registry.",
        "public_fund",
    ),
    (
        "all-private-funds",
        "All 私募",
        "All active private funds in the shared Registry.",
        "private_fund",
    ),
)
CLASSIFICATION_COLUMNS = (
    ("instrument_name", 1, 320),
    ("instrument_type", 2, 130),
    ("attr.instrument_taxonomy_path", 3, 280),
    ("attr.instrument_taxonomy_leaf", 4, 180),
    ("latest_quote", 5, 130),
    ("latest_quote_date", 6, 140),
    ("data_freshness_status", 7, 140),
    ("attr.coverage_status", 8, 110),
)
ETF_ASSIGNMENT_MAP = {
    "fund-public-equity-indexed": "etf-equity-index",
    "fund-public-commodity-precious-metals": "etf-commodity-precious-metals",
    "fund-public-qdii-equity": "etf-equity-cross-border",
    "fund-public-bond-convertible": "etf-fixed-income-convertible",
}
EQUITY_NODE_BY_EXCHANGE = {
    "XNAS": "equity-exchange-xnas",
    "XNYS": "equity-exchange-xnys",
    "XASE": "equity-exchange-xase",
    "XHKG": "equity-exchange-xhkg",
    "XSHG": "equity-exchange-xshg",
    "XSHE": "equity-exchange-xshe",
}


def _new_taxonomy_nodes() -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []

    def add(
        node_id: str,
        label: str,
        instrument_type: str,
        *,
        parent_node_id: str | None = None,
        display_order: int,
        path_labels: list[str] | None = None,
        path_node_ids: list[str] | None = None,
        is_leaf: bool = True,
    ) -> None:
        parent_path_labels = path_labels or []
        parent_path_node_ids = path_node_ids or []
        nodes.append(
            {
                "node_id": node_id,
                "taxonomy_code": "instrument_taxonomy",
                "instrument_type": instrument_type,
                "label": label,
                "parent_node_id": parent_node_id,
                "level_index": len(parent_path_labels) + 1,
                "display_order": display_order,
                "is_leaf": is_leaf,
                "path_labels_json": [*parent_path_labels, label],
                "path_node_ids_json": [*parent_path_node_ids, node_id],
            }
        )

    etf_branches = (
        (
            "etf-equity",
            "权益",
            ("宽基", "行业主题", "策略 / Smart Beta", "跨境"),
            (
                "etf-equity-index",
                "etf-equity-sector-theme",
                "etf-equity-strategy",
                "etf-equity-cross-border",
            ),
        ),
        (
            "etf-fixed-income",
            "固定收益",
            ("利率债", "信用债", "可转债", "现金管理"),
            (
                "etf-fixed-income-government",
                "etf-fixed-income-credit",
                "etf-fixed-income-convertible",
                "etf-fixed-income-cash",
            ),
        ),
        (
            "etf-commodity",
            "商品",
            ("贵金属", "其他商品"),
            ("etf-commodity-precious-metals", "etf-commodity-other"),
        ),
    )
    for order, (parent_id, parent_label, child_labels, child_ids) in enumerate(
        etf_branches,
        start=1,
    ):
        add(parent_id, parent_label, "etf", display_order=order, is_leaf=False)
        for child_order, (child_id, child_label) in enumerate(
            zip(child_ids, child_labels, strict=True),
            start=1,
        ):
            add(
                child_id,
                child_label,
                "etf",
                parent_node_id=parent_id,
                display_order=child_order,
                path_labels=[parent_label],
                path_node_ids=[parent_id],
            )
    add("etf-multi-asset", "多资产", "etf", display_order=4)
    add("etf-other", "其他", "etf", display_order=5)

    equity_branches = (
        (
            "equity-market-us",
            "美股",
            (("equity-exchange-xnas", "NASDAQ"), ("equity-exchange-xnys", "NYSE"), ("equity-exchange-xase", "NYSE American")),
        ),
        ("equity-market-hk", "港股", (("equity-exchange-xhkg", "HKEX"),)),
        (
            "equity-market-cn-a",
            "A股",
            (("equity-exchange-xshg", "上交所"), ("equity-exchange-xshe", "深交所")),
        ),
    )
    for order, (parent_id, parent_label, children) in enumerate(equity_branches, start=1):
        add(parent_id, parent_label, "equity", display_order=order, is_leaf=False)
        for child_order, (child_id, child_label) in enumerate(children, start=1):
            add(
                child_id,
                child_label,
                "equity",
                parent_node_id=parent_id,
                display_order=child_order,
                path_labels=[parent_label],
                path_node_ids=[parent_id],
            )

    for order, (node_id, label) in enumerate(
        (
            ("index-broad-market", "宽基"),
            ("index-sector-theme", "行业主题"),
            ("index-strategy", "策略"),
            ("index-fixed-income", "固定收益"),
            ("index-commodity", "商品"),
            ("index-multi-asset", "多资产"),
            ("index-other", "其他"),
        ),
        start=1,
    ):
        add(node_id, label, "index", display_order=order)
    return nodes


def _expand_fund_type(value: object) -> object:
    if isinstance(value, list):
        rewritten: list[object] = []
        for item in value:
            if item == "fund":
                rewritten.extend(("public_fund", "private_fund"))
            else:
                rewritten.append(_expand_fund_type(item))
        return rewritten
    if isinstance(value, dict):
        rewritten = {str(key): _expand_fund_type(item) for key, item in value.items()}
        if rewritten.get("field") == "instrument_type" and rewritten.get("value") == "fund":
            rewritten["operator"] = "in"
            rewritten["value"] = ["public_fund", "private_fund"]
        return rewritten
    return value


def _registry_identity_map(bind: sa.Connection) -> dict[str, tuple[str, str | None]]:
    if bind.dialect.name != "postgresql":
        return {}
    return {
        str(row.instrument_id): (
            str(row.instrument_type),
            str(row.exchange_code) if row.exchange_code else None,
        )
        for row in bind.execute(
            sa.text(
                "SELECT instrument_id, instrument_type, exchange_code "
                "FROM instrument_registry.instrument"
            )
        )
    }


def _fallback_fund_type(
    detail_row: dict[str, object],
    assignment_node_id: str | None,
) -> str | None:
    if assignment_node_id and assignment_node_id.startswith("fund-public"):
        return "public_fund"
    if assignment_node_id and assignment_node_id.startswith("fund-private"):
        return "private_fund"
    identifier = str(detail_row.get("primary_identifier_value") or "").strip().upper()
    name = str(detail_row.get("instrument_name") or "")
    if identifier.endswith(".OF") or "公募" in name:
        return "public_fund"
    if "私募" in name:
        return "private_fund"
    return None


def _upsert_assignment(
    bind: sa.Connection,
    assignment: sa.TableClause,
    history: sa.TableClause,
    *,
    instrument_id: str,
    node_id: str,
    path_labels: list[str],
    source_record_id: str,
    assigned_at: datetime,
) -> None:
    exists = bind.execute(
        sa.select(assignment.c.instrument_id).where(
            assignment.c.instrument_id == instrument_id,
            assignment.c.taxonomy_code == "instrument_taxonomy",
        )
    ).first()
    values = {
        "node_id": node_id,
        "assigned_at": assigned_at,
        "source_record_id": source_record_id,
    }
    if exists is None:
        bind.execute(
            sa.insert(assignment).values(
                instrument_id=instrument_id,
                taxonomy_code="instrument_taxonomy",
                **values,
            )
        )
    else:
        bind.execute(
            sa.update(assignment)
            .where(
                assignment.c.instrument_id == instrument_id,
                assignment.c.taxonomy_code == "instrument_taxonomy",
            )
            .values(**values)
        )
    bind.execute(
        sa.insert(history).values(
            instrument_id=instrument_id,
            taxonomy_code="instrument_taxonomy",
            node_id=node_id,
            path_labels_json=path_labels,
            assigned_at=assigned_at,
            source_record_id=source_record_id,
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    now = datetime.now(UTC).replace(microsecond=0)

    detail = sa.table(
        "instrument_detail",
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("detail_view_type", sa.String()),
        sa.column("instrument_name", sa.String()),
        sa.column("primary_identifier_value", sa.String()),
        sa.column("metadata_json", sa.JSON()),
    )
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
    watchlist_row = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
    )
    summary = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )

    registry_map = _registry_identity_map(bind)
    assignment_by_instrument = {
        str(row.instrument_id): str(row.node_id) if row.node_id else None
        for row in bind.execute(
            sa.select(assignment.c.instrument_id, assignment.c.node_id).where(
                assignment.c.taxonomy_code == "instrument_taxonomy"
            )
        )
    }
    unresolved_funds: list[str] = []
    local_types: dict[str, str] = {}
    equity_exchanges: dict[str, str] = {}
    for row in bind.execute(sa.select(detail)).mappings():
        instrument_id = str(row["instrument_id"])
        current_type = str(row["instrument_type"] or "").strip().lower()
        registry_identity = registry_map.get(instrument_id)
        target_type = registry_identity[0] if registry_identity else current_type
        if target_type == "fund":
            target_type = _fallback_fund_type(
                dict(row),
                assignment_by_instrument.get(instrument_id),
            ) or ""
        if current_type == "fund" and target_type not in {"public_fund", "private_fund"}:
            unresolved_funds.append(instrument_id)
            continue
        if target_type:
            local_types[instrument_id] = target_type
            metadata = dict(row["metadata_json"] or {})
            if registry_identity and registry_identity[1]:
                metadata["exchange_code"] = registry_identity[1]
                equity_exchanges[instrument_id] = registry_identity[1]
            bind.execute(
                sa.update(detail)
                .where(detail.c.instrument_id == instrument_id)
                .values(
                    instrument_type=target_type,
                    detail_view_type=(
                        target_type
                        if target_type in {"public_fund", "private_fund", "etf", "equity", "index"}
                        else row["detail_view_type"]
                    ),
                    metadata_json=metadata,
                )
            )
            bind.execute(
                sa.update(watchlist_row)
                .where(watchlist_row.c.instrument_id == instrument_id)
                .values(instrument_type=target_type)
            )
    if unresolved_funds:
        raise RuntimeError(
            "Watchlist fund type migration requires Registry identity or a concrete "
            "Watchlist classification: " + ", ".join(sorted(unresolved_funds))
        )

    for row in bind.execute(sa.select(summary)).mappings():
        instrument_type = local_types.get(str(row["instrument_id"]))
        payload = row["payload_json"]
        if not instrument_type or not isinstance(payload, dict):
            continue
        rewritten = dict(payload)
        rewritten["instrument_type"] = instrument_type
        bind.execute(
            sa.update(summary)
            .where(summary.c.instrument_id == row["instrument_id"])
            .values(payload_json=rewritten)
        )

    for table_name, key_columns, column_name in (
        ("field_registry", ("field_key",), "instrument_scope_json"),
        ("instrument_attribute_definition", ("attribute_key",), "instrument_scope_json"),
        ("instrument_attribute_definition", ("attribute_key",), "applicability_json"),
        ("watchlist_view", ("watchlist_view_id",), "default_filters_json"),
        ("watchlist_view", ("watchlist_view_id",), "default_advanced_filter_json"),
    ):
        table = sa.table(
            table_name,
            *(sa.column(key, sa.String()) for key in key_columns),
            sa.column(column_name, sa.JSON()),
        )
        for row in bind.execute(sa.select(table)).mappings():
            current = row[column_name]
            rewritten = _expand_fund_type(current)
            if rewritten == current:
                continue
            predicate = sa.and_(
                *(table.c[key] == row[key] for key in key_columns)
            )
            bind.execute(
                sa.update(table).where(predicate).values({column_name: rewritten})
            )

    root_assignments = bind.execute(
        sa.select(assignment.c.instrument_id).where(
            assignment.c.node_id.in_(("fund-public", "fund-private"))
        )
    ).scalars().all()
    if root_assignments:
        raise RuntimeError(
            "Public/private type roots were used as taxonomy leaves: "
            + ", ".join(str(value) for value in root_assignments)
        )

    for root_id, fund_type in (
        ("fund-public", "public_fund"),
        ("fund-private", "private_fund"),
    ):
        rows = list(
            bind.execute(
                sa.select(node).where(
                    node.c.node_id != root_id,
                    node.c.path_node_ids_json.is_not(None),
                )
            ).mappings()
        )
        for row in rows:
            path_ids = list(row["path_node_ids_json"] or [])
            if not path_ids or path_ids[0] != root_id:
                continue
            path_labels = list(row["path_labels_json"] or [])
            bind.execute(
                sa.update(node)
                .where(node.c.node_id == row["node_id"])
                .values(
                    instrument_type=fund_type,
                    parent_node_id=(
                        None if row["parent_node_id"] == root_id else row["parent_node_id"]
                    ),
                    level_index=int(row["level_index"]) - 1,
                    path_labels_json=path_labels[1:],
                    path_node_ids_json=path_ids[1:],
                )
            )
        for row in bind.execute(
            sa.select(history.c.instrument_id, history.c.assigned_at, history.c.path_labels_json).where(
                history.c.taxonomy_code == "instrument_taxonomy"
            )
        ).mappings():
            labels = list(row["path_labels_json"] or [])
            expected_label = "公募" if root_id == "fund-public" else "私募"
            if labels and labels[0] == expected_label:
                bind.execute(
                    sa.update(history)
                    .where(
                        history.c.instrument_id == row["instrument_id"],
                        history.c.taxonomy_code == "instrument_taxonomy",
                        history.c.assigned_at == row["assigned_at"],
                    )
                    .values(path_labels_json=labels[1:])
                )

    existing_node_ids = set(bind.execute(sa.select(node.c.node_id)).scalars())
    new_nodes = _new_taxonomy_nodes()
    for node_record in new_nodes:
        if node_record["node_id"] not in existing_node_ids:
            bind.execute(sa.insert(node).values(**node_record))

    new_node_by_id = {str(item["node_id"]): item for item in new_nodes}
    for row in bind.execute(
        sa.select(assignment.c.instrument_id, assignment.c.node_id)
        .select_from(assignment.join(detail, assignment.c.instrument_id == detail.c.instrument_id))
        .where(detail.c.instrument_type == "etf")
    ).mappings():
        old_node_id = str(row["node_id"] or "")
        target_node_id = ETF_ASSIGNMENT_MAP.get(old_node_id)
        if target_node_id is None:
            raise RuntimeError(
                f"ETF taxonomy assignment has no explicit mapping: {row['instrument_id']}:{old_node_id}"
            )
        target = new_node_by_id[target_node_id]
        _upsert_assignment(
            bind,
            assignment,
            history,
            instrument_id=str(row["instrument_id"]),
            node_id=target_node_id,
            path_labels=list(target["path_labels_json"]),
            source_record_id="migration:20260818_0042:etf",
            assigned_at=now,
        )

    for row in bind.execute(
        sa.select(detail.c.instrument_id).where(detail.c.instrument_type == "index")
    ):
        current = assignment_by_instrument.get(str(row.instrument_id))
        if current != "index":
            continue
        target = new_node_by_id["index-other"]
        _upsert_assignment(
            bind,
            assignment,
            history,
            instrument_id=str(row.instrument_id),
            node_id="index-other",
            path_labels=list(target["path_labels_json"]),
            source_record_id="migration:20260818_0042:index",
            assigned_at=now,
        )

    for instrument_id, exchange_code in equity_exchanges.items():
        node_id = EQUITY_NODE_BY_EXCHANGE.get(exchange_code)
        if node_id is None:
            raise RuntimeError(f"Unsupported equity exchange in Watchlist migration: {exchange_code}")
        target = new_node_by_id[node_id]
        _upsert_assignment(
            bind,
            assignment,
            history,
            instrument_id=instrument_id,
            node_id=node_id,
            path_labels=list(target["path_labels_json"]),
            source_record_id=f"registry_exchange:{exchange_code}",
            assigned_at=now,
        )

    bind.execute(
        sa.delete(node).where(
            sa.or_(
                node.c.node_id.in_(("fund-public", "fund-private", "index")),
                node.c.node_id == "equity",
                node.c.node_id.like("equity-sector-%"),
            )
        )
    )

    watchlist = sa.table(
        "watchlist",
        sa.column("watchlist_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("owner_type", sa.String()),
        sa.column("owner_id", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("is_shared", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    item = sa.table(
        "watchlist_item",
        sa.column("watchlist_id", sa.String()),
    )
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("watchlist_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
        sa.column("density", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    view_column = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("width", sa.Integer()),
        sa.column("is_visible", sa.Boolean()),
        sa.column("pin_side", sa.String()),
    )
    reserved_ids = [value[0] for value in SYSTEM_WATCHLISTS]
    removed_ids = ["all-coverage", *reserved_ids]

    legacy_classification_views = list(
        bind.execute(
            sa.select(view).where(
                view.c.watchlist_view_id.like("%::fund-screening"),
                view.c.watchlist_id.not_in(removed_ids),
            )
        ).mappings()
    )
    for legacy_view in legacy_classification_views:
        watchlist_id = str(legacy_view["watchlist_id"])
        new_view_id = f"{watchlist_id}::classification"
        if bind.execute(
            sa.select(view.c.watchlist_view_id).where(
                view.c.watchlist_view_id == new_view_id
            )
        ).first():
            raise RuntimeError(
                f"Watchlist already has a classification view during migration: {watchlist_id}"
            )
        bind.execute(
            sa.insert(view).values(
                watchlist_view_id=new_view_id,
                watchlist_id=watchlist_id,
                name="分类",
                description="使用 Watchlist 内部分类树组织当前列表。",
                kind=legacy_view["kind"],
                default_sort_json=legacy_view["default_sort_json"],
                default_filters_json=legacy_view["default_filters_json"],
                default_advanced_filter_json=legacy_view["default_advanced_filter_json"],
                default_group_by="taxonomy",
                density=legacy_view["density"],
                is_default=legacy_view["is_default"],
                created_at=legacy_view["created_at"],
            )
        )
        for field_key, display_order, width in CLASSIFICATION_COLUMNS:
            bind.execute(
                sa.insert(view_column).values(
                    watchlist_view_id=new_view_id,
                    field_key=field_key,
                    display_order=display_order,
                    width=width,
                    is_visible=True,
                    pin_side=None,
                )
            )
        bind.execute(
            sa.delete(view_column).where(
                view_column.c.watchlist_view_id == legacy_view["watchlist_view_id"]
            )
        )
        bind.execute(
            sa.delete(view).where(
                view.c.watchlist_view_id == legacy_view["watchlist_view_id"]
            )
        )

    removed_view_ids = list(
        bind.execute(
            sa.select(view.c.watchlist_view_id).where(view.c.watchlist_id.in_(removed_ids))
        ).scalars()
    )
    if removed_view_ids:
        bind.execute(
            sa.delete(view_column).where(view_column.c.watchlist_view_id.in_(removed_view_ids))
        )
    bind.execute(sa.delete(view).where(view.c.watchlist_id.in_(removed_ids)))
    bind.execute(sa.delete(item).where(item.c.watchlist_id.in_(removed_ids)))
    bind.execute(sa.delete(watchlist_row).where(watchlist_row.c.watchlist_id.in_(removed_ids)))
    bind.execute(sa.delete(watchlist).where(watchlist.c.watchlist_id.in_(removed_ids)))
    bind.execute(
        sa.update(watchlist)
        .where(watchlist.c.watchlist_id.not_in(reserved_ids))
        .values(sort_order=watchlist.c.sort_order + len(SYSTEM_WATCHLISTS))
    )

    overview_columns = (
        ("instrument_name", 1, 320),
        ("attr.coverage_status", 2, 110),
        ("return_chart_1m", 3, 140),
        ("latest_quote", 4, 130),
        ("latest_quote_date", 5, 140),
        ("return_1w", 6, 150),
        ("return_mtd", 7, 120),
        ("return_ytd", 8, 150),
        ("attr.current_drawdown", 9, 120),
    )
    for sort_order, (watchlist_id, name, description, instrument_type) in enumerate(
        SYSTEM_WATCHLISTS
    ):
        bind.execute(
            sa.insert(watchlist).values(
                watchlist_id=watchlist_id,
                name=name,
                description=description,
                owner_type="system",
                owner_id="watchlist",
                is_default=True,
                is_shared=True,
                sort_order=sort_order,
                created_at=now,
                updated_at=now,
            )
        )
        for local_view_id, view_name, view_description, default_group_by, filters, is_default in (
            ("overview", "Overview", "Default overview view", "none", {}, True),
            (
                "classification",
                "分类",
                "使用 Watchlist 内部分类树组织当前列表。",
                "taxonomy",
                {"instrument_type": [instrument_type]},
                False,
            ),
        ):
            view_id = f"{watchlist_id}::{local_view_id}"
            bind.execute(
                sa.insert(view).values(
                    watchlist_view_id=view_id,
                    watchlist_id=watchlist_id,
                    name=view_name,
                    description=view_description,
                    kind="system",
                    default_sort_json=[],
                    default_filters_json=filters,
                    default_advanced_filter_json={},
                    default_group_by=default_group_by,
                    density="standard",
                    is_default=is_default,
                    created_at=now,
                )
            )
            columns = overview_columns if local_view_id == "overview" else CLASSIFICATION_COLUMNS
            for field_key, display_order, width in columns:
                bind.execute(
                    sa.insert(view_column).values(
                        watchlist_view_id=view_id,
                        field_key=field_key,
                        display_order=display_order,
                        width=width,
                        is_visible=True,
                        pin_side=None,
                    )
                )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260818_0042 removes the ambiguous fund type and All Covered. "
        "Restore the pre-migration database backup instead."
    )
