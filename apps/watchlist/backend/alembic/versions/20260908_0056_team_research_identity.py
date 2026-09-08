"""Team research, permanent authors and private conversations.

Historical conversations remain unclaimed until the initial operator is explicitly
mapped by an administrator. Public/team research is never copied per account.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0056"
down_revision = "20260908_0055"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("instrument_research_note", "instrument_research_note_revision"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("owner_user_id", new_column_name="author_user_id", existing_type=sa.Text())
            batch.add_column(sa.Column("team_id", sa.Text(), nullable=False, server_default="default"))
    with op.batch_alter_table("research_topic") as batch:
        batch.add_column(sa.Column("team_id", sa.Text(), nullable=False, server_default="default"))
        batch.add_column(sa.Column("created_by_user_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("visibility", sa.Text(), nullable=False, server_default="private"))
        batch.create_index("ix_research_topic_created_by_user_id", ["created_by_user_id"])
    with op.batch_alter_table("research_entry") as batch:
        batch.add_column(sa.Column("team_id", sa.Text(), nullable=False, server_default="default"))
        batch.add_column(sa.Column("author_user_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("responsible_user_id", sa.Text(), nullable=True))
        batch.create_index("ix_research_entry_author_user_id", ["author_user_id"])
    with op.batch_alter_table("watchlist_view") as batch:
        batch.add_column(sa.Column("author_user_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("base_view_id", sa.String(), nullable=True))
    op.create_table("watchlist_user_settings", sa.Column("user_id", sa.String(), primary_key=True),
                    sa.Column("ordered_watchlist_ids", sa.JSON(), nullable=False))
    connection = op.get_bind()
    watchlist = sa.table("watchlist", sa.column("owner_type", sa.String()), sa.column("owner_id", sa.String()), sa.column("is_shared", sa.Boolean()))
    connection.execute(watchlist.update().where(watchlist.c.owner_type != "system").values(owner_type="team", owner_id="default", is_shared=True))
    topic = sa.table("research_topic", sa.column("topic_id", sa.Text()), sa.column("visibility", sa.Text()))
    connection.execute(topic.update().where(sa.or_(
        topic.c.topic_id.like("dossier:%"), topic.c.topic_id.like("risk-officer:%"),
        topic.c.topic_id.like("instrument-events:%"), topic.c.topic_id == "us-sector-daily-review",
    )).values(visibility="team"))
    entry = sa.table("research_entry", sa.column("entry_id", sa.Text()), sa.column("context_json", sa.JSON()),
                     sa.column("author_user_id", sa.Text()), sa.column("responsible_user_id", sa.Text()))
    for row in connection.execute(sa.select(entry.c.entry_id, entry.c.context_json)):
        context = dict(row.context_json or {})
        if context.get("role") == "research_theme":
            author = context.pop("owner_user_id", None)
            context.pop("author_user_id", None)
            connection.execute(entry.update().where(entry.c.entry_id == row.entry_id).values(
                context_json=context, author_user_id=author, responsible_user_id=author))


def downgrade():
    raise RuntimeError("Multi-account research attribution cannot be downgraded without an explicit data export.")
