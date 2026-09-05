"""Merge the duplicate Longqi instrument into canonical SH7639.

Revision ID: 20260712_0005
Revises: 20260712_0004
"""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260712_0005"
down_revision = "20260712_0004"
branch_labels = None
depends_on = None


CANONICAL_ID = "sh7639"
DUPLICATE_ID = "nav-8c76dae71a"


instrument = sa.table(
    "instrument",
    sa.column("instrument_id", sa.String()),
    sa.column("source_settings_json", sa.JSON()),
    sa.column("refresh_status_json", sa.JSON()),
    sa.column("quote_selection_policy_json", sa.JSON()),
    sa.column("lifecycle_state_json", sa.JSON()),
    sa.column("market_data_updated_at", sa.String()),
)
identifier = sa.table(
    "instrument_identifier",
    sa.column("instrument_identifier_id", sa.Integer()),
    sa.column("instrument_id", sa.String()),
    sa.column("identifier_type", sa.String()),
    sa.column("identifier_value", sa.String()),
    sa.column("is_primary", sa.Boolean()),
)
market_data = sa.table(
    "instrument_market_data",
    sa.column("instrument_id", sa.String()),
    sa.column("metric_family", sa.String()),
    sa.column("quote_basis", sa.String()),
    sa.column("as_of_date", sa.Date()),
    sa.column("value", sa.Text()),
    sa.column("currency", sa.String()),
    sa.column("provider", sa.String()),
    sa.column("status", sa.String()),
)
registry_metadata = sa.table(
    "registry_metadata",
    sa.column("registry_key", sa.String()),
    sa.column("market_data_updated_at", sa.String()),
)


CORRECT_FUND_POLICY = {
    "trading": ["last", "close", "official_nav"],
    "valuation": ["official_nav", "close", "last"],
    "total_return": [
        "total_return_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
        "adjusted_close",
        "official_nav",
        "close",
    ],
    "chart": [
        "total_return_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
        "adjusted_close",
        "official_nav",
        "close",
    ],
    "reference": ["official_nav", "close", "last"],
}


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def upgrade() -> None:
    connection = op.get_bind()
    records = {
        row["instrument_id"]: row
        for row in connection.execute(
            sa.select(instrument).where(
                instrument.c.instrument_id.in_((CANONICAL_ID, DUPLICATE_ID))
            )
        ).mappings()
    }
    canonical = records.get(CANONICAL_ID)
    duplicate = records.get(DUPLICATE_ID)
    if canonical is None or duplicate is None:
        return

    existing_keys = set(
        connection.execute(
            sa.select(
                market_data.c.metric_family,
                market_data.c.quote_basis,
                market_data.c.as_of_date,
                market_data.c.currency,
            ).where(market_data.c.instrument_id == CANONICAL_ID)
        ).tuples()
    )
    for row in connection.execute(
        sa.select(market_data).where(market_data.c.instrument_id == DUPLICATE_ID)
    ).mappings():
        key = (
            row["metric_family"],
            row["quote_basis"],
            row["as_of_date"],
            row["currency"],
        )
        if key in existing_keys:
            continue
        connection.execute(
            sa.insert(market_data).values(
                instrument_id=CANONICAL_ID,
                metric_family=row["metric_family"],
                quote_basis=row["quote_basis"],
                as_of_date=row["as_of_date"],
                value=row["value"],
                currency=row["currency"],
                provider=row["provider"],
                status=row["status"],
            )
        )
        existing_keys.add(key)

    canonical_source = dict(canonical["source_settings_json"] or {})
    duplicate_source = dict(duplicate["source_settings_json"] or {})
    for key in (
        "source_mode",
        "source_email",
        "source_email_rules",
        "source_location",
        "source_api_profile",
    ):
        if duplicate_source.get(key) not in (None, "", []):
            canonical_source[key] = duplicate_source[key]

    changed_at = _watermark()
    connection.execute(
        sa.update(instrument)
        .where(instrument.c.instrument_id == CANONICAL_ID)
        .values(
            source_settings_json=canonical_source,
            refresh_status_json=dict(duplicate["refresh_status_json"] or {}),
            quote_selection_policy_json=CORRECT_FUND_POLICY,
            market_data_updated_at=changed_at,
        )
    )
    connection.execute(
        sa.update(identifier)
        .where(identifier.c.instrument_id == DUPLICATE_ID)
        .values(instrument_id=CANONICAL_ID, is_primary=False)
    )
    duplicate_state = dict(duplicate["lifecycle_state_json"] or {})
    duplicate_state.update(
        {
            "status": "archived",
            "changed_at": changed_at,
            "changed_by": "merge_duplicate_into_sh7639",
            "canonical_instrument_id": CANONICAL_ID,
        }
    )
    connection.execute(
        sa.update(instrument)
        .where(instrument.c.instrument_id == DUPLICATE_ID)
        .values(
            lifecycle_state_json=duplicate_state,
            source_settings_json={
                "source_mode": "manual",
                "source_email": "",
                "source_location": f"Archived alias of {CANONICAL_ID}",
                "source_api_profile": "",
                "source_email_rules": [],
            },
            market_data_updated_at=changed_at,
        )
    )
    connection.execute(
        sa.update(registry_metadata)
        .where(registry_metadata.c.registry_key == "shared")
        .values(market_data_updated_at=changed_at)
    )


def downgrade() -> None:
    # The original duplicate remains archived, so the merge is deliberately not
    # reversed into two active master records.
    pass
