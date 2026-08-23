"""Apply the single-primary-source policy for listed market data.

Revision ID: 20260823_0027
Revises: 20260822_0026
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_0027"
down_revision: str | None = "20260822_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FALLBACK_KEYS = (
    "source_api_fallback_profile",
    "source_api_fallback_code",
    "source_api_fallback_location",
)


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def upgrade() -> None:
    connection = op.get_bind()
    changed_at = _watermark()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT instrument_id, instrument_type, exchange_code, "
                "source_settings_json FROM instrument ORDER BY instrument_id"
            )
        ).mappings()
    )
    for row in rows:
        settings = _json_object(row["source_settings_json"])
        had_fallback = any(key in settings for key in FALLBACK_KEYS)
        changed = False
        for key in FALLBACK_KEYS:
            if key in settings:
                settings.pop(key)
                changed = True

        is_mainland_etf = (
            str(row["instrument_type"]) == "etf"
            and str(row["exchange_code"]) in {"XSHG", "XSHE"}
        )
        fmp_cutover = (
            is_mainland_etf
            and str(settings.get("source_api_profile") or "").strip().lower() == "fmp"
        )
        if fmp_cutover:
            exchange_ticker = connection.execute(
                sa.text(
                    "SELECT identifier_value FROM instrument_identifier "
                    "WHERE instrument_id = :instrument_id "
                    "AND identifier_type IN ('exchange_ticker', 'ticker') "
                    "ORDER BY is_primary DESC, instrument_identifier_id LIMIT 1"
                ),
                {"instrument_id": str(row["instrument_id"])},
            ).scalar_one_or_none()
            if exchange_ticker is None:
                raise RuntimeError(
                    f"Mainland ETF {row['instrument_id']} has no exchange ticker."
                )
            settings.update(
                {
                    "source_mode": "api",
                    "source_location": "DataHub Tushare",
                    "source_api_profile": "tushare",
                    "expected_frequency": "daily",
                    "market_calendar": str(row["exchange_code"]),
                    "release_lag_days": 0,
                    "return_semantics": "unknown",
                    "source_price_multiplier": "1",
                }
            )
            settings.pop("source_provider_currency", None)
            changed = True

            connection.execute(
                sa.text(
                    "DELETE FROM instrument_market_data "
                    "WHERE instrument_id = :instrument_id AND provider LIKE 'fmp:%'"
                ),
                {"instrument_id": str(row["instrument_id"])},
            )
            connection.execute(
                sa.text(
                    "DELETE FROM instrument_price_bar "
                    "WHERE instrument_id = :instrument_id AND provider LIKE 'fmp:%'"
                ),
                {"instrument_id": str(row["instrument_id"])},
            )
            tushare_symbol = f"tushare:{str(exchange_ticker).upper()}"
            identifier_owner = connection.execute(
                sa.text(
                    "SELECT instrument_id FROM instrument_identifier "
                    "WHERE identifier_type = 'provider_symbol' "
                    "AND identifier_value = :identifier_value"
                ),
                {"identifier_value": tushare_symbol},
            ).scalar_one_or_none()
            if (
                identifier_owner is not None
                and str(identifier_owner) != str(row["instrument_id"])
            ):
                raise RuntimeError(
                    f"Tushare provider symbol {tushare_symbol} already belongs to {identifier_owner}."
                )
            if identifier_owner is None:
                connection.execute(
                    sa.text(
                        "INSERT INTO instrument_identifier "
                        "(instrument_id, identifier_type, identifier_value, is_primary) "
                        "VALUES (:instrument_id, 'provider_symbol', :identifier_value, false)"
                    ),
                    {
                        "instrument_id": str(row["instrument_id"]),
                        "identifier_value": tushare_symbol,
                    },
                )

        if had_fallback:
            connection.execute(
                sa.text(
                    "DELETE FROM instrument_market_data "
                    "WHERE instrument_id = :instrument_id AND provider LIKE 'csindex:%'"
                ),
                {"instrument_id": str(row["instrument_id"])},
            )

        if not changed:
            continue

        values: dict[str, object] = {
            "instrument_id": str(row["instrument_id"]),
            "settings": json.dumps(settings, ensure_ascii=False),
        }
        assignments = "source_settings_json = :settings"
        if fmp_cutover or had_fallback:
            has_remaining_market_data = bool(
                connection.execute(
                    sa.text(
                        "SELECT CASE WHEN "
                        "EXISTS (SELECT 1 FROM instrument_market_data "
                        "WHERE instrument_id = :instrument_id) "
                        "OR EXISTS (SELECT 1 FROM instrument_price_bar "
                        "WHERE instrument_id = :instrument_id) "
                        "THEN 1 ELSE 0 END"
                    ),
                    {"instrument_id": str(row["instrument_id"])},
                ).scalar_one()
            )
            values.update(
                {
                    "refresh_status": json.dumps(
                        {
                            "status": "idle",
                            "message": (
                                "Awaiting full DataHub Tushare history repair after the "
                                "mainland ETF source cutover."
                                if fmp_cutover
                                else (
                                    "Former fallback observations were removed; awaiting "
                                    "the configured primary-source refresh."
                                )
                            ),
                            "requested_at": None,
                            "requested_by": None,
                            "mode": "api",
                            "last_successful_requested_at": None,
                        },
                        ensure_ascii=False,
                    ),
                    "changed_at": changed_at,
                    "market_data_updated_at": (
                        changed_at if has_remaining_market_data else None
                    ),
                }
            )
            assignments += (
                ", refresh_status_json = :refresh_status, "
                "market_data_updated_at = :market_data_updated_at, "
                "calculation_inputs_updated_at = :changed_at"
            )
        connection.execute(
            sa.text(
                f"UPDATE instrument SET {assignments} "
                "WHERE instrument_id = :instrument_id"
            ),
            values,
        )


def downgrade() -> None:
    connection = op.get_bind()
    changed_at = _watermark()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT instrument_id, exchange_code, source_settings_json, "
                "refresh_status_json FROM instrument "
                "WHERE instrument_type = 'etf' "
                "AND exchange_code IN ('XSHG', 'XSHE') "
                "ORDER BY instrument_id"
            )
        ).mappings()
    )
    for row in rows:
        refresh_status = _json_object(row["refresh_status_json"])
        if not str(refresh_status.get("message") or "").startswith(
            "Awaiting full DataHub Tushare history repair"
        ):
            continue
        settings = _json_object(row["source_settings_json"])
        settings.update(
            {
                "source_mode": "api",
                "source_location": "FMP API",
                "source_api_profile": "fmp",
                "expected_frequency": "daily",
                "release_lag_days": 0,
                "return_semantics": "price_return",
                "source_price_multiplier": "1",
            }
        )
        connection.execute(
            sa.text(
                "UPDATE instrument SET source_settings_json = :settings, "
                "refresh_status_json = :refresh_status, "
                "calculation_inputs_updated_at = :changed_at "
                "WHERE instrument_id = :instrument_id"
            ),
            {
                "instrument_id": str(row["instrument_id"]),
                "settings": json.dumps(settings, ensure_ascii=False),
                "refresh_status": json.dumps(
                    {
                        "status": "idle",
                        "message": "Awaiting FMP EOD refresh after source rollback.",
                        "requested_at": None,
                        "requested_by": None,
                        "mode": "api",
                        "last_successful_requested_at": None,
                    },
                    ensure_ascii=False,
                ),
                "changed_at": changed_at,
            },
        )
