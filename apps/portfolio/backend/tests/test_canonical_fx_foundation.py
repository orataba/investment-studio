from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.services import canonical_fx
from portfolio_ops_instrument_core.canonical_fx import CanonicalFxResolverError
from portfolio_ops_instrument_core.models import QuoteFreshnessPolicy


def test_portfolio_fx_policy_is_explicit_versioned_five_calendar_days() -> None:
    policy = canonical_fx.portfolio_fx_freshness_policy()

    assert policy == QuoteFreshnessPolicy(
        policy_version="canonical_quote_freshness.v1",
        mode="calendar_day_carry_forward",
        max_age_days=5,
    )
    assert canonical_fx.PORTFOLIO_FX_CONSUMER_POLICY_VERSION == (
        "portfolio_fx_consumer.v1"
    )


def test_portfolio_fx_window_rejects_any_implicit_policy_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_resolve(session, **kwargs):
        calls.append({"session": session, **kwargs})
        return "locked-book"

    monkeypatch.setattr(
        canonical_fx,
        "resolve_canonical_fx_window_book_in_session",
        fake_resolve,
    )
    session = object()
    correct_policy = canonical_fx.portfolio_fx_freshness_policy()
    result = canonical_fx.resolve_portfolio_fx_window_book_in_session(
        session,  # type: ignore[arg-type]
        currency_pairs=[("HKD", "CNY")],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 13),
        freshness_policy=correct_policy,
    )

    assert result == "locked-book"
    assert calls == [
        {
            "session": session,
            "currency_pairs": [("HKD", "CNY")],
            "start_date": date(2026, 7, 1),
            "end_date": date(2026, 7, 13),
            "freshness_policy": correct_policy,
            "consumer_policy_version": "portfolio_fx_consumer.v1",
        }
    ]

    with pytest.raises(CanonicalFxResolverError) as error:
        canonical_fx.resolve_portfolio_fx_window_book_in_session(
            session,  # type: ignore[arg-type]
            currency_pairs=[("HKD", "CNY")],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 13),
            freshness_policy=QuoteFreshnessPolicy(
                policy_version="canonical_quote_freshness.v1",
                mode="calendar_day_carry_forward",
                max_age_days=6,
            ),
        )
    assert error.value.reason_code == "invalid_portfolio_fx_freshness_policy"
    assert len(calls) == 1
