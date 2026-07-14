from __future__ import annotations

from datetime import date
from decimal import Decimal

from portfolio_app.calculations.numeric import PRICE_SCALE, quantize_decimal
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.canonical_quotes import resolve_single_role_quote_in_session


def get_execution_quote(
    instrument_id: str,
    *,
    as_of_date: date,
) -> dict[str, object] | None:
    """Resolve the canonical same-date trading reference for transaction entry.

    Execution assistance intentionally uses ``exact_only``.  A previous close
    remains visible elsewhere as market history, but is not silently proposed
    as the execution price for a different business date.
    """

    session_factory = get_session_factory()
    with session_factory() as session:
        payload = resolve_single_role_quote_in_session(
            session,
            instrument_id=instrument_id,
            role="trading",
            as_of_date=as_of_date,
        )
    if payload is None:
        return None
    source_value = payload.get("value")
    if source_value is not None and not isinstance(source_value, Decimal):
        raise TypeError("canonical execution quote value must be Decimal")
    suggested_price = (
        quantize_decimal(
            source_value,
            scale=PRICE_SCALE,
            field_name="suggested transaction price",
        )
        if source_value is not None
        else None
    )
    return {
        "instrument_id": str(payload["instrument_id"]),
        "requested_as_of_date": as_of_date,
        "selection_role": payload.get("role"),
        "value": source_value,
        "suggested_transaction_price": suggested_price,
        "suggested_transaction_price_scale": PRICE_SCALE,
        "suggested_transaction_price_rounding": "ROUND_HALF_EVEN",
        "suggested_transaction_price_was_rounded": (
            source_value != suggested_price if source_value is not None else False
        ),
        "quote_date": payload.get("as_of_date"),
        "quote_basis": payload.get("quote_basis"),
        "metric_family": payload.get("metric_family"),
        "currency": payload.get("currency"),
        "source_ref": payload.get("source_ref"),
        "source_status": payload.get("source_status"),
        "status": payload.get("status"),
        "resolution_status": payload.get("resolution_status"),
        "freshness_status": payload.get("freshness_status"),
        "ingestion_status": payload.get("ingestion_status"),
        "reliability_status": payload.get("reliability_status"),
        "reason_codes": list(payload.get("reason_codes") or []),
        "stale": bool(payload.get("stale")),
        "carry_forward": bool(payload.get("carry_forward")),
        "age_days": payload.get("age_days"),
        "quote_selection_policy_version": payload.get(
            "quote_selection_policy_version"
        ),
        "quote_selection_policy_revision": payload.get(
            "quote_selection_policy_revision"
        ),
        "quote_series_id": payload.get("quote_series_id"),
        "observation_id": payload.get("observation_id"),
        "revision_id": payload.get("revision_id"),
        "revision_number": payload.get("revision_number"),
        "payload_hash": payload.get("payload_hash"),
        "source_published_at": payload.get("source_published_at"),
        "ingested_at": payload.get("ingested_at"),
        "ingestion_time_state": payload.get("ingestion_time_state"),
        "calculation_dependency": payload.get("calculation_dependency"),
    }


__all__ = ["get_execution_quote"]
