"""Normalize FCN terms into master and per-underlying facts.

Revision ID: 20260816_0051
Revises: 20260812_0050
"""

from __future__ import annotations

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260816_0051"
down_revision: str | None = "20260812_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load_terms(raw: object, contract_id: str) -> dict[str, object]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"FCN contract {contract_id} has invalid terms_json."
            ) from error
    if not isinstance(raw, dict):
        raise RuntimeError(f"FCN contract {contract_id} terms_json must be an object.")
    return raw


def _normalize_fcn_terms(terms: dict[str, object], contract_id: str) -> dict[str, object]:
    barrier_type = str(terms.get("barrier_type") or "none")
    if barrier_type == "dual":
        raise RuntimeError(
            f"FCN contract {contract_id} uses the ambiguous legacy dual barrier. "
            "Resolve its knock-in and knock-out levels before migrating."
        )
    if barrier_type not in {"none", "knock_in", "knock_out"}:
        raise RuntimeError(
            f"FCN contract {contract_id} has unsupported barrier_type {barrier_type!r}."
        )

    raw_underlyings = terms.get("underlying_instrument_ids")
    raw_deliverables = terms.get("deliverable_instrument_ids") or []
    if not isinstance(raw_underlyings, list) or not raw_underlyings:
        raise RuntimeError(f"FCN contract {contract_id} must have at least one underlying.")
    if not isinstance(raw_deliverables, list):
        raise RuntimeError(
            f"FCN contract {contract_id} deliverable_instrument_ids must be an array."
        )

    underlying_ids = [str(value).strip() for value in raw_underlyings]
    if any(not value for value in underlying_ids) or len(underlying_ids) != len(
        set(underlying_ids)
    ):
        raise RuntimeError(
            f"FCN contract {contract_id} underlying ids must be non-empty and unique."
        )
    deliverable_ids = {str(value).strip() for value in raw_deliverables}
    if not deliverable_ids.issubset(set(underlying_ids)):
        raise RuntimeError(
            f"FCN contract {contract_id} has a deliverable that is not an underlying."
        )

    barrier_level = terms.get("barrier_level")
    if barrier_type != "none" and barrier_level is None:
        raise RuntimeError(
            f"FCN contract {contract_id} is missing its active barrier level."
        )

    underlyings: list[dict[str, object]] = []
    for instrument_id in underlying_ids:
        underlying: dict[str, object] = {
            "instrument_id": instrument_id,
            "initial_reference_price": None,
            "strike_level_pct": None,
            "knock_in_level_pct": None,
            "knock_out_level_pct": None,
            "deliverable": instrument_id in deliverable_ids,
        }
        if barrier_type == "knock_in":
            underlying["knock_in_level_pct"] = barrier_level
        elif barrier_type == "knock_out":
            underlying["knock_out_level_pct"] = barrier_level
        underlyings.append(underlying)

    return {
        "notional": terms.get("notional"),
        "annual_coupon_rate_pct": None,
        "issue_date": terms.get("issue_date"),
        "final_observation_date": None,
        "maturity_date": terms.get("maturity_date"),
        "issuer": terms.get("issuer"),
        "counterparty": terms.get("counterparty"),
        "underlyings": underlyings,
    }


def upgrade() -> None:
    connection = op.get_bind()
    contracts = sa.table(
        "derivative_contract_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("derivative_contract_id", sa.String()),
        sa.column("contract_type", sa.String()),
        sa.column("terms_json", sa.JSON()),
    )
    rows = connection.execute(
        sa.select(
            contracts.c.portfolio_id,
            contracts.c.derivative_contract_id,
            contracts.c.terms_json,
        ).where(contracts.c.contract_type == "fcn")
    ).mappings()
    for row in rows:
        contract_id = str(row["derivative_contract_id"])
        normalized = _normalize_fcn_terms(
            _load_terms(row["terms_json"], contract_id),
            contract_id,
        )
        connection.execute(
            sa.update(contracts)
            .where(
                contracts.c.portfolio_id == row["portfolio_id"],
                contracts.c.derivative_contract_id == row["derivative_contract_id"],
            )
            .values(terms_json=normalized)
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260816_0051 removes ambiguous shared FCN barrier fields. "
        "Restore the pre-migration database backup instead of downgrading."
    )
