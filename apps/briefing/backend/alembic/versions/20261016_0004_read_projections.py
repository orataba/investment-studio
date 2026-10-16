"""Project frozen edition metadata and source rows for bounded reading."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0004"
down_revision = "20260908_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("report", sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("report", sa.Column("edition_role", sa.String(16), nullable=False, server_default="preview"))
    op.add_column("report", sa.Column("reading_json", sa.JSON(), nullable=False, server_default="{}"))
    sources = op.create_table("report_source",
        sa.Column("report_id", sa.String(32), sa.ForeignKey("report.report_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("window_scope", sa.String(32), nullable=False),
        sa.Column("completeness", sa.String(32), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False))
    op.create_index("ix_briefing_source_identity", "report_source", ["report_id", "source_id"])
    # Bounded one-edition backfill, preserving input/result bytes and source order.
    # This is a serving projection only: it never re-reads a provider or regenerates text.
    from briefing_app.read_projection import reading_projection, source_projection_rows
    connection = op.get_bind()
    report = sa.table("report", sa.column("report_id", sa.String()), sa.column("input_json", sa.JSON()),
                      sa.column("result_json", sa.JSON()), sa.column("source_count", sa.Integer()),
                      sa.column("edition_role", sa.String()), sa.column("reading_json", sa.JSON()))
    identifiers = connection.execute(sa.select(report.c.report_id)).scalars().all()
    for identifier in identifiers:
        row = connection.execute(sa.select(report.c.input_json, report.c.result_json).where(report.c.report_id == identifier)).one()
        snapshot = row.input_json or {}
        connection.execute(report.update().where(report.c.report_id == identifier).values(
            source_count=snapshot.get("source_count", 0), edition_role=snapshot.get("edition_role", "preview"),
            reading_json=reading_projection(snapshot, row.result_json)))
        rows = list(source_projection_rows(identifier, snapshot))
        if rows:
            connection.execute(sources.insert(), rows)


def downgrade():
    op.drop_table("report_source")
    for name in ("reading_json", "edition_role", "source_count"):
        op.drop_column("report", name)
