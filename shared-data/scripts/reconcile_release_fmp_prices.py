#!/usr/bin/env python3
"""Replay an explicitly reviewed FMP scope after backup and before derived rebuilds.

This is an offline canonical projection: no provider collection or immutable
object writes. The installer owns the stopped-writer and rollback boundaries.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from investment_studio_instrument_core.db_models import Instrument, InstrumentIdentifier, InstrumentMarketData, InstrumentPriceBar
from studio_data.db.session import get_session_factory
from studio_data.services.fmp.eod import refresh_fmp_eod
from studio_market.config import MarketSettings
from studio_market.numeric.store import NumericStore


def read_scope(path: Path) -> tuple[datetime, list[str], str]:
    raw = path.read_bytes()
    scope = json.loads(raw)
    clock = datetime.fromisoformat(scope['clock'].replace('Z', '+00:00'))
    if clock.tzinfo is None or clock.utcoffset() != timedelta(0):
        raise ValueError('Release FMP scope clock must be an explicit UTC instant.')
    ids = scope['instrument_ids']
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in ids):
        raise ValueError('Release FMP scope requires explicit nonempty instrument IDs.')
    if len(set(ids)) != len(ids):
        raise ValueError('Release FMP scope contains duplicate instrument IDs.')
    return clock, ids, hashlib.sha256(raw).hexdigest()


def reviewed_targets(ids: list[str]) -> list[tuple[str, str]]:
    targets = []
    # Validate the complete scope before the first replay. The publisher also
    # checks source ownership under its write lock, guarding configuration drift.
    with get_session_factory()() as session:
        for iid in ids:
            instrument = session.get(Instrument, iid)
            if instrument is None or instrument.instrument_type not in {'equity', 'etf', 'index'}:
                raise ValueError(f'{iid}: unsupported or missing registered FMP instrument.')
            profile = str((instrument.source_settings_json or {}).get('source_api_profile') or '').strip().lower()
            symbols = list(session.scalars(select(InstrumentIdentifier.identifier_value).where(
                InstrumentIdentifier.instrument_id == iid,
                InstrumentIdentifier.identifier_type == 'provider_symbol',
                InstrumentIdentifier.identifier_value.like('fmp:%'))))
            if profile not in {'', 'fmp'} or len(symbols) != 1 or not symbols[0].removeprefix('fmp:'):
                raise ValueError(f'{iid}: current source ownership is not unambiguously FMP.')
            for table in (InstrumentMarketData, InstrumentPriceBar):
                providers = session.scalars(select(table.provider).where(table.instrument_id == iid).distinct())
                if any(not str(provider or '').startswith('fmp:') for provider in providers):
                    raise ValueError(f'{iid}: non-FMP canonical prices require a separate reviewed source migration.')
            targets.append((iid, instrument.instrument_type))
    return targets


def reconcile(scope_path: Path) -> dict:
    clock, ids, checksum = read_scope(scope_path)
    targets = reviewed_targets(ids)
    completed = []
    market = NumericStore(MarketSettings.from_environment())
    try:
        for iid, kind in targets:
            result = refresh_fmp_eod(iid, instrument_type=kind, store=market, acquire_history=False, now=clock)
            status = dict(result.get('refresh_status') or {}).get('status')
            if status not in {'refreshed', 'no_new_data'}:
                raise RuntimeError(f'{iid}: offline FMP projection did not complete ({status}).')
            # An empty unbound history also returns no_new_data; never accept it
            # as a repair of existing canonical rows without a complete replay.
            if result.get('projection_complete') is not True:
                raise RuntimeError(f'{iid}: no immutable full history is available for release replay.')
            completed.append({'instrument_id': iid, 'instrument_type': kind, 'status': status})
            print(json.dumps({'stage': 'fmp-canonical-replay', **completed[-1]}), flush=True)
    finally:
        market.close()
    return {'scope_sha256': checksum, 'clock': clock.isoformat(), 'completed': completed, 'offline': True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope-file', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(reconcile(args.scope_file)), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
