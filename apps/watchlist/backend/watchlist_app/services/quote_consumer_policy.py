from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from portfolio_ops_instrument_core.models import QuoteFreshnessPolicy
from portfolio_ops_instrument_core.quote_resolver import (
    CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION,
)

from watchlist_app.core.settings import get_settings


WATCHLIST_QUOTE_CONSUMER_POLICY_VERSION = "watchlist_quote_consumer.v1"
WATCHLIST_QUOTE_DEPENDENCY_KIND = "watchlist_quote_consumer_dependency"
WATCHLIST_QUOTE_DEPENDENCY_VERSION = "v1"


@dataclass(frozen=True)
class ConsumerFreshnessProfile:
    profile: str
    policy_version: str
    reason_code: str
    canonical_instrument_type: str
    resolver_policy: QuoteFreshnessPolicy

    def payload(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "policy_version": self.policy_version,
            "reason_code": self.reason_code,
            "canonical_instrument_type": self.canonical_instrument_type,
            "resolver_policy": self.resolver_policy.model_dump(mode="json"),
        }


def watchlist_freshness_profile(instrument_type: str) -> ConsumerFreshnessProfile:
    """Select freshness only from canonical instrument type.

    Taxonomy, names, identifiers and observed quote cadence are deliberately not
    inputs to this classification.
    """

    normalized_type = str(instrument_type or "").strip().lower()
    settings = get_settings()
    if normalized_type == "fund":
        profile = "periodic_fund_nav"
        reason_code = "canonical_fund_periodic_publication_window"
        max_age_days = settings.fund_quote_max_age_days
    else:
        profile = "daily_market"
        reason_code = "canonical_nonfund_daily_market_window"
        max_age_days = settings.daily_market_quote_max_age_days
    return ConsumerFreshnessProfile(
        profile=profile,
        policy_version=WATCHLIST_QUOTE_CONSUMER_POLICY_VERSION,
        reason_code=reason_code,
        canonical_instrument_type=normalized_type,
        resolver_policy=QuoteFreshnessPolicy(
            policy_version=CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION,
            mode="calendar_day_carry_forward",
            max_age_days=max_age_days,
        ),
    )


def quote_consumer_dependency(
    *,
    canonical_dependency_fingerprint: str,
    consumer_profile: ConsumerFreshnessProfile,
) -> dict[str, object]:
    profile_payload = consumer_profile.payload()
    dependency_payload = {
        "dependency_kind": WATCHLIST_QUOTE_DEPENDENCY_KIND,
        "dependency_version": WATCHLIST_QUOTE_DEPENDENCY_VERSION,
        "canonical_dependency_fingerprint": canonical_dependency_fingerprint,
        "consumer_freshness_profile": profile_payload,
    }
    encoded = json.dumps(
        dependency_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        **dependency_payload,
        "fingerprint": f"sha256:{sha256(encoded).hexdigest()}",
    }
