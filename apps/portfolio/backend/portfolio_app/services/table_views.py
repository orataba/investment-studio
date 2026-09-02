from __future__ import annotations

from datetime import UTC, datetime

from portfolio_app.db.models import PortfolioRecordModel, PortfolioTableViewStoreModel
from portfolio_app.db.session import get_session_factory


SUPPORTED_TABLE_VIEW_SCOPES = {
    "holdings",
    "holdings_fcn",
    "holdings_options",
    "performance_calculation",
}


def _current_utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_table_view_scope(view_scope: str) -> str:
    normalized = view_scope.strip()
    if normalized not in SUPPORTED_TABLE_VIEW_SCOPES:
        raise ValueError(f"Unsupported table view scope: {view_scope}")
    return normalized


def get_portfolio_table_view_store(portfolio_id: str, view_scope: str) -> dict[str, object] | None:
    normalized_scope = normalize_table_view_scope(view_scope)
    session_factory = get_session_factory()
    with session_factory() as session:
        row = session.get(PortfolioTableViewStoreModel, (portfolio_id, normalized_scope))
        if row is None:
            return None
        return {
            "portfolio_id": row.portfolio_id,
            "view_scope": row.view_scope,
            "store": row.store_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


def upsert_portfolio_table_view_store(
    portfolio_id: str,
    view_scope: str,
    store: dict[str, object],
) -> dict[str, object]:
    normalized_scope = normalize_table_view_scope(view_scope)
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            raise LookupError("Portfolio not found")

        timestamp = _current_utc_timestamp()
        row = session.get(PortfolioTableViewStoreModel, (portfolio_id, normalized_scope))
        if row is None:
            row = PortfolioTableViewStoreModel(
                portfolio_id=portfolio_id,
                view_scope=normalized_scope,
                store_json=store,
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(row)
        else:
            row.store_json = store
            row.updated_at = timestamp
        session.commit()
        session.refresh(row)
        return {
            "portfolio_id": row.portfolio_id,
            "view_scope": row.view_scope,
            "store": row.store_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
