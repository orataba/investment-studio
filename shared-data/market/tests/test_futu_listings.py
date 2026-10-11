from datetime import datetime, timezone
from io import BytesIO
import re
from zipfile import ZipFile

import openpyxl
import pytest

from studio_market.futu import listings
from studio_market.futu.listings import ListingReferenceError, parse_hkex_listings, parse_nasdaq_listings
from studio_market.numeric.providers.http_client import PublicResponse


HK_HEADER = ["Stock Code", "Name of Securities", "Category", "Sub-Category", "Trading Currency"]
NASDAQ_HEADER = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"
OTHER_HEADER = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol"


def hk_workbook(rows, *, header=None, dimension=None):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "ListOfSecurities"
    sheet.append(["List of Securities"])
    sheet.append(["Updated as at 08/10/2026"])
    sheet.append(header if header is not None else HK_HEADER)
    for row in rows:
        sheet.append(row)
    result = BytesIO()
    workbook.save(result)
    workbook.close()
    if dimension is None:
        return result.getvalue()
    output = BytesIO()
    with ZipFile(result) as source, ZipFile(output, "w") as target:
        for name in source.namelist():
            body = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                body = re.sub(rb'<dimension ref="[^"]+"', f'<dimension ref="{dimension}"'.encode(), body)
            target.writestr(name, body)
    return output.getvalue()


def us_file(rows, *, other=False, complete=True):
    lines = [OTHER_HEADER if other else NASDAQ_HEADER, *rows]
    if complete:
        # The official otherlisted footer currently has one fewer delimiter
        # than its header. It is metadata, not a security record.
        lines.append("File Creation Time: 1007202621:31||||||" if other else "File Creation Time: 1007202621:31|||||||")
    return ("\n".join(lines) + "\n").encode()


def test_hkex_resets_incorrect_sheet_dimension_and_preserves_counter_currency():
    body = hk_workbook([
        ["00001", "CKH HOLDINGS", "Equity", "Equity Securities (Main Board)", "HKD"],
        ["00823", "LINK REIT", "Real Estate Investment Trusts", None, "HKD"],
        ["02822", "CSOP A50 ETF", "Exchange Traded Products", "Exchange Traded Funds", "HKD"],
        ["82822", "CSOP A50 ETF-R", "Exchange Traded Products", "Exchange Traded Funds", "RMB"],
        ["09840", "SPDR GOLD TRT-U", "Exchange Traded Products", "Exchange Traded Funds", "USD"],
        ["07000", "LEVERAGED PRODUCT", "Exchange Traded Products", "Leveraged and Inverse", "HKD"],
        ["00100", "INVESTMENT COMPANY", "Equity", "Investment Companies", "HKD"],
        ["20000", "WARRANT", "Derivative Warrants", None, "HKD"],
        ["04000", "BOND", "Debt Securities", None, "USD"],
        ["08222", "GEM COMPANY", "Equity", "Equity Securities (GEM)", "HKD"],
        ["02840", "SPDR GOLD TRT", "Exchange Traded Products", "Exchange Traded Funds", "HKD"],
    ], dimension="A1:E4")
    rows = {row["symbol"]: row for row in parse_hkex_listings(body)}
    assert set(rows) == {"HK.00001", "HK.02822", "HK.82822", "HK.09840", "HK.08222", "HK.02840"}
    assert rows["HK.00001"]["exchange_ticker"] == "0001.HK"
    assert rows["HK.02822"]["exchange_ticker"] == "2822.HK"
    assert rows["HK.82822"]["exchange_ticker"] == "82822.HK"
    assert rows["HK.82822"]["currency"] == "CNY"
    assert rows["HK.09840"]["currency"] == "USD"
    assert rows["HK.02840"]["futu_stock_type"] == "ETF"
    assert rows["HK.08222"]["instrument_type"] == "equity"
    assert all(row["exchange_code"] == "XHKG" and row["currency_basis"] == "exchange_directory" for row in rows.values())


def test_hkex_refuses_missing_currency_and_duplicate_listing_identity():
    missing_currency = ["02822", "ETF", "Exchange Traded Products", "Exchange Traded Funds", None]
    with pytest.raises(ListingReferenceError, match="trading currency"):
        parse_hkex_listings(hk_workbook([missing_currency]))
    valid = ["02822", "ETF", "Exchange Traded Products", "Exchange Traded Funds", "HKD"]
    with pytest.raises(ListingReferenceError, match="duplicate security"):
        parse_hkex_listings(hk_workbook([valid, valid]))
    with pytest.raises(ListingReferenceError, match="required columns"):
        parse_hkex_listings(hk_workbook([valid], header=HK_HEADER[:-1]))
    with pytest.raises(ListingReferenceError, match="readable XLSX"):
        parse_hkex_listings(b"<html>temporarily unavailable</html>")


