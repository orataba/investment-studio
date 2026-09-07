from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest

from studio_data.services import sector_reference as service
from studio_data.services.fmp import FmpApiError, FmpClient


@pytest.fixture(autouse=True)
def source_clock(monkeypatch):
    class CollectionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 7, 0, tzinfo=UTC)
    monkeypatch.setattr(service, "datetime", CollectionClock)


def holdings():
    return [{"symbol": "XLK", "asset": symbol, "name": name, "weightPercentage": weight,
             "isin": f"id-{index}", "sharesNumber": 3, "marketValue": 10,
             "updatedAt": "2026-09-04 12:00:00"}
            for index, (symbol, name, weight) in enumerate([
                ("AAA", "Alpha", 99.5), ("", "US DOLLAR", -0.1),
                ("", "SSI US GOV MONEY MARKET CLASS", 0.4),
                ("IXTU6", "XAK TECHNOLOGY SEP26", 0.1),
                ("2602335D", "CONTRA HOLOGIC", 0), ("BBB", "Unresolved holding", 0.1),
            ])]


class Provider:
    def __init__(self, *, annual_error=False):
        self.calls = []
        self.annual_error = annual_error

    @contextmanager
    def independent_session(self):
        yield self

    def profile(self, symbol):
        self.calls.append(("profile", symbol))
        if symbol == "BBB":
            raise FmpApiError("No exact profile for unresolved holding.")
        return {"symbol": symbol, "companyName": "Alpha", "currency": "USD",
                "isEtf": False, "isFund": False, "industry": "Software", "description": "Actual company description."}

    def income_statements(self, symbol, *, limit):
        return [{"symbol": symbol, "date": "2025-12-31", "reportedCurrency": "CNY"}]

    def analyst_estimates(self, symbol, *, period):
        self.calls.append(("estimates", symbol, period))
        if symbol == "BBB":
            return []
        if self.annual_error and period == "annual":
            raise FmpApiError("Annual source unavailable.")
        return [{"symbol": symbol, "date": target, "revenueAvg": 1000, "revenueLow": 900,
                 "revenueHigh": 1100, "epsAvg": 2.5, "netIncomeAvg": 500,
                 "numAnalystsRevenue": 12, "numAnalystsEps": 11}
                for target in (["2027-12-31", "2026-12-31"] if period == "annual" else ["2025-12-31"])]

    def split_adjusted_eod(self, symbol, *, start_date, end_date):
        self.calls.append(("close", symbol, start_date, end_date))
        if symbol == "BBB":
            return []
        return [{"symbol": symbol, "date": "2026-09-04", "close": 100},
                {"symbol": symbol, "date": "2026-09-03", "close": 98}]

    def historical_eod(self, symbol, *, adjusted, start_date, end_date):
        assert adjusted is True
        self.calls.append(("adjusted", symbol, start_date, end_date))
        return [] if symbol == "BBB" else [{"symbol": symbol, "date": "2026-09-03", "adjClose": 95},
                                           {"symbol": symbol, "date": "2026-09-04", "adjClose": 97}]


def collect(client, rows=None):
    return service.collect_sector_market_data("XLK", info={"symbol": "XLK", "name": "Technology ETF"},
                                              holdings=holdings() if rows is None else rows, client=client)


def test_full_holdings_use_asset_for_company_and_keep_non_equity_weights_and_clocks():
    client = Provider()
    raw = holdings()
    original = deepcopy(raw)
    result = collect(client, raw)
    assert raw == original
    json.dumps(result, allow_nan=False)
    assert len(result["holdings"]) == 6
    assert sum(row["weight_percent"] for row in result["holdings"]) == pytest.approx(100)
    assert {row["holding_name"]: row["holding_type"] for row in result["holdings"]} == {
        "Alpha": "equity", "US DOLLAR": "cash", "SSI US GOV MONEY MARKET CLASS": "fund",
        "XAK TECHNOLOGY SEP26": "future", "CONTRA HOLOGIC": "other", "Unresolved holding": "unclassified"}
    assert {call[1] for call in client.calls if call[0] == "profile"} == {"AAA", "BBB"}
    stock = next(row for row in result["holdings"] if row["holding_symbol"] == "AAA")
    assert stock["as_of_date"] is None and stock["snapshot_date"] == "2026-09-07"
    assert stock["provider_updated_at"] == "2026-09-04 12:00:00"
    assert stock["collected_at"] == "2026-09-07T00:00:00+00:00"
    assert stock["company_profile"]["currency"] == "USD"
    estimates = stock["annual_estimates"]
    assert [row["target_period_end"] for row in estimates] == ["2026-12-31", "2027-12-31"]
    assert estimates[0]["revenue_avg"] == 1000 and estimates[0]["eps_avg"] == 2.5
    assert estimates[0]["net_income_avg"] == 500 and estimates[0]["num_analysts_revenue"] == 12
    assert estimates[0]["currency"] == "CNY"
    assert estimates[0]["currency_status"] == "inferred_from_reporting_currency"
    assert stock["company_profile"]["reporting_currency_source"]["source_dataset"] == "fmp_income_statement"
    assert "published_at" not in estimates[0] and len(estimates[0]["raw_sha256"]) == 64
    assert stock["quarterly_estimates"][0]["target_period_end"] == "2025-12-31"
    assert stock["latest_price"]["close"] == 100 and stock["latest_price"]["adjusted_close"] == 97
    assert stock["price_coverage"] == {"first_date": "2026-09-03", "last_date": "2026-09-04", "observations": 2}
    assert "database_path" not in result["source"] and "read_at" not in result["source"]
    assert "30 calendar days" in result["source"]["semantics"]["price_coverage"]
    assert any(row["kind"] == "no_forward_quarter_estimates" and row["symbol"] == "AAA" for row in result["gaps"])


