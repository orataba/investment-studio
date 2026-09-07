"""Retain each reference collection and seed only genuinely collected baselines."""

from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260907_0033"
down_revision = "20260904_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    observations = op.create_table(
        "instrument_reference_observation",
        sa.Column("observation_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"],
                                name="fk_instrument_reference_observation_instrument_id_instrument",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("observation_id", name="pk_instrument_reference_observation"),
    )
    op.create_index("ix_reference_observation_instrument_collected", "instrument_reference_observation",
                    ["instrument_id", "collected_at", "observation_id"])
    snapshots = sa.table("instrument_reference_snapshot", sa.column("instrument_id", sa.String()),
                         sa.column("value_json", sa.JSON()))
    connection = op.get_bind()
    for instrument_id, value in connection.execute(sa.select(snapshots)):
        # Do not turn research creation times or undated placeholders into history.
        if not value.get("sections") or not value.get("fetched_at"):
            continue
        try:
            collected_at = datetime.fromisoformat(value["fetched_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError):
            continue
        if collected_at.tzinfo is None:
            continue
        connection.execute(observations.insert().values(
            observation_id=str(uuid4()), instrument_id=instrument_id,
            collected_at=collected_at.astimezone(UTC), value_json=value,
        ))


def downgrade() -> None:
    op.drop_index("ix_reference_observation_instrument_collected", table_name="instrument_reference_observation")
    op.drop_table("instrument_reference_observation")
