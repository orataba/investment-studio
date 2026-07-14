from __future__ import annotations

from decimal import Decimal

import pytest

from portfolio_app.calculations.numeric import CalculationNumericError
from portfolio_app.calculations.portfolio_daily.input_policy import (
    resolve_instrument_freshness_policy,
    resolve_instrument_valuation_contract,
)


pytestmark = pytest.mark.no_database


@pytest.mark.parametrize("instrument_type", ["equity", "fund", "etf"])
def test_per_unit_assets_use_explicit_methodology_factors(
    instrument_type: str,
) -> None:
    contract = resolve_instrument_valuation_contract(
        instrument_type=instrument_type,
        source_settings=None,
    )
    assert contract.available
    assert contract.contract_multiplier == Decimal("1")
    assert contract.price_factor == Decimal("1")
    assert contract.price_unit == "per_unit"
    assert contract.source == "methodology"


def test_bond_quote_units_are_never_guessed() -> None:
    missing = resolve_instrument_valuation_contract(
        instrument_type="bond",
        source_settings={},
    )
    assert not missing.available
    assert missing.reason_codes == (
        "missing_accrual_convention",
        "missing_contract_multiplier",
        "missing_price_factor",
        "missing_price_unit",
    )

    explicit = resolve_instrument_valuation_contract(
        instrument_type="bond",
        source_settings={
            "valuation_contract": {
                "price_unit": "percent_of_par",
                "contract_multiplier": "1",
                "price_factor": "0.01",
                "accrual_convention": "dirty_price",
            }
        },
    )
    assert explicit.available
    assert explicit.contract_multiplier == Decimal("1")
    assert explicit.price_factor == Decimal("0.01")
    assert explicit.price_unit == "percent_of_par"
    assert explicit.source == "instrument"


def test_contract_settings_reject_binary_float() -> None:
    with pytest.raises(CalculationNumericError, match="must not be a float"):
        resolve_instrument_valuation_contract(
            instrument_type="bond",
            source_settings={
                "valuation_contract": {
                    "contract_multiplier": 1.0,
                    "price_factor": "0.01",
                    "accrual_convention": "dirty_price",
                }
            },
        )


def test_funds_and_exchange_traded_assets_have_distinct_freshness() -> None:
    fund = resolve_instrument_freshness_policy(
        instrument_type="fund",
        daily_market_max_age_days=5,
        fund_max_age_days=45,
    )
    etf = resolve_instrument_freshness_policy(
        instrument_type="etf",
        daily_market_max_age_days=5,
        fund_max_age_days=45,
    )
    assert fund.max_age_days == 45
    assert etf.max_age_days == 5
