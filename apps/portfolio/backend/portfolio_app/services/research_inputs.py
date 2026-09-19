"""Capture the current configuration once for all dates in a Research run."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
import hashlib
import json

from sqlalchemy import select

from portfolio_app.db.models import PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.analytics_scope import (
    _serialize_policy,
    current_analytics_policies_by_node,
    current_taxonomy_configuration_in_session,
    resolve_configuration_analytics_scopes,
)
from portfolio_app.services.research_eligibility import contract_only_instrument_ids
from portfolio_app.services.valuation_clock import portfolio_valuation_today


CURRENT_TARGET_CONFIGURATION = "current_snapshot"


def capture_current_target_configuration(
    portfolio_id: str,
    taxonomy_id: str,
    *,
    session=None,
) -> dict[str, object]:
    """Freeze the full current tree, targets and eligibility with a stable identity."""
    with nullcontext(session) if session is not None else get_session_factory()() as reader:
        # Writers lock this portfolio first. One shared lock covers the complete
        # multi-table capture, so a snapshot cannot contain halves of two edits.
        portfolio = reader.scalar(
            select(PortfolioRecordModel)
            .where(PortfolioRecordModel.portfolio_id == portfolio_id)
            .with_for_update(read=True)
        )
        if portfolio is None:
            raise ValueError("Portfolio not found.")
        configuration = current_taxonomy_configuration_in_session(reader, portfolio_id, taxonomy_id)
        if configuration is None:
            raise ValueError("Planning taxonomy not found.")
        configuration["base_currency"] = portfolio.base_currency
        policies = current_analytics_policies_by_node(
            reader, portfolio_id=portfolio_id, taxonomy_id=taxonomy_id,
        )
        instrument_ids = sorted({
            str(item["target_entity_id"])
            for item in configuration["taxonomy_assignments"]
            if item.get("status") == "active" and item.get("target_scope") == "instrument"
        })
        active_target_ids = {
            item["target_set_id"] for item in configuration["target_sets"]
            if item.get("status") == "active"
        }
        explicit_members = {
            str(item["target_member_id"]) for item in configuration["target_set_lines"]
            if item.get("target_member_type") == "instrument"
            and item.get("target_set_id") in active_target_ids
        }
        # Current model membership is independent of a pinned data cutoff and
        # remains fixed across historical rebalances. Actual derivative capital
        # continues to follow its dated ledger events in the simulation.
        configuration["contract_only_instrument_ids"] = sorted(contract_only_instrument_ids(
            portfolio_id,
            as_of_date=portfolio_valuation_today(portfolio.valuation_timezone),
            explicitly_selected=explicit_members,
            session=reader,
        ))
        configuration["analytics_scope_policies"] = [_serialize_policy(policies[key]) for key in sorted(policies)]
        configuration["instrument_analytics_scopes"] = resolve_configuration_analytics_scopes(
            configuration, policies, instrument_ids=instrument_ids, taxonomy_id=taxonomy_id,
        )
        configuration["target_configuration"] = CURRENT_TARGET_CONFIGURATION
        configuration["snapshot_schema_version"] = 1
        canonical = json.dumps(configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        configuration["target_snapshot_fingerprint"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        # Capture time is evidence, not configuration identity.
        configuration["captured_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return configuration
