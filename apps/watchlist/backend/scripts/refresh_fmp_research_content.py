#!/usr/bin/env python3
"""Export the current public research scope and refresh its FMP source material.

No model, research publication, portfolio write or financial calculation runs.
The shared collector receives public symbols and security types only.
"""
from datetime import UTC, datetime
import argparse
import json
import os

from studio_identity import principal_context, service_principal
from studio_market.config import MarketSettings
from studio_market.pipeline import run
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_fmp_scope import build_fmp_research_scope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope-only', action='store_true')
    args = parser.parse_args(argv)
    if not args.scope_only and os.environ.get('INVESTMENT_STUDIO_MARKET_ROLE') != 'collector':
        raise ValueError('Research content acquisition runs only on the configured collector')
    now = datetime.now(UTC)
    with principal_context(service_principal('watchlist')):
        with get_session_factory()() as session:
            scope = build_fmp_research_scope(session, as_of=now)
    if args.scope_only:
        print(json.dumps(scope, ensure_ascii=False, default=str))
        return 0
    result = run(MarketSettings.from_environment(), 'research-content', now=now,
                 research_subjects=scope['subjects'])
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 75 if result['status'] == 'already_running' else 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
