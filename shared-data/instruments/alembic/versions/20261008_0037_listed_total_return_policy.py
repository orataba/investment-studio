"""Remove raw-price fallbacks from listed-security economic-return policies.

Revision ID: 20261008_0037
Revises: 20260920_0036
"""
from datetime import UTC, datetime, timedelta

from alembic import op
import sqlalchemy as sa


revision = "20261008_0037"
down_revision = "20260920_0036"
branch_labels = None
depends_on = None

CONSTRAINT = "listed_total_return_policy"
POSTGRES_CHECK = (
    "instrument_type NOT IN ('equity', 'etf') OR "
    "coalesce(quote_selection_policy_json::jsonb -> 'total_return' = "
    "'[\"adjusted_close\"]'::jsonb, false)"
)
SQLITE_CHECK = (
    "instrument_type NOT IN ('equity', 'etf') OR coalesce("
    "json_type(quote_selection_policy_json, '$.total_return') = 'array' AND "
    "json_array_length(quote_selection_policy_json, '$.total_return') = 1 AND "
    "json_extract(quote_selection_policy_json, '$.total_return[0]') = 'adjusted_close', 0)"
)
instrument = sa.table(
    "instrument", sa.column("instrument_id", sa.String()),
    sa.column("instrument_type", sa.String()),
    sa.column("quote_selection_policy_json", sa.JSON()),
    sa.column("calculation_inputs_updated_at", sa.String()),
)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.select(instrument).where(
        instrument.c.instrument_type.in_(("equity", "etf")),
    )).mappings().all()
    corrections = []
    for row in rows:
        policy = row["quote_selection_policy_json"]
        bases = policy.get("total_return") if isinstance(policy, dict) else None
        if (not isinstance(bases, list) or not bases
                or any(basis not in {"adjusted_close", "close", "last"} for basis in bases)):
            raise ValueError(f"Invalid listed return policy for {row['instrument_id']}; repair before migration.")
        if bases != ["adjusted_close"]:
            corrections.append((row["instrument_id"], {**policy, "total_return": ["adjusted_close"]}))
    if corrections:
        # Installation stops writers. Preserve the committed generation order,
        # including an existing clock ahead of this machine's wall clock.
        previous = connection.scalar(sa.select(sa.func.max(instrument.c.calculation_inputs_updated_at)))
        changed_at = datetime.now(UTC)
        if previous:
            parsed = datetime.fromisoformat(previous.replace("Z", "+00:00"))
            changed_at = max(changed_at, parsed.replace(tzinfo=parsed.tzinfo or UTC) + timedelta(microseconds=1))
        watermark = changed_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
        for instrument_id, policy in corrections:
            connection.execute(sa.update(instrument).where(instrument.c.instrument_id == instrument_id).values(
                quote_selection_policy_json=policy, calculation_inputs_updated_at=watermark,
            ))
    if connection.dialect.name == "postgresql":
        op.create_check_constraint(CONSTRAINT, "instrument", POSTGRES_CHECK)
    else:
        # Adding a SQLite table constraint would rebuild the parent table and
        # disrupt existing immutable-ledger triggers. Equivalent row triggers
        # keep migration tests faithful without replacing the table.
        for event in ("INSERT", "UPDATE"):
            expression = SQLITE_CHECK.replace("instrument_type", "NEW.instrument_type").replace(
                "quote_selection_policy_json", "NEW.quote_selection_policy_json")
            op.execute(f"CREATE TRIGGER trg_listed_return_policy_{event.lower()} BEFORE {event} ON instrument "
                       f"WHEN NOT ({expression}) BEGIN SELECT RAISE(ABORT, 'listed_total_return_policy'); END")


def downgrade() -> None:
    # Removing enforcement cannot reconstruct which raw-price fallbacks existed.
    # Retain the corrected economic-return policy rather than restore that bug.
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(CONSTRAINT, "instrument", type_="check")
    else:
        for event in ("insert", "update"):
            op.execute(f"DROP TRIGGER trg_listed_return_policy_{event}")
