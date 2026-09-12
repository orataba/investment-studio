#!/usr/bin/env python3
"""Reconcile system Watchlists from the shared registry before the release gate.

Run with managed database writers stopped, after migrations and catalog refresh.
This reuses normal directory membership and row materialization; it does not
refresh market data, calculate new financial values, or alter user lists.
"""

from watchlist_app.api.routes.watchlists import _sync_system_watchlist
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.watchlists import SYSTEM_WATCHLIST_SPECS


def main() -> int:
    with get_session_factory()() as session, session.begin():
        for spec in SYSTEM_WATCHLIST_SPECS:
            # A release cannot use the UI's temporarily retained directory when
            # registry access or materialization fails. Roll back all four lists.
            _sync_system_watchlist(session, spec=spec, raise_on_registry_error=True)
    print(f"Release Watchlist reconciliation completed: {len(SYSTEM_WATCHLIST_SPECS)} system lists.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
