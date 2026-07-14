"""Versioned Portfolio Daily producer contracts.

Changing any value in this module changes either the accepted input universe or
the meaning of a published financial result.  Such changes therefore require a
new methodology/input/output version; they are not runtime tuning knobs.
"""

from __future__ import annotations

from datetime import time

from portfolio_ops_instrument_core.canonical_fx import (
    CANONICAL_FX_RATE_PRECISION,
    CANONICAL_FX_RATE_ROUNDING,
    CANONICAL_FX_RESOLVER_STRATEGY_VERSION,
)
from portfolio_ops_instrument_core.quote_resolver import (
    CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION as INSTRUMENT_QUOTE_FRESHNESS_POLICY_VERSION,
    CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION as INSTRUMENT_QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION as INSTRUMENT_QUOTE_SELECTION_POLICY_VERSION,
)


CALCULATION_KIND = "portfolio_daily"
SCOPE_KIND = "portfolio"

METHODOLOGY_VERSION = "portfolio-daily.exact.v1"
INPUT_SCHEMA_VERSION = "portfolio-daily-input.v1"
OUTPUT_SCHEMA_VERSION = "portfolio-daily-output.v1"

CONFIG_SCHEMA_VERSION = "portfolio-daily-config.v1"
ACCOUNT_SCHEMA_VERSION = "portfolio-daily-account.v1"
INSTRUMENT_SCHEMA_VERSION = "portfolio-daily-instrument.v1"
CORPORATE_ACTION_SCHEMA_VERSION = "portfolio-daily-corporate-action.v1"

QUOTE_SELECTION_POLICY_VERSION = INSTRUMENT_QUOTE_SELECTION_POLICY_VERSION
QUOTE_RESOLVER_STRATEGY_VERSION = INSTRUMENT_QUOTE_RESOLVER_STRATEGY_VERSION
QUOTE_FRESHNESS_POLICY_VERSION = INSTRUMENT_QUOTE_FRESHNESS_POLICY_VERSION
VALUATION_QUOTE_CONSUMER_POLICY_VERSION = "portfolio-daily-valuation.v1"

FX_RESOLVER_STRATEGY_VERSION = CANONICAL_FX_RESOLVER_STRATEGY_VERSION
FX_CONSUMER_POLICY_VERSION = "portfolio-daily-fx.v1"
# These fields document inverse-leg division.  Path composition is an exact
# coefficient/exponent product under the resolver strategy version.
FX_RATE_MATH_PRECISION = CANONICAL_FX_RATE_PRECISION
FX_RATE_ROUNDING_MODE = str(CANONICAL_FX_RATE_ROUNDING)

CORPORATE_ACTION_POLICY_VERSION = "confirmed-share-split.v1"

# Portfolio Daily is a calendar-day ledger.  Market calendars determine quote
# availability, but do not erase settlement, accrual, or cash-flow dates.
VALUATION_CALENDAR_ID = "portfolio-daily-calendar-day"
VALUATION_CALENDAR_VERSION = "portfolio-daily-calendar-day.v1"
DEFAULT_VALUATION_CUTOFF_LOCAL_TIME = time(23, 59, 59, 999999)

__all__ = [name for name in globals() if name.isupper()]
