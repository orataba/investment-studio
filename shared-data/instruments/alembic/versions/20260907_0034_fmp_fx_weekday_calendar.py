"""Declare weekday availability for the project's maintained FMP FX sources."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260907_0034"
down_revision = "20260907_0033"
branch_labels = None
depends_on = None

FX_IDS = ("fx-usd-hkd", "fx-usd-cny", "fx-usd-eur", "fx-usd-gbp", "fx-usd-chf")


def _set_calendar(previous: str | None, calendar: str | None) -> None:
    instruments = sa.table("instrument", sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()), sa.column("source_settings_json", sa.JSON()),
        sa.column("calculation_inputs_updated_at", sa.String()))
    connection = op.get_bind()
    changed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    for row in connection.execute(sa.select(instruments).where(
            instruments.c.instrument_id.in_(FX_IDS), instruments.c.instrument_type == "fx")).mappings():
        settings = row["source_settings_json"]
        if (settings.get("source_mode") != "api" or settings.get("source_api_profile") != "fmp"
                or settings.get("market_calendar") != previous):
            continue
        connection.execute(instruments.update().where(instruments.c.instrument_id == row["instrument_id"]).values(
            source_settings_json={**settings, "market_calendar": calendar},
            calculation_inputs_updated_at=changed_at,
        ))


def upgrade() -> None:
    # This changes missing-day expectations only. Actual weekend observations
    # remain usable; no observation is inserted, removed, or dated differently.
    _set_calendar(None, "24/5")


def downgrade() -> None:
    _set_calendar("24/5", None)
