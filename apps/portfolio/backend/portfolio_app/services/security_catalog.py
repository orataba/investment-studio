"""Portfolio import surface for the shared data-owner securities bridge."""
from investment_studio_instrument_core.security_catalog import (
    SecurityCatalogError,
    SecurityMaterializeRequest,
    SecuritySearchResponse,
    SecuritySearchResult,
    materialize_catalog_security,
    search_catalog as _search_catalog,
)

__all__ = [
    "SecurityCatalogError", "SecurityMaterializeRequest", "SecuritySearchResponse",
    "SecuritySearchResult", "materialize_catalog_security", "search_catalog",
]


def search_catalog(query: str, limit: int) -> SecuritySearchResponse:
    from portfolio_app.db.session import get_session_factory
    return _search_catalog(query, limit, session_factory=get_session_factory())
