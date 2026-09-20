"""Durable latest-page projections, identified by the complete input generation.

Only the background publisher writes these disposable results. Financial facts,
permissions and live operational overlays are never owned by this module.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from portfolio_app.db.models import PortfolioRecordModel, PortfolioWorkspaceReadModel
from portfolio_app.db.session import get_session_factory


WORKSPACE_ANALYSIS_VERSION = 1
PUBLISHED_SURFACES = frozenset({"holdings_analytics", "portfolio_risk_basis"})


def serialize_source_key(key: tuple) -> str:
    return json.dumps((WORKSPACE_ANALYSIS_VERSION, *key), sort_keys=True, separators=(",", ":"))


def read_workspace_projection(portfolio_id: str, surface: str, source_key: tuple) -> dict | None:
    with get_session_factory()() as session:
        # Do not transfer/deserialize a large obsolete payload just to reject it.
        return session.scalar(select(PortfolioWorkspaceReadModel.payload_json).where(
            PortfolioWorkspaceReadModel.portfolio_id == portfolio_id,
            PortfolioWorkspaceReadModel.surface == surface,
            PortfolioWorkspaceReadModel.source_key == serialize_source_key(source_key),
            PortfolioWorkspaceReadModel.error_type.is_(None),
        ))


def publish_workspace_projection(
    portfolio_id: str,
    surface: str,
    source_key: tuple,
    *,
    current_source_key: Callable[[], tuple | None],
    payload: dict | None,
    error_type: str | None = None,
) -> bool:
    """An old builder cannot replace a projection for newer facts or settings."""
    encoded_payload = jsonable_encoder(payload) if payload is not None else None
    with get_session_factory()() as session:
        portfolio = session.scalar(select(PortfolioRecordModel).where(
            PortfolioRecordModel.portfolio_id == portfolio_id,
        ).with_for_update())
        if portfolio is None or current_source_key() != source_key:
            return False
        row = session.get(PortfolioWorkspaceReadModel, (portfolio_id, surface))
        serialized_key = serialize_source_key(source_key)
        if (error_type is not None and row is not None
                and row.source_key == serialized_key and row.error_type is None
                and row.payload_json is not None):
            # Another process may have completed the same generation while
            # this builder failed. A failure cannot erase its valid result.
            return False
        if row is None:
            row = PortfolioWorkspaceReadModel(portfolio_id=portfolio_id, surface=surface)
            session.add(row)
        row.source_key = serialized_key
        row.payload_json = encoded_payload
        row.error_type = error_type
        row.calculated_at = datetime.now(UTC).isoformat()
        session.commit()
    return True
