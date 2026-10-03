"""HKEX's dated daily quotations, separate from traded OHLC observations."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
import re


class HkexQuotationError(ValueError):
    pass


def daily_quotations_url(day: date) -> str:
    return f"https://www.hkex.com.hk/eng/stat/smstat/dayquot/d{day:%y%m%d}e.htm"


def _amount(value: str) -> str | None:
    if value in {"-", "N/A"}:
        return None
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        raise HkexQuotationError("HKEX quotation has an invalid numeric field") from None
    if not number.is_finite() or number < 0:
        raise HkexQuotationError("HKEX quotation has an invalid numeric field")
    return str(number)


def parse_daily_quotations(body: bytes, *, expected_date: date) -> list[dict]:
    """Require the dated complete quotation section; absence never means no trade.

    The exchange prints each security on two lines: previous close/ask/high/
    shares followed by closing/bid/low/turnover. Suspensions and halts have an
    explicit single-line marker. Dashes remain missing fields, never fake OHLC.
    """
    source = body.decode("iso-8859-1")
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    heading = re.search(r"DATE:\s*(\d{2})\s+([A-Z]{3})\s+(\d{4})", source)
    if not heading or heading[2] not in months:
        raise HkexQuotationError("HKEX quotation date is missing")
    actual = date(int(heading[3]), months.index(heading[2]) + 1, int(heading[1]))
    if actual != expected_date:
        raise HkexQuotationError("HKEX quotation date does not match the requested session")
    section = re.search(
        r'<a\s+name\s*=\s*[\"\']quotations[\"\']\s*>.*?</a>(.*?)'
        r'<a\s+name\s*=\s*[\"\']sales_all[\"\']\s*>', source, re.S | re.I,
    )
    if not section:
        raise HkexQuotationError("HKEX complete quotation section is missing")
    lines = [unescape(re.sub(r"<[^>]+>", "", line)).strip() for line in section[1].splitlines()]
    if not any("PRV.CLO./" in line and "SHARES TRADED/" in line for line in lines):
        raise HkexQuotationError("HKEX quotation columns are unrecognized")
    rows, seen = [], set()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        match = re.match(r"^[*# ]*(\d{1,5})[ #*]+(.+?)\s{2,}([A-Z]{3})\s+(.+)$", line)
        if not match:
            if re.match(r"^\d", line):
                raise HkexQuotationError("HKEX security quotation is malformed")
            continue
        code, name, currency, values = match.groups()
        symbol = f"{int(code):04d}.HK"
        if symbol in seen:
            raise HkexQuotationError("HKEX security code is duplicated")
        seen.add(symbol)
        row = dict(symbol=symbol, date=expected_date, security_code=code.zfill(5),
                   security_name=name, currency=currency, source_url=daily_quotations_url(expected_date),
                   session_status="suspended", official_close=None, previous_close=None,
                   shares_traded=None, turnover=None)
        if values not in {"TRADING SUSPENDED", "TRADING HALTED"}:
            first = values.split()
            second = lines[index].split() if index < len(lines) else []
            if len(first) != 4 or len(second) != 4:
                raise HkexQuotationError("HKEX security quotation is incomplete")
            index += 1
            previous, _ask, high, shares = map(_amount, first)
            close, _bid, low, turnover = map(_amount, second)
            row.update(previous_close=previous, official_close=close, shares_traded=shares, turnover=turnover)
            if first[2:] == ["-", "-"] and second[2:] == ["-", "-"] and close and Decimal(close) > 0:
                row["session_status"] = "no_trade"
            elif shares and Decimal(shares) > 0 and high and low and close:
                row["session_status"] = "traded"
            else:
                row["session_status"] = "unknown"
        rows.append(row)
    if not rows:
        raise HkexQuotationError("HKEX quotation section has no securities")
    return rows
