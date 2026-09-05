"""Keep adjusted prices out of listed-security valuation policies.

Revision ID: 20260712_0006
Revises: 20260712_0005
"""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260712_0006"
down_revision = "20260712_0005"
branch_labels = None
depends_on = None


instrument = sa.table(
    "instrument",
    sa.column("instrument_id", sa.String()),
    sa.column("instrument_type", sa.String()),
    sa.column("quote_selection_policy_json", sa.JSON()),
    sa.column("market_data_updated_at", sa.String()),
)
registry_metadata = sa.table(
    "registry_metadata",
    sa.column("registry_key", sa.String()),
    sa.column("market_data_updated_at", sa.String()),
)


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def upgrade() -> None:
    connection = op.get_bind()
    changed_at = _watermark()
    changed = False
    records = list(connection.execute(
        sa.select(
            instrument.c.instrument_id,
            instrument.c.quote_selection_policy_json,
        ).where(instrument.c.instrument_type.in_(("equity", "etf", "index")))
    ).mappings())
    for record in records:
        policy = dict(record["quote_selection_policy_json"] or {})
        if policy.get("valuation") == ["close", "last"]:
            continue
        policy["valuation"] = ["close", "last"]
        connection.execute(
            sa.update(instrument)
            .where(instrument.c.instrument_id == record["instrument_id"])
            .values(
                quote_selection_policy_json=policy,
                market_data_updated_at=changed_at,
            )
        )
        changed = True

    cumulative_aliases = {"cumulative_nav", "accumulated_nav", "cum_nav"}
    fund_records = list(
        connection.execute(
            sa.select(
                instrument.c.instrument_id,
                instrument.c.quote_selection_policy_json,
            ).where(instrument.c.instrument_type == "fund")
        ).mappings()
    )
    for record in fund_records:
        policy = dict(record["quote_selection_policy_json"] or {})
        policy_changed = False
        for role in ("total_return", "chart"):
            raw_values = policy.get(role)
            if not isinstance(raw_values, list):
                continue
            clean_values = [
                value for value in raw_values if str(value) not in cumulative_aliases
            ]
            if clean_values != raw_values:
                policy[role] = clean_values
                policy_changed = True
        if not policy_changed:
            continue
        connection.execute(
            sa.update(instrument)
            .where(instrument.c.instrument_id == record["instrument_id"])
            .values(
                quote_selection_policy_json=policy,
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
    # Reintroducing adjusted close into valuation would restore the accounting
    # defect fixed by this revision, so this data correction is irreversible.
    pass
