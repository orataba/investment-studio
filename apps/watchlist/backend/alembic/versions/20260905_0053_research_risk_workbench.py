"""Research topics, note completion, and instrument risk follow-up.

Revision ID: 20260905_0053
Revises: 20260901_0052
"""
from alembic import op
import sqlalchemy as sa
revision = "20260905_0053"
down_revision = "20260901_0052"
branch_labels = depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade():
    for table in ("instrument_research_profile", "instrument_research_profile_revision"):
        op.add_column(table, sa.Column("research_stage", sa.Text(), nullable=False, server_default="watching"))
    for table in ("instrument_research_note", "instrument_research_note_revision"):
        op.add_column(table, sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_table("research_topic",
        sa.Column("topic_id", sa.String(), primary_key=True), sa.Column("title", sa.String(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False), sa.Column("instrument_ids", sa.JSON(), nullable=False),
        sa.Column("portfolio_id", sa.String()), sa.Column("status", sa.String(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False), sa.Column("next_review_date", sa.Date()), *timestamps())
    op.create_table("research_entry",
        sa.Column("entry_id", sa.String(), primary_key=True),
        sa.Column("topic_id", sa.String(), sa.ForeignKey("research_topic.topic_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False), sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False), sa.Column("source", sa.Text(), nullable=False),
        sa.Column("follow_up_date", sa.Date()), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("context_json", sa.JSON(), nullable=False), sa.Column("status", sa.String(), nullable=False), *timestamps())
    op.create_index("ix_research_entry_topic_id", "research_entry", ["topic_id"])
    op.create_table("risk_review_rule",
        sa.Column("instrument_id", sa.String(), sa.ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("drawdown_limit", sa.Float()), *timestamps())
    op.create_table("risk_case",
        sa.Column("case_id", sa.String(), primary_key=True),
        sa.Column("instrument_id", sa.String(), sa.ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal", sa.String(), nullable=False), sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False), sa.Column("severity", sa.String(), nullable=False),
        sa.Column("trigger_active", sa.Boolean(), nullable=False), sa.Column("status", sa.String(), nullable=False),
        sa.Column("observed_on", sa.Date()), sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("follow_up_date", sa.Date()), sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("history_json", sa.JSON(), nullable=False), *timestamps())
    op.create_index("ix_risk_case_instrument_id", "risk_case", ["instrument_id"])
    bind = op.get_bind()
    registry = sa.Table("field_registry", sa.MetaData(), autoload_with=bind)
    for key, label, domain in [("research_stage", "Research Stage", "investment_research"), ("risk_attention", "Risk Attention", "investment_research")]:
        bind.execute(registry.insert().values(field_key=f"attr.{key}", label=label, description=label,
            category_code="general", data_type="string", formatter_code="text", sort_mode="alpha",
            filter_mode="multi_select", group_mode="none", instrument_scope_json=[], product_scope_json=[],
            availability_rule_json={}, source_domain=domain, source_metric_code=key, default_width=140, default_visible=True))
    # Built-in defaults become flat. User-created views and their chosen grouping remain intact.
    views = sa.Table("watchlist_view", sa.MetaData(), autoload_with=bind)
    bind.execute(views.update().where(sa.and_(views.c.kind == "system", views.c.is_default.is_(True))).values(default_group_by="none"))

    columns = sa.Table("watchlist_view_column", sa.MetaData(), autoload_with=bind)
    for view_id in bind.scalars(sa.select(views.c.watchlist_view_id).where(views.c.kind == "system", views.c.is_default.is_(True))):
        bind.execute(columns.delete().where(columns.c.watchlist_view_id == view_id))
        bind.execute(columns.insert(), [
            {"watchlist_view_id": view_id, "field_key": "instrument_name", "display_order": 1, "width": 280, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "instrument_type", "display_order": 2, "width": 110, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "attr.instrument_taxonomy_path", "display_order": 3, "width": 210, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "attr.research_stage", "display_order": 4, "width": 100, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "return_chart_1m", "display_order": 5, "width": 120, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "return_ytd", "display_order": 6, "width": 115, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "attr.current_drawdown", "display_order": 7, "width": 115, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "attr.risk_attention", "display_order": 8, "width": 120, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "attr.research_updated_at", "display_order": 9, "width": 150, "is_visible": True},
            {"watchlist_view_id": view_id, "field_key": "latest_quote_date", "display_order": 10, "width": 120, "is_visible": True},
        ])


def downgrade():
    bind = op.get_bind()
    registry = sa.table("field_registry", sa.column("field_key", sa.String()))
    bind.execute(registry.delete().where(registry.c.field_key.in_(["attr.research_stage", "attr.risk_attention"])))
    for table in ("risk_case", "risk_review_rule", "research_entry", "research_topic"):
        op.drop_table(table)
    for table in ("instrument_research_note", "instrument_research_note_revision"):
        op.drop_column(table, "completed_at")
    for table in ("instrument_research_profile", "instrument_research_profile_revision"):
        op.drop_column(table, "research_stage")
