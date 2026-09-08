from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


_PRODUCT = re.compile(r"^[A-Z]{1,3}$")
_CONTRACT = re.compile(r"^(?P<product>[A-Z]{1,3})(?P<delivery>\d{3,4})$")
_DCE_GTJA_NON_CONTRACT_PRODUCT = re.compile(r"^[A-Z]{1,3}_F$")
_SYNTHETIC_SUFFIX = re.compile(
    r"(?:88A2|88A3|888|889|88|99)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContractIdentity:
    delivery_year: int
    delivery_month: int
    economic_contract_id: str


def is_dce_gtja_non_contract_product(
    exchange_id: str,
    product_id: str,
) -> bool:
    """Return whether a GTJA DCE ``*_F`` product is non-contract evidence.

    These provider records have distinct prices and open interest from the
    exchange-deliverable contract.  They stay in the owned raw archive but must
    not be renamed into a real DCE product or published as canonical facts.
    """

    return (
        exchange_id.strip().upper() == "DCE"
        and _DCE_GTJA_NON_CONTRACT_PRODUCT.fullmatch(
            product_id.strip().upper()
        )
        is not None
    )


def parse_contract_identity(
    exchange_id: str,
    product_id: str,
    instrument_id: str,
    trading_day: date,
) -> ContractIdentity:
    """Normalize a real China-futures contract into an economic identity.

    Four-digit delivery codes are ``YYMM``. Three-digit Zhengzhou-style
    codes are ``YMM`` and are resolved to the nearest delivery month that is
    not earlier than the trading month.
    """

    if not isinstance(trading_day, date):
        raise TypeError("trading_day must be a date")

    exchange = exchange_id.strip().upper()
    product = product_id.strip().upper()
    instrument = instrument_id.strip().upper()
    if not exchange:
        raise ValueError("exchange_id must not be empty")
    if _PRODUCT.fullmatch(product) is None:
        raise ValueError("product_id must contain one to three ASCII letters")
    if _SYNTHETIC_SUFFIX.search(instrument):
        raise ValueError("synthetic instrument_id is not a real contract")

    match = _CONTRACT.fullmatch(instrument)
    if match is None:
        raise ValueError(
            "instrument_id must contain a one-to-three-letter product and "
            "a three- or four-digit delivery code"
        )
    if match.group("product") != product:
        raise ValueError("instrument_id product disagrees with product_id")

    delivery = match.group("delivery")
    month = int(delivery[-2:])
    if month < 1 or month > 12:
        raise ValueError("delivery month must be between 01 and 12")

    if len(delivery) == 4:
        year = 2000 + int(delivery[:2])
    else:
        year_digit = int(delivery[0])
        year = (trading_day.year // 10) * 10 + year_digit
        if (year, month) < (trading_day.year, trading_day.month):
            year += 10

    return ContractIdentity(
        delivery_year=year,
        delivery_month=month,
        economic_contract_id=f"{exchange}|{product}|{year:04d}{month:02d}",
    )
