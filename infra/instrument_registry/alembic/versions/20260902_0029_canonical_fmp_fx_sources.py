"""Make FMP the canonical source for every maintained FX pair.

Revision ID: 20260902_0029
Revises: 20260823_0028
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0029"
down_revision: str | None = "20260823_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FX_IDENTITIES = (
    ("fx-usd-hkd", "USD", "HKD"),
    ("fx-usd-cny", "USD", "CNY"),
    ("fx-usd-eur", "USD", "EUR"),
    ("fx-usd-gbp", "USD", "GBP"),
    ("fx-usd-chf", "USD", "CHF"),
)
SOURCE_CUTOVER_IDS = ("fx-usd-hkd", "fx-usd-cny")


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_quote_policy() -> dict[str, list[str]]:
    return {
        role: ["spot"]
        for role in ("trading", "valuation", "total_return", "chart", "reference")
    }


def _canonical_source_settings() -> dict[str, object]:
    return {
        "source_mode": "api",
        "source_email": "",
        "source_location": "FMP API",
        "source_api_profile": "fmp",
        "source_email_rules": [],
        "expected_frequency": "daily",
        "market_calendar": None,
        "release_lag_days": 0,
        "return_semantics": "price_return",
    }


def _ensure_identifier(
    connection: sa.Connection,
    *,
    instrument_id: str,
    identifier_type: str,
    identifier_value: str,
    is_primary: bool,
) -> None:
    owner = connection.execute(
        sa.text(
            "SELECT instrument_id FROM instrument_identifier "
            "WHERE identifier_type = :identifier_type "
            "AND identifier_value = :identifier_value"
        ),
        {
            "identifier_type": identifier_type,
            "identifier_value": identifier_value,
        },
    ).scalar_one_or_none()
    if owner is not None and str(owner) != instrument_id:
        raise RuntimeError(
            f"FX identifier {identifier_type}:{identifier_value} belongs to {owner}."
        )
    if owner is None:
        connection.execute(
            sa.text(
                "INSERT INTO instrument_identifier "
                "(instrument_id, identifier_type, identifier_value, is_primary) "
                "VALUES (:instrument_id, :identifier_type, :identifier_value, :is_primary)"
            ),
            {
                "instrument_id": instrument_id,
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
                "is_primary": is_primary,
            },
        )


def _ensure_fx_instrument(
    connection: sa.Connection,
    *,
    instrument_id: str,
    base_currency: str,
    quote_currency: str,
    changed_at: str,
) -> None:
    existing = connection.execute(
        sa.text(
            "SELECT instrument_type, currency FROM instrument "
            "WHERE instrument_id = :instrument_id"
        ),
        {"instrument_id": instrument_id},
    ).mappings().one_or_none()
    if existing is not None and (
        str(existing["instrument_type"]) != "fx"
        or str(existing["currency"]) != quote_currency
    ):
        raise RuntimeError(
            f"Maintained FX identity {instrument_id} has incompatible type or currency."
        )

    awaiting_refresh = {
        "status": "idle",
        "message": "Awaiting full FMP FX EOD history refresh.",
        "requested_at": None,
        "requested_by": None,
        "mode": "api",
        "last_successful_requested_at": None,
    }
    if existing is None:
        connection.execute(
            sa.text(
                "INSERT INTO instrument "
                "(instrument_id, instrument_name, instrument_type, currency, exchange_code, "
                "quote_selection_policy_json, source_settings_json, refresh_status_json, "
                "lifecycle_state_json, market_data_updated_at, calculation_inputs_updated_at) "
                "VALUES (:instrument_id, :instrument_name, 'fx', :currency, NULL, "
                ":quote_policy, :source_settings, :refresh_status, :lifecycle_state, NULL, :changed_at)"
            ),
            {
                "instrument_id": instrument_id,
                "instrument_name": f"{base_currency}/{quote_currency} Spot",
                "currency": quote_currency,
                "quote_policy": json.dumps(_canonical_quote_policy()),
                "source_settings": json.dumps(_canonical_source_settings()),
                "refresh_status": json.dumps(awaiting_refresh),
                "lifecycle_state": json.dumps(
                    {
                        "status": "active",
                        "changed_at": changed_at,
                        "changed_by": "migration:20260902_0029",
                        "canonical_instrument_id": None,
                    }
                ),
                "changed_at": changed_at,
            },
        )
    else:
        refresh_status_assignment = ""
        values: dict[str, object] = {
            "instrument_id": instrument_id,
            "instrument_name": f"{base_currency}/{quote_currency} Spot",
            "quote_policy": json.dumps(_canonical_quote_policy()),
            "source_settings": json.dumps(_canonical_source_settings()),
            "changed_at": changed_at,
        }
        if instrument_id in SOURCE_CUTOVER_IDS:
            refresh_status_assignment = ", refresh_status_json = :refresh_status, market_data_updated_at = NULL"
            values["refresh_status"] = json.dumps(awaiting_refresh)
        connection.execute(
            sa.text(
                "UPDATE instrument SET instrument_name = :instrument_name, "
                "quote_selection_policy_json = :quote_policy, "
                "source_settings_json = :source_settings, "
                "calculation_inputs_updated_at = :changed_at"
                f"{refresh_status_assignment} WHERE instrument_id = :instrument_id"
            ),
            values,
        )

    _ensure_identifier(
        connection,
        instrument_id=instrument_id,
        identifier_type="internal",
        identifier_value=instrument_id,
        is_primary=True,
    )
    _ensure_identifier(
        connection,
        instrument_id=instrument_id,
        identifier_type="ticker",
        identifier_value=base_currency + quote_currency,
        is_primary=False,
    )


def upgrade() -> None:
    connection = op.get_bind()
    changed_at = _watermark()
    for instrument_id, base_currency, quote_currency in FX_IDENTITIES:
        _ensure_fx_instrument(
            connection,
            instrument_id=instrument_id,
            base_currency=base_currency,
            quote_currency=quote_currency,
            changed_at=changed_at,
        )

    # The two legacy pairs used manually maintained CFETS observations.  Mixing
    # those points with the FMP series would create a provider discontinuity, so
    # the refetchable series is cleared and rebuilt from FMP in full.
    connection.execute(
        sa.text(
            "DELETE FROM instrument_market_data "
            "WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "The canonical FMP FX cutover removed refetchable legacy observations. "
        "Restore the pre-migration database backup instead of downgrading."
    )
