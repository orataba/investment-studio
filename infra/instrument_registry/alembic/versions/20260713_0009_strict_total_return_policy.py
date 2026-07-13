"""Remove price/NAV fallbacks from the canonical total-return role.

Revision ID: 20260713_0009
Revises: 20260713_0008
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alembic import op
import sqlalchemy as sa


revision = "20260713_0009"
down_revision = "20260713_0008"
branch_labels = None
depends_on = None


# Frozen migration contract. Do not import runtime policy code here.
TRUE_TOTAL_RETURN_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    }
)
VALID_QUOTE_BASES = frozenset(
    {
        "last",
        "close",
        "adjusted_close",
        "official_nav",
        "total_return_nav",
        "cumulative_nav",
        "accumulated_nav",
        "cum_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
        "spot",
        "clean_price",
        "dirty_price",
        "par",
    }
)
POLICY_ROLES = ("trading", "valuation", "total_return", "chart", "reference")
VALUATION_PROHIBITED_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
        "cumulative_nav",
        "accumulated_nav",
        "cum_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    }
)
CASH_CUMULATIVE_NAV_BASES = frozenset(
    {"cumulative_nav", "accumulated_nav", "cum_nav"}
)
STRICT_POLICY_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "fund": {
        "trading": ["last", "close", "official_nav"],
        "valuation": ["official_nav", "close", "last"],
        "total_return": [
            "total_return_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
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
    },
    "etf": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "equity": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "index": {
        "trading": ["close", "last"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "bond": {
        "trading": ["clean_price", "dirty_price"],
        "valuation": ["dirty_price", "clean_price"],
        "total_return": [],
        "chart": ["dirty_price", "clean_price"],
        "reference": ["clean_price", "dirty_price"],
    },
    "cash": {
        "trading": ["par"],
        "valuation": ["par"],
        "total_return": [],
        "chart": ["par"],
        "reference": ["par"],
    },
    "fx": {
        "trading": ["spot"],
        "valuation": ["spot"],
        "total_return": [],
        "chart": ["spot"],
        "reference": ["spot"],
    },
    "other": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
}

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
    sa.column("registry_name", sa.String()),
    sa.column("market_data_updated_at", sa.String()),
)


def _parse_watermark(value: object) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _next_watermark(values: list[object]) -> str:
    candidate = datetime.now(UTC)
    previous = max(
        (parsed for value in values if (parsed := _parse_watermark(value)) is not None),
        default=None,
    )
    if previous is not None and candidate <= previous:
        candidate = previous + timedelta(microseconds=1)
    return candidate.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_policy(*, instrument_id: str, policy: dict[str, object]) -> None:
    missing_roles = [role for role in POLICY_ROLES if role not in policy]
    if missing_roles:
        raise RuntimeError(
            f"Strict quote policy is missing roles {missing_roles!r}; "
            f"instrument={instrument_id!r}."
        )
    invalid_valuation = sorted(
        set(policy["valuation"]).intersection(VALUATION_PROHIBITED_BASES)
    )
    if invalid_valuation:
        raise RuntimeError(
            "Valuation policy contains total-return/cumulative bases "
            f"{invalid_valuation!r}; instrument={instrument_id!r}."
        )
    invalid_chart = sorted(
        set(policy["chart"]).intersection(CASH_CUMULATIVE_NAV_BASES)
    )
    if invalid_chart:
        raise RuntimeError(
            "Chart policy contains cash-cumulative NAV bases "
            f"{invalid_chart!r}; instrument={instrument_id!r}."
        )
    invalid_total_return = sorted(
        set(policy["total_return"]).difference(TRUE_TOTAL_RETURN_BASES)
    )
    if invalid_total_return:
        raise RuntimeError(
            "Total-return policy contains non-total-return bases "
            f"{invalid_total_return!r}; instrument={instrument_id!r}."
        )


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            instrument.c.instrument_id,
            instrument.c.instrument_type,
            instrument.c.quote_selection_policy_json,
            instrument.c.market_data_updated_at,
        )
    ).mappings().all()

    updates: list[tuple[str, dict[str, object]]] = []
    watermark_inputs: list[object] = [
        row["market_data_updated_at"] for row in rows
    ]
    metadata_row = connection.execute(
        sa.select(
            registry_metadata.c.registry_key,
            registry_metadata.c.market_data_updated_at,
        ).where(
            registry_metadata.c.registry_key == "shared"
        )
    ).mappings().first()
    watermark_inputs.append(
        metadata_row["market_data_updated_at"] if metadata_row is not None else None
    )

    for row in rows:
        raw_policy = row["quote_selection_policy_json"]
        if not isinstance(raw_policy, dict):
            raise RuntimeError(
                "quote_selection_policy_json must be an object before strict "
                f"total-return migration; instrument={row['instrument_id']!r}."
            )
        instrument_type = str(row["instrument_type"] or "other").strip().lower()
        defaults = STRICT_POLICY_DEFAULTS.get(
            instrument_type,
            STRICT_POLICY_DEFAULTS["other"],
        )
        policy = dict(raw_policy)
        for role in POLICY_ROLES:
            raw_values = policy.get(role)
            if raw_values is None:
                policy[role] = list(defaults[role])
                continue
            if not isinstance(raw_values, list):
                raise RuntimeError(
                    f"quote_selection_policy.{role} must be a list before strict "
                    f"migration; instrument={row['instrument_id']!r}."
                )
            normalized: list[str] = []
            for raw_basis in raw_values:
                basis = str(raw_basis or "").strip().lower()
                if basis not in VALID_QUOTE_BASES:
                    raise RuntimeError(
                        f"quote_selection_policy.{role} contains unsupported basis "
                        f"{basis!r}; instrument={row['instrument_id']!r}."
                    )
                if basis in normalized:
                    raise RuntimeError(
                        f"quote_selection_policy.{role} contains duplicate basis "
                        f"{basis!r}; instrument={row['instrument_id']!r}."
                    )
                normalized.append(basis)
            policy[role] = normalized

        if instrument_type in {"etf", "equity", "index"}:
            policy["total_return"] = ["adjusted_close"]
        else:
            policy["total_return"] = [
                basis
                for basis in policy["total_return"]
                if basis in TRUE_TOTAL_RETURN_BASES
            ]
        _validate_policy(
            instrument_id=str(row["instrument_id"]),
            policy=policy,
        )
        if policy == raw_policy:
            continue
        updates.append((str(row["instrument_id"]), policy))

    if not updates:
        return

    watermark = _next_watermark(watermark_inputs)
    for instrument_id, policy in updates:
        connection.execute(
            sa.update(instrument)
            .where(instrument.c.instrument_id == instrument_id)
            .values(
                quote_selection_policy_json=policy,
                market_data_updated_at=watermark,
            )
        )
    if metadata_row is None:
        connection.execute(
            sa.insert(registry_metadata).values(
                registry_key="shared",
                registry_name="Portfolio Operations Shared Instruments",
                market_data_updated_at=watermark,
            )
        )
    else:
        connection.execute(
            sa.update(registry_metadata)
            .where(registry_metadata.c.registry_key == "shared")
            .values(market_data_updated_at=watermark)
        )


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0009 is irreversible: removed total-return fallbacks cannot be "
        "reconstructed without inventing policy intent."
    )
