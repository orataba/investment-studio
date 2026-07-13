"""Seed canonical FX reference identities without inventing market data.

Revision ID: 20260713_0010
Revises: 20260713_0009
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import RowMapping


revision = "20260713_0010"
down_revision = "20260713_0009"
branch_labels = None
depends_on = None


# Frozen migration contract. Do not import runtime FX-universe or policy code here.
FX_POLICY: dict[str, list[str]] = {
    "trading": ["spot"],
    "valuation": ["spot"],
    "total_return": [],
    "chart": ["spot"],
    "reference": ["spot"],
}
FX_REFERENCE_IDENTITIES: tuple[dict[str, str], ...] = (
    {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD Spot",
        "currency": "HKD",
        "ticker": "USDHKD",
    },
    {
        "instrument_id": "fx-usd-cny",
        "instrument_name": "USD/CNY Spot",
        "currency": "CNY",
        "ticker": "USDCNY",
    },
)
POLICY_ROLES = tuple(FX_POLICY)


instrument = sa.table(
    "instrument",
    sa.column("instrument_id", sa.String()),
    sa.column("instrument_name", sa.String()),
    sa.column("instrument_type", sa.String()),
    sa.column("currency", sa.String()),
    sa.column("quote_selection_policy_json", sa.JSON()),
    sa.column("source_settings_json", sa.JSON()),
    sa.column("refresh_status_json", sa.JSON()),
    sa.column("lifecycle_state_json", sa.JSON()),
    sa.column("market_data_updated_at", sa.String()),
)
instrument_identifier = sa.table(
    "instrument_identifier",
    sa.column("instrument_identifier_id", sa.Integer()),
    sa.column("instrument_id", sa.String()),
    sa.column("identifier_type", sa.String()),
    sa.column("identifier_value", sa.String()),
    sa.column("is_primary", sa.Boolean()),
)
registry_metadata = sa.table(
    "registry_metadata",
    sa.column("registry_key", sa.String()),
    sa.column("registry_name", sa.String()),
    sa.column("market_data_updated_at", sa.String()),
)


def _mapping(value: object, *, instrument_id: str, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(
            f"Canonical FX identity conflict: {field_name} must be an object; "
            f"instrument={instrument_id!r}."
        )
    return value


def _validate_existing_instrument(
    row: RowMapping,
    *,
    expected: dict[str, str],
) -> None:
    instrument_id = expected["instrument_id"]
    if row["instrument_type"] != "fx":
        raise RuntimeError(
            "Canonical FX identity conflict: instrument_type must be exactly "
            f"'fx'; instrument={instrument_id!r}, actual={row['instrument_type']!r}."
        )
    if row["currency"] != expected["currency"]:
        raise RuntimeError(
            "Canonical FX identity conflict: quote currency does not match the "
            f"maintained pair; instrument={instrument_id!r}, "
            f"expected={expected['currency']!r}, actual={row['currency']!r}."
        )

    lifecycle = _mapping(
        row["lifecycle_state_json"],
        instrument_id=instrument_id,
        field_name="lifecycle_state_json",
    )
    if lifecycle.get("status") != "active":
        raise RuntimeError(
            "Canonical FX identity conflict: lifecycle status must be exactly "
            f"'active'; instrument={instrument_id!r}, "
            f"actual={lifecycle.get('status')!r}."
        )

    policy = _mapping(
        row["quote_selection_policy_json"],
        instrument_id=instrument_id,
        field_name="quote_selection_policy_json",
    )
    if set(policy) != set(POLICY_ROLES):
        raise RuntimeError(
            "Canonical FX identity conflict: quote-selection policy must contain "
            "exactly the five strict role keys; "
            f"instrument={instrument_id!r}, actual_keys={sorted(policy)!r}."
        )
    for role in POLICY_ROLES:
        if policy.get(role) != FX_POLICY[role]:
            raise RuntimeError(
                "Canonical FX identity conflict: quote-selection role must match "
                f"the strict FX policy; instrument={instrument_id!r}, role={role!r}, "
                f"expected={FX_POLICY[role]!r}, actual={policy.get(role)!r}."
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


def upgrade() -> None:
    connection = op.get_bind()
    expected_by_id = {
        expected["instrument_id"]: expected for expected in FX_REFERENCE_IDENTITIES
    }
    instrument_ids = tuple(expected_by_id)
    rows_by_id = {
        str(row["instrument_id"]): row
        for row in connection.execute(
            sa.select(
                instrument.c.instrument_id,
                instrument.c.instrument_type,
                instrument.c.currency,
                instrument.c.quote_selection_policy_json,
                instrument.c.lifecycle_state_json,
                instrument.c.market_data_updated_at,
            ).where(instrument.c.instrument_id.in_(instrument_ids))
        ).mappings()
    }

    # Complete every preflight before writing anything. A conflicting existing
    # identity must never be silently repaired, and it must not leave the other
    # canonical leg partially inserted.
    for instrument_id, row in rows_by_id.items():
        _validate_existing_instrument(row, expected=expected_by_id[instrument_id])

    identifier_rows = list(
        connection.execute(
            sa.select(
                instrument_identifier.c.instrument_id,
                instrument_identifier.c.identifier_type,
                instrument_identifier.c.identifier_value,
                instrument_identifier.c.is_primary,
            ).where(
                sa.or_(
                    instrument_identifier.c.instrument_id.in_(instrument_ids),
                    sa.and_(
                        instrument_identifier.c.identifier_type == "ticker",
                        instrument_identifier.c.identifier_value.in_(
                            tuple(
                                expected["ticker"]
                                for expected in FX_REFERENCE_IDENTITIES
                            )
                        ),
                    ),
                )
            )
        ).mappings()
    )
    missing_identifiers: list[dict[str, str]] = []
    for expected in FX_REFERENCE_IDENTITIES:
        instrument_id = expected["instrument_id"]
        ticker = expected["ticker"]
        conflicting_primary_identifiers = [
            (row["identifier_type"], row["identifier_value"])
            for row in identifier_rows
            if row["instrument_id"] == instrument_id
            and row["is_primary"] is True
            and not (
                row["identifier_type"] == "ticker"
                and row["identifier_value"] == ticker
            )
        ]
        if conflicting_primary_identifiers:
            raise RuntimeError(
                "Canonical FX identifier conflict: instrument already has a "
                "different primary identifier; "
                f"instrument={instrument_id!r}, expected={ticker!r}, "
                f"actual={conflicting_primary_identifiers!r}."
            )
        expected_rows = [
            row
            for row in identifier_rows
            if row["identifier_type"] == "ticker"
            and row["identifier_value"] == ticker
        ]
        if expected_rows:
            identifier_row = expected_rows[0]
            if (
                identifier_row["instrument_id"] != instrument_id
                or identifier_row["is_primary"] is not True
            ):
                raise RuntimeError(
                    "Canonical FX identifier conflict: primary ticker is already "
                    f"assigned inconsistently; instrument={instrument_id!r}, "
                    f"ticker={ticker!r}, owner={identifier_row['instrument_id']!r}, "
                    f"is_primary={identifier_row['is_primary']!r}."
                )
        else:
            missing_identifiers.append(expected)

    missing_instruments = [
        expected
        for expected in FX_REFERENCE_IDENTITIES
        if expected["instrument_id"] not in rows_by_id
    ]
    if not missing_instruments and not missing_identifiers:
        return

    metadata_row = connection.execute(
        sa.select(
            registry_metadata.c.registry_key,
            registry_metadata.c.market_data_updated_at,
        ).where(registry_metadata.c.registry_key == "shared")
    ).mappings().first()
    watermark_inputs: list[object] = list(
        connection.scalars(sa.select(instrument.c.market_data_updated_at))
    )
    watermark_inputs.append(
        metadata_row["market_data_updated_at"] if metadata_row is not None else None
    )
    changed_at = _next_watermark(watermark_inputs)

    for expected in missing_instruments:
        connection.execute(
            sa.insert(instrument).values(
                instrument_id=expected["instrument_id"],
                instrument_name=expected["instrument_name"],
                instrument_type="fx",
                currency=expected["currency"],
                quote_selection_policy_json=FX_POLICY,
                source_settings_json={},
                refresh_status_json={},
                lifecycle_state_json={"status": "active"},
                market_data_updated_at=changed_at,
            )
        )

    for expected in missing_identifiers:
        connection.execute(
            sa.insert(instrument_identifier).values(
                instrument_id=expected["instrument_id"],
                identifier_type="ticker",
                identifier_value=expected["ticker"],
                is_primary=True,
            )
        )
        if expected["instrument_id"] in rows_by_id:
            connection.execute(
                sa.update(instrument)
                .where(instrument.c.instrument_id == expected["instrument_id"])
                .values(market_data_updated_at=changed_at)
            )

    if metadata_row is None:
        connection.execute(
            sa.insert(registry_metadata).values(
                registry_key="shared",
                registry_name="Portfolio Operations Shared Instruments",
                market_data_updated_at=changed_at,
            )
        )
    else:
        connection.execute(
            sa.update(registry_metadata)
            .where(registry_metadata.c.registry_key == "shared")
            .values(market_data_updated_at=changed_at)
        )


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0010 is irreversible: canonical FX reference identities may "
        "already be referenced by instruments, portfolios, quote series, or "
        "observations, so an automatic downgrade must not delete them."
    )
