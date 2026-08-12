"""Remove weekly and monthly source-frequency settings.

Revision ID: 20260812_0024
Revises: 20260810_0023
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_0024"
down_revision: str | None = "20260810_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def upgrade() -> None:
    connection = op.get_bind()
    instrument = sa.table(
        "instrument",
        sa.column("instrument_id", sa.String()),
        sa.column("source_settings_json", sa.JSON()),
        sa.column("market_data_updated_at", sa.String()),
        sa.column("calculation_inputs_updated_at", sa.String()),
    )
    changed_at = _watermark()
    for row in connection.execute(
        sa.select(instrument.c.instrument_id, instrument.c.source_settings_json)
    ).mappings():
        settings = row["source_settings_json"]
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except json.JSONDecodeError:
                continue
        if not isinstance(settings, dict):
            continue
        expected_frequency = str(settings.get("expected_frequency") or "").strip().lower()
        if expected_frequency not in {"weekly", "monthly"}:
            continue
        normalized = dict(settings)
        normalized["expected_frequency"] = "daily"
        connection.execute(
            sa.update(instrument)
            .where(instrument.c.instrument_id == row["instrument_id"])
            .values(
                source_settings_json=normalized,
                market_data_updated_at=changed_at,
                calculation_inputs_updated_at=changed_at,
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260812_0024 removes weekly and monthly source-frequency state. "
        "Restore the pre-migration database backup instead of downgrading."
    )
