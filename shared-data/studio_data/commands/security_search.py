"""Read the current local listing directory without initializing data writers."""
from __future__ import annotations

from investment_studio_instrument_core.security_directory import search_directory
from studio_data.db.session import get_session_factory
from studio_data.services.securities.contracts import SecuritySearchResponse


def search_security_records(q: str, limit: int = 10) -> SecuritySearchResponse:
    if not q.strip():
        raise ValueError("q must not be empty")
    if not 1 <= limit <= 25:
        raise ValueError("limit must be between 1 and 25")
    results, catalog_errors = search_directory(get_session_factory(), q, limit)
    return SecuritySearchResponse.model_validate(
        {"results": results, "catalog_errors": catalog_errors}
    )