def test_nasdaq_identifies_etfs_and_only_unambiguous_common_equity():
    rows = parse_nasdaq_listings(us_file([
        "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N",
        "ADRC|Company Ltd. - American Depositary Shares|Q|N|N|100|N|N",
        "ETFA|Preferred Securities ETF|G|N|N|100|Y|N",
        "PFBC|Preferred Bank - Common Stock|Q|N|N|100|N|N",
        "PREFP|Company - Depositary Shares representing Preferred Stock|Q|N|N|100|N|N",
        "RIGHT|Company Rights to receive Common Stock|S|N|N|100|N|N",
        "ABCW|Company Warrants to purchase Common Stock|S|N|N|100|N|N",
        "UNITU|Company Units each comprising Ordinary Shares and a Warrant|G|N|N|100|N|N",
        "FUND|Closed-End Fund Common Shares|G|N|N|100|N|N",
        "TRST|Income Trust Common Shares|G|N|N|100|N|N",
        "NTRS|Northern Trust Corporation - Common Stock|Q|N|N|100|N|N",
        "NOTE|Issuer Exchange Traded Notes ETN|G|N|N|100|Y|N",
        "NEXT|Managed NextShares|G|N|N|100|Y|Y",
        "TEST|A Test Common Stock|Q|Y|N|100|N|N",
        "UNKN|Company Unspecified Security|Q|N|N|100|N|N",
    ]))
    by_symbol = {row["symbol"]: row for row in rows}
    assert set(by_symbol) == {"US.AAPL", "US.ADRC", "US.ETFA", "US.PFBC", "US.NTRS"}
    assert by_symbol["US.ETFA"]["instrument_type"] == "etf"
    assert by_symbol["US.AAPL"]["futu_stock_type"] == "STOCK"
    assert all(row["exchange_code"] == "XNAS" and row["currency"] == "USD" for row in rows)
    assert all(row["currency_basis"] == "us_listing_convention" for row in rows)


def test_otherlisted_maps_mic_and_preserves_exact_class_symbol_without_guessing():
    rows = parse_nasdaq_listings(us_file([
        "A|Agilent Common Stock|N|A|N|100|N|A",
        "SPY|S&P ETF Trust|P|SPY|Y|100|N|SPY",
        "BATE|Cboe Example ETF|Z|BATE|Y|100|N|BATE",
        "AMER|Company Common Stock|A|AMER|N|100|N|AMER",
        "BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK.B|N|100|N|BRK/B",
        "PREF$A|Company Preferred Stock|N|PREFpA|N|100|N|PREF-A",
        "ABC/WS|Company Warrants|N|ABC WS|N|100|N|ABC.WS",
        "IEX|Company Common Stock|V|IEX|N|100|N|IEX",
    ], other=True), other_listed=True)
    by_symbol = {row["symbol"]: row for row in rows}
    assert {symbol: row["exchange_code"] for symbol, row in by_symbol.items()} == {
        "US.A": "XNYS", "US.SPY": "ARCX", "US.BATE": "BATS", "US.AMER": "XASE", "US.BRK.B": "XNYS",
    }
    assert by_symbol["US.BRK.B"]["exchange_ticker"] == "BRK.B"
    assert by_symbol["US.SPY"]["source_endpoint"] == listings.OTHER_LISTED_URL


@pytest.mark.parametrize("body,match", [
    (us_file(["AAPL|Apple Common Stock|Q|N|N|100|N|N"], complete=False), "empty or incomplete"),
    (us_file(["AAPL|Apple Common Stock|Q|N|N|100|N|N", "MSFT|Microsoft Common Stock|Q|N|N|100|N|N"], complete=False), "completion timestamp"),
    (us_file(["AAPL|Apple Common Stock|Q|N|N|100|X|N"]), "invalid Test Issue or ETF"),
    (us_file(["AAPL|Apple Common Stock|Q|N|N|100|N"]), "malformed row"),
    (us_file(["AAPL|Apple Common Stock|Q|N|N|100|N|N", "AAPL|Apple Common Stock|Q|N|N|100|N|N"]), "duplicate security"),
])
def test_nasdaq_incomplete_or_ambiguous_files_fail_instead_of_publishing_partial_list(body, match):
    with pytest.raises(ListingReferenceError, match=match):
        parse_nasdaq_listings(body)


def test_load_listings_returns_original_captures_without_claiming_futu_coverage(monkeypatch):
    received_at = datetime(2026, 10, 8, tzinfo=timezone.utc)
    bodies = {
        listings.HKEX_LISTINGS_URL: hk_workbook([["02822", "ETF", "Exchange Traded Products", "Exchange Traded Funds", "HKD"]]),
        listings.NASDAQ_LISTED_URL: us_file(["AAPL|Apple Common Stock|Q|N|N|100|N|N"]),
        listings.OTHER_LISTED_URL: us_file(["SPY|S&P ETF Trust|P|SPY|Y|100|N|SPY"], other=True),
    }
    requests = []

    def download(endpoint):
        requests.append(endpoint)
        return PublicResponse(endpoint, {}, received_at, bodies[endpoint])

    monkeypatch.setattr(listings, "get_public_bytes", download)
    rows, captures = listings.load_listings()
    assert requests == list(bodies)
    assert {row["symbol"] for row in rows} == {"HK.02822", "US.AAPL", "US.SPY"}
    assert [(capture.endpoint, capture.params, capture.received_at, capture.body) for capture in captures] == [
        (endpoint, {}, received_at, body) for endpoint, body in bodies.items()
    ]
    assert all("futu_covered" not in row for row in rows)
    with pytest.raises(ValueError, match="hk and/or us"):
        listings.load_listings(("cn",))