def test_cross_etf_holding_response_is_rejected_before_collecting_companies():
    client = Provider()
    with pytest.raises(ValueError, match="different ETF"):
        collect(client, [{**holdings()[0], "symbol": "XLF"}])
    assert client.calls == []


def test_adjusted_close_must_match_the_latest_close_date():
    class MissingLatestAdjustment(Provider):
        def historical_eod(self, symbol, **kwargs):
            return [{"symbol": symbol, "date": "2026-09-03", "adjClose": 95}]
    result = collect(MissingLatestAdjustment(), holdings()[:1])
    stock = result["holdings"][0]
    assert stock["latest_price"]["date"] == "2026-09-04"
    assert stock["latest_price"]["close"] == 100 and stock["latest_price"]["adjusted_close"] is None


def test_failed_annual_estimates_do_not_discard_successful_sections_or_mislabel_their_status():
    result = collect(Provider(annual_error=True), holdings()[:1])
    stock = result["holdings"][0]
    assert stock["annual_estimates"] == [] and stock["quarterly_estimates"]
    assert stock["company_profile"] and stock["latest_price"]
    failed = [row for row in result["gaps"] if row["kind"] == "collection_failed"]
    assert failed == [{"kind": "collection_failed", "symbol": "AAA", "section": "annual_estimates",
                       "message": "Annual source unavailable."}]
    statuses = {row["dataset"]: row for row in result["dataset_status"]}
    assert statuses["fmp_analyst_estimates"]["status"] == "partial"
    for dataset in ("fmp_etf_current", "fmp_us_company_profiles", "fmp_us_eod"):
        assert statuses[dataset]["status"] == "success" and statuses[dataset]["last_success_at"]


@pytest.mark.parametrize("invalid", [
    {"symbol": "WRONG", "date": "2026-09-04", "close": 888},
    {"symbol": "AAA", "date": "2099-09-04", "close": 888},
])
def test_price_rows_outside_requested_company_or_dates_cannot_become_latest_price(invalid):
    class InvalidPrices(Provider):
        def split_adjusted_eod(self, symbol, **kwargs):
            return [invalid] if symbol == "AAA" else super().split_adjusted_eod(symbol, **kwargs)
    result = collect(InvalidPrices(), holdings()[:1])
    assert result["holdings"][0]["latest_price"] is None
    assert any(row["symbol"] == "AAA" and row["kind"] in {"collection_failed", "missing_price"} for row in result["gaps"])


def test_fmp_client_uses_current_estimate_fields_and_split_adjusted_endpoint(monkeypatch):
    client = object.__new__(FmpClient)
    calls = []
    monkeypatch.setattr(client, "_get_list", lambda endpoint, *, params: calls.append((endpoint, params)) or [])
    assert client.analyst_estimates(" aapl ", period="quarter") == []
    assert client.split_adjusted_eod("AAPL", start_date="2026-08-08", end_date="2026-09-07") == []
    assert calls == [("analyst-estimates", {"symbol": "AAPL", "period": "quarter", "page": 0, "limit": 20}),
                     ("historical-price-eod/full", {"symbol": "AAPL", "from": "2026-08-08", "to": "2026-09-07"})]


def test_reference_refresh_keeps_over_100_holdings_and_normal_sections_alongside_sector_inputs(monkeypatch):
    from studio_data.services import instrument_reference
    rows = [{**holdings()[0], "asset": f"STOCK{index}", "isin": f"isin{index}", "weightPercentage": 100 / 101}
            for index in range(101)]

    class FullEtfProvider(Provider):
        def fund_info(self, symbol):
            return {"symbol": symbol, "name": "Technology ETF", "holdingsCount": 101}

        def fund_holdings(self, symbol):
            return rows

        def fund_sector_weights(self, symbol):
            return [{"sector": "Technology", "weightPercentage": 100}]

        def fund_country_weights(self, symbol):
            return [{"country": "United States", "weightPercentage": 100}]

    monkeypatch.setattr(instrument_reference, "get_instrument", lambda _: {
        "instrument_id": "xlk", "instrument_type": "etf", "source_settings": {"source_api_profile": "fmp"},
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": "fmp:XLK"}],
    })
    result = instrument_reference.get_instrument_reference_data("xlk", client=FullEtfProvider())
    sections = result["sections"]
    assert result["provider"] == "fmp" and result["provider_symbol"] == "XLK"
    assert result["section_errors"] == {}
    assert sections["holdings"] == rows and sections["fund_info"]["holdingsCount"] == 101
    assert sections["sector_weights"] == [{"sector": "Technology", "weightPercentage": 100}]
    assert sections["country_weights"] == [{"country": "United States", "weightPercentage": 100}]
    assert len(sections["sector_market_data"]["holdings"]) == 101
    assert {row["holding_symbol"] for row in sections["sector_market_data"]["holdings"]} == {row["asset"] for row in rows}
    assert sum(row["weight_percent"] for row in sections["sector_market_data"]["holdings"]) == pytest.approx(100)


def test_estimates_for_another_company_are_rejected_without_erasing_other_sources():
    class MixedEstimates(Provider):
        def analyst_estimates(self, symbol, *, period):
            rows = super().analyst_estimates(symbol, period=period)
            return [{**row, "symbol": "WRONG"} for row in rows] if period == "annual" else rows
    result = collect(MixedEstimates(), holdings()[:1])
    stock = result["holdings"][0]
    assert stock["annual_estimates"] == [] and stock["quarterly_estimates"]
    assert stock["company_profile"]["symbol"] == "AAA"
    assert any(row["kind"] == "collection_failed" and row["section"] == "annual_estimates" for row in result["gaps"])
