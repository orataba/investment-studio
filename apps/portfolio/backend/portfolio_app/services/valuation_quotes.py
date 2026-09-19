"""Source-validated daily market quotes, without portfolio transaction facts."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import date

from investment_studio_instrument_core import VALUATION_PROHIBITED_TOTAL_RETURN_BASES
from investment_studio_instrument_core.db_models import Instrument
from sqlalchemy import select

from portfolio_app.db.session import get_session_factory
from portfolio_app.services.market_data import quote_policy_bases, resolve_quote_point
from portfolio_app.services.source_cache import get_source_value


def _source_generation(instrument_ids: tuple[str, ...]) -> tuple:
    with get_session_factory()() as session:
        return tuple(tuple(row) for row in session.execute(
            select(Instrument.instrument_id, Instrument.market_data_updated_at,
                   Instrument.calculation_inputs_updated_at)
            .where(Instrument.instrument_id.in_(instrument_ids))
            .order_by(Instrument.instrument_id)
        ))


def load_valuation_quotes(
    instrument_ids: set[str],
    *,
    as_of_date: date,
    detail_loader: Callable,
) -> dict[str, dict[str, object]]:
    ids = tuple(sorted(instrument_ids))

    def source_key():
        return (get_session_factory(), "valuation_quotes", ids, as_of_date,
                detail_loader, _source_generation(ids))

    def build():
        details = detail_loader(ids)
        quotes = {}
        for instrument_id in ids:
            detail = details.get(instrument_id)
            if not isinstance(detail, dict):
                continue
            candidates = quote_policy_bases(detail, "valuation")
            eligible = not any(basis in VALUATION_PROHIBITED_TOTAL_RETURN_BASES for basis in candidates)
            resolution = resolve_quote_point(detail, candidate_bases=candidates, as_of_date=as_of_date) if eligible else None
            # Only the selected quote and its full-history validation result
            # survive this read. Never retain market history or portfolio
            # purchase-price evidence in the cross-request cache.
            quotes[instrument_id] = {
                "detail": {key: deepcopy(detail.get(key)) for key in (
                    "instrument_id", "instrument_type", "currency", "exchange_code",
                    "source_settings", "quote_selection_policy",
                )},
                "point": resolution.point if resolution is not None else None,
                "unavailable_reason": resolution.unavailable_reason if resolution is not None else None,
                "eligible": eligible,
            }
        return quotes

    return deepcopy(get_source_value(source_key=source_key, builder=build))
