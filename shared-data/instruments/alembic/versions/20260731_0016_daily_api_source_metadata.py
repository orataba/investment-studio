"""Repair daily API schedule metadata and enable the H11001 CSI fallback.

Revision ID: 20260731_0016
Revises: 20260717_0015
"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260731_0016"
down_revision = "20260717_0015"
branch_labels = None
depends_on = None


TUSHARE_PROFILE_ALIASES = {"tushare", "tushare_pro", "tushare-pro"}
H11001_INSTRUMENT_ID = "h11001-csi"
CSINDEX_API_URL = "https://www.csindex.com.cn/csindex-home"


instrument = sa.table(
    "instrument",
    sa.column("instrument_id", sa.String()),
    sa.column("instrument_type", sa.String()),
    sa.column("source_settings_json", sa.JSON()),
    sa.column("lifecycle_state_json", sa.JSON()),
    sa.column("market_data_updated_at", sa.String()),
)
registry_metadata = sa.table(
    "registry_metadata",
    sa.column("registry_key", sa.String()),
    sa.column("market_data_updated_at", sa.String()),
)


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_active(row: dict[str, object]) -> bool:
    lifecycle_state = dict(row.get("lifecycle_state_json") or {})
    return str(lifecycle_state.get("status") or "active").strip().lower() == "active"


def _is_tushare_api(source_settings: dict[str, object]) -> bool:
    return (
        str(source_settings.get("source_mode") or "").strip().lower() == "api"
        and str(source_settings.get("source_api_profile") or "").strip().lower()
        in TUSHARE_PROFILE_ALIASES
    )


def upgrade() -> None:
    connection = op.get_bind()
    changed_at = _watermark()
    changed = False
    rows = connection.execute(sa.select(instrument)).mappings()
    for raw_row in rows:
        row = dict(raw_row)
        if not _is_active(row):
            continue
        source_settings = dict(row.get("source_settings_json") or {})
        instrument_id = str(row.get("instrument_id") or "")
        row_changed = False

        if (
            str(row.get("instrument_type") or "").strip().lower() == "etf"
            and _is_tushare_api(source_settings)
            and not str(source_settings.get("expected_frequency") or "").strip()
        ):
            source_settings["expected_frequency"] = "daily"
            if not str(source_settings.get("market_calendar") or "").strip():
                source_settings["market_calendar"] = "XSHG"
            if source_settings.get("release_lag_days") is None:
                source_settings["release_lag_days"] = 0
            row_changed = True

        if instrument_id == H11001_INSTRUMENT_ID and _is_tushare_api(
            source_settings
        ):
            fallback_settings = {
                "source_api_fallback_profile": "csindex",
                "source_api_fallback_code": "H11001",
                "source_api_fallback_location": CSINDEX_API_URL,
            }
            if any(
                source_settings.get(key) != value
                for key, value in fallback_settings.items()
            ):
                source_settings.update(fallback_settings)
                row_changed = True

        if not row_changed:
            continue
        connection.execute(
            sa.update(instrument)
            .where(instrument.c.instrument_id == instrument_id)
            .values(
                source_settings_json=source_settings,
                market_data_updated_at=changed_at,
            )
        )
        changed = True

    if changed:
        connection.execute(
            sa.update(registry_metadata)
            .where(registry_metadata.c.registry_key == "shared")
            .values(market_data_updated_at=changed_at)
        )


def downgrade() -> None:
    # Corrective source metadata may be edited after deployment. Removing keys
    # during a downgrade would erase those later operational choices, so the
    # data repair is deliberately retained.
    pass
