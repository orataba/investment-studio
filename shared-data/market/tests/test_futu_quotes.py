from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from studio_market.futu.quotes import (
    FutuQuoteClient,
    FutuQuoteCoverageError,
    FutuQuoteError,
    validate_symbol,
)


class Response:
    def __init__(self, data=None, *, status=200, headers=None, raw=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = raw if raw is not None else json.dumps(data).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


def success(data):
    return Response({"ret_code": 0, "ret_msg": "success", "data": data})


def bar(day="2026-09-30", *, symbol="HK.02822", **changes):
    zone = ZoneInfo("Asia/Hong_Kong" if symbol.startswith("HK.") else "America/New_York")
    row = {
        "time_key": int(datetime.fromisoformat(day).replace(tzinfo=zone).timestamp() * 1000),
        "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
        "volume": 100, "turnover": 1100.0,
    }
    row.update(changes)
    return row


def client_for(responses, token_provider=lambda **kw: "PRIVATE-BEARER"):
    session = Session(responses)
    waits = []
    client = FutuQuoteClient(token_provider, session=session, sleep=waits.append)
    return client, session, waits


def test_daily_history_retains_raw_evidence_and_explicit_unadjusted_contract():
    response = success({"kline_list": [bar()]})
    client, session, _ = client_for([response])
    result = client.history("HK.02822", date(2026, 9, 1), date(2026, 9, 30))
    assert result.rows == ({"symbol": "HK.02822", "date": "2026-09-30", "open": 10,
                            "high": 12, "low": 9, "close": 11, "volume": 100,
                            "volume_unit": "shares", "turnover": 1100, "ohlc_quality": "valid"},)
    assert result.captures[0].body == response.body
    assert result.captures[0].received_at.tzinfo is not None
    assert "PRIVATE-BEARER" not in repr(result)
    method, endpoint, options = session.calls[0]
    assert method == "GET"
    assert endpoint == "https://webapi.futunn.com/api/v1.0/quote/HK.02822/history-kline"
    assert options["params"] == {"start": "2026-09-01", "end": "2026-09-30",
                                  "ktype": 2, "autype": 0, "num": 370, "extended_time": 0}
    assert options["allow_redirects"] is False
    assert options["headers"]["Authorization"] == "Bearer PRIVATE-BEARER"
    assert session.trust_env is False and response.closed
    client.close()
    assert session.closed


def test_us_dates_follow_new_york_dst_not_a_fixed_offset():
    # The summer daily timestamp is 04:00 UTC; fixed UTC-5 gives the wrong day.
    client, _, _ = client_for([success({"kline_list": [
        bar("2026-07-01", symbol="US.SPY", date=20260701),
        bar("2026-01-02", symbol="US.SPY", date=20260102),
    ]})])
    result = client.history("US.SPY", date(2026, 1, 1), date(2026, 12, 31))
    assert [row["date"] for row in result.rows] == ["2026-01-02", "2026-07-01"]


def test_long_history_uses_nonoverlapping_date_windows_and_keeps_empty_captures():
    client, session, _ = client_for([
        success({"kline_list": [bar("2024-12-30")]}),
        success({"kline_list": []}),
    ])
    result = client.history("HK.02822", date(2024, 1, 1), date(2024, 12, 31))
    assert len(result.captures) == 2 and len(result.rows) == 1
    assert [(call[2]["params"]["start"], call[2]["params"]["end"]) for call in session.calls] == [
        ("2024-01-01", "2024-12-30"), ("2024-12-31", "2024-12-31"),
    ]


def test_volume_precision_is_restored_before_returning_shares():
    client, _, _ = client_for([success({"kline_list": [bar(volume=12345)], "volume_precision": 2})])
    result = client.history("HK.02822", date(2026, 9, 30), date(2026, 9, 30))
    assert result.rows[0]["volume"] == 123.45


@pytest.mark.parametrize("changes", [
    {"date": 20260929}, {"low": 13.0}, {"open": 0}, {"low": -1},
    {"high": float("inf")}, {"volume": -1}, {"volume": 1.5},
    {"turnover": None}, {"time_key": True}, {"symbol": "HK.02840"},
])
def test_bad_bars_fail_the_whole_capture_without_silent_row_loss(changes):
    client, _, _ = client_for([success({"kline_list": [bar(), bar(**changes)]})])
    with pytest.raises(FutuQuoteError) as error:
        client.history("HK.02822", date(2026, 9, 30), date(2026, 9, 30))
    assert len(error.value.captures) == 1
    assert len(error.value.captures[0].payload["data"]["kline_list"]) == 2


def test_duplicate_dates_are_rejected_even_if_values_match():
    client, _, _ = client_for([success({"kline_list": [bar(), bar()]})])
    with pytest.raises(FutuQuoteError, match="duplicate"):
        client.history("HK.02822", date(2026, 9, 30), date(2026, 9, 30))


def test_out_of_window_bar_and_remaining_page_are_rejected():
    client, _, _ = client_for([success({"kline_list": [bar("2026-09-29")]})])
    with pytest.raises(FutuQuoteError, match="outside"):
        client.history("HK.02822", date(2026, 9, 30), date(2026, 9, 30))
    client, _, _ = client_for([success({"kline_list": [bar()], "next_time": bar()["time_key"]})])
    with pytest.raises(FutuQuoteError, match="pagination"):
        client.history("HK.02822", date(2026, 9, 30), date(2026, 9, 30))


def test_failed_later_window_keeps_previous_and_failure_evidence():
    client, _, _ = client_for([
        success({"kline_list": [bar("2024-12-30")]}),
        Response({"ret_code": -8, "ret_msg": "do not expose this response", "data": {}}),
    ])
    with pytest.raises(FutuQuoteCoverageError) as error:
        client.history("HK.02822", date(2024, 1, 1), date(2024, 12, 31))
    assert len(error.value.captures) == 2
    assert "do not expose" not in str(error.value)


def basic(**changes):
    row = {"code": "HK.02822", "name": "Example ETF", "stock_type": "ETF",
           "stock_id": 123456, "listing_date": bar("2004-11-18")["time_key"],
           "exchange": "SEHK"}
    row.update(changes)
    return row


def test_basic_information_proves_security_type_and_stable_identity():
    client, session, _ = client_for([success({"basic_list": [basic()]})])
    result = client.basic_info("HK.02822")
    assert result.info["symbol"] == "HK.02822"
    assert result.info["stock_type"] == "ETF" and result.info["stock_id"] == 123456
    assert result.info["listing_date"] == "2004-11-18"
    assert session.calls[0][0] == "POST"
    assert session.calls[0][2]["json"] == {"code_list": ["HK.02822"]}
    assert result.captures[0].params == {"code_list": ["HK.02822"]}


@pytest.mark.parametrize("items", [[], [basic(stock_type="DRVT")],
                                   [basic(stock_id=0)], [basic(code="HK.02840")],
                                   [basic(), basic()]])
def test_basic_information_never_silently_accepts_unknown_or_different_instrument(items):
    client, _, _ = client_for([success({"basic_list": items})])
    with pytest.raises(FutuQuoteError) as error:
        client.basic_info("HK.02822")
    assert len(error.value.captures) == 1


@pytest.mark.parametrize("symbol", ["HK.2822", "HK.00000", "SH.600000", "US.SPY/../../trade",
                                    "https://evil.test", "US.SPY?scope=trade:write"])
def test_only_canonical_hk_us_symbols_can_be_requested(symbol):
    client, session, _ = client_for([])
    with pytest.raises(FutuQuoteError):
        client.basic_info(symbol)
    assert not session.calls
    with pytest.raises(FutuQuoteError):
        client._request("POST", "/api/v1.0/trade/place-order", {})
    assert not session.calls


def test_401_forces_at_most_one_refresh_and_then_uses_new_token():
    calls = []
    def token_provider(*, force_refresh=False):
        calls.append(force_refresh)
        return "NEW-PRIVATE" if force_refresh else "OLD-PRIVATE"
    client, session, _ = client_for([Response(status=401), success({"kline_list": []})], token_provider)
    result = client.history("US.SPY", date(2026, 9, 30), date(2026, 9, 30))
    assert not result.rows and calls == [False, True]
    assert session.calls[1][2]["headers"]["Authorization"] == "Bearer NEW-PRIVATE"
    assert "PRIVATE" not in repr(result)
    client, session, _ = client_for([Response(status=401), Response(status=401)], token_provider)
    with pytest.raises(FutuQuoteError, match="HTTP status 401"):
        client.history("US.SPY", date(2026, 9, 30), date(2026, 9, 30))
    assert len(session.calls) == 2


@pytest.mark.parametrize("status", [302, 400, 403, 404])
def test_deterministic_http_failures_and_redirects_are_not_retried(status):
    client, session, waits = client_for([Response(status=status, raw=b"PRIVATE-BEARER")])
    with pytest.raises(FutuQuoteError) as error:
        client.history("US.SPY", date(2026, 9, 30), date(2026, 9, 30))
    assert str(status) in str(error.value)
    assert "PRIVATE" not in str(error.value) and not error.value.captures
    assert len(session.calls) == 1 and not waits


def test_transient_retry_obeys_retry_after_and_does_not_leak_transport_error_text():
    client, session, waits = client_for([
        Response(status=429, headers={"Retry-After": "2"}),
        requests.ConnectionError("Authorization: Bearer PRIVATE-BEARER"),
        success({"kline_list": []}),
    ])
    client.history("US.SPY", date(2026, 9, 30), date(2026, 9, 30))
    assert waits == [2.0, 2.0] and len(session.calls) == 3
    client, session, waits = client_for([requests.ConnectionError("PRIVATE-BEARER")] * 3)
    with pytest.raises(FutuQuoteError) as error:
        client.history("US.SPY", date(2026, 9, 30), date(2026, 9, 30))
    assert "ConnectionError" in str(error.value) and "PRIVATE" not in str(error.value)
    assert len(session.calls) == 3 and len(waits) == 2


def test_long_retry_after_returns_to_caller_without_retrying_earlier_than_requested():
    client, session, waits = client_for([Response(status=429, headers={"Retry-After": "120"})])
    with pytest.raises(FutuQuoteError, match="retry later"):
        client.history("US.SPY", date(2026, 9, 30), date(2026, 9, 30))
    assert len(session.calls) == 1 and not waits


def test_missing_business_status_is_not_treated_as_success():
    client, _, _ = client_for([Response({"data": {"kline_list": []}})])
    with pytest.raises(FutuQuoteError, match="business status") as error:
        client.history("US.SPY", date(2026, 9, 30), date(2026, 9, 30))
    assert len(error.value.captures) == 1


def test_bulk_basic_info_records_real_coverage_and_does_not_invent_currency():
    client, session, _ = client_for([success({"basic_list": [
        basic(code="HK.00700", stock_type="STOCK", stock_id=700),
        basic(code="HK.02822", stock_id=2822),
        basic(code="US.OPTION", stock_type="DRVT"),
    ]})])
    result = client.basic_info_many(["HK.00700", "HK.02822", "HK.03008", "US.OPTION"])
    assert [row["symbol"] for row in result.rows] == ["HK.00700", "HK.02822"]
    assert all("currency" not in row for row in result.rows)
    assert len(result.captures[0].payload["data"]["basic_list"]) == 3
    assert session.calls[0][2]["json"]["code_list"] == ["HK.00700", "HK.02822", "HK.03008", "US.OPTION"]


def test_bulk_basic_info_chunks_at_the_documented_limit():
    symbols = [f"HK.{i:05}" for i in range(1, 402)]
    client, session, _ = client_for([
        success({"basic_list": [basic(code=symbols[0])]}),
        success({"basic_list": [basic(code=symbols[-1])]}),
    ])
    result = client.basic_info_many(symbols)
    assert [len(call[2]["json"]["code_list"]) for call in session.calls] == [400, 1]
    assert [row["symbol"] for row in result.rows] == [symbols[0], symbols[-1]]
    assert len(result.captures) == 2


@pytest.mark.parametrize("items", [
    [basic(stock_type=None)], [basic(code="HK.02840")], [basic(), basic()],
    [basic(stock_id=True)], [basic(symbol="HK.02840")],
])
def test_bulk_basic_info_rejects_structural_and_identity_errors(items):
    client, _, _ = client_for([success({"basic_list": items})])
    with pytest.raises(FutuQuoteError) as error:
        client.basic_info_many(["HK.02822", "HK.03008"])
    assert len(error.value.captures) == 1


def test_bulk_empty_and_duplicate_requests_make_no_network_calls():
    client, session, _ = client_for([])
    assert client.basic_info_many([]).rows == ()
    with pytest.raises(FutuQuoteError, match="duplicate"):
        client.basic_info_many(["HK.02822", "HK.02822"])
    assert not session.calls


def test_volume_underflow_and_malformed_json_preserve_capture_as_failed_evidence():
    client, _, _ = client_for([success({"kline_list": [bar(volume=1)], "volume_precision": 10000})])
    with pytest.raises(FutuQuoteError, match="represented") as error:
        client.history("HK.02822", date(2026, 9, 30), date(2026, 9, 30))
    assert len(error.value.captures) == 1
    client, _, _ = client_for([Response(raw=b"broken financial-data response")])
    with pytest.raises(FutuQuoteError, match="JSON") as error:
        client.basic_info("HK.02822")
    assert error.value.captures[0].body == b"broken financial-data response"
    assert "broken financial" not in str(error.value)


def test_all_unknown_basic_info_batch_is_explicitly_uncovered_with_original_evidence():
    client, _, _ = client_for([Response({"ret_code": -7, "ret_msg": "invalid_symbol"})])
    result = client.basic_info_many(["HK.09999"])
    assert result.rows == () and len(result.captures) == 1
    assert result.captures[0].payload["ret_code"] == -7
    client, _, _ = client_for([Response({"ret_code": -7, "ret_msg": "invalid_symbol"})])
    with pytest.raises(FutuQuoteCoverageError) as error:
        client.basic_info("HK.09999")
    assert len(error.value.captures) == 1


def test_deterministic_request_configuration_errors_are_not_retried_or_exposed():
    client, session, waits = client_for([requests.exceptions.InvalidHeader("PRIVATE-BEARER")])
    with pytest.raises(FutuQuoteError, match="InvalidHeader") as error:
        client.basic_info("HK.02822")
    assert len(session.calls) == 1 and not waits
    assert "PRIVATE" not in str(error.value)


def business_rate_limit(message="too many requests, retry after 15s"):
    return Response({"ret_code": -11, "ret_msg": "rate limit exceeded", "error": {
        "code": "rate_limited", "message": message,
    }})


def test_http_200_business_rate_limit_waits_for_the_observed_provider_delay():
    client, session, waits = client_for([business_rate_limit(), success({"basic_list": [basic()]})])
    result = client.basic_info_many(["HK.02822"])
    assert result.rows[0]["symbol"] == "HK.02822"
    assert len(session.calls) == 2 and waits == [15.0]
    assert "PRIVATE-BEARER" not in repr(result)


@pytest.mark.parametrize("header", [None, "43"])
def test_observed_43_second_rate_limit_allows_catalog_to_resume(header):
    response = business_rate_limit("too many requests, retry after 43s")
    if header is not None:
        response.headers["Retry-After"] = header
    client, session, waits = client_for([response, success({"basic_list": [basic()]})])
    result = client.basic_info_many(["HK.02822"])
    assert result.rows[0]["symbol"] == "HK.02822"
    assert waits == [43.0] and len(session.calls) == 2
    assert "PRIVATE-BEARER" not in repr(result)


def test_one_minute_retry_budget_remains_bounded_across_attempts():
    client, session, waits = client_for([
        business_rate_limit("too many requests, retry after 60s")
    ] * 3)
    with pytest.raises(FutuQuoteError, match="business code -11"):
        client.basic_info_many(["HK.02822"])
    assert len(session.calls) == 3 and waits == [60.0, 60.0]


def test_business_rate_limit_is_bounded_and_keeps_safe_failure_evidence():
    client, session, waits = client_for([business_rate_limit()] * 3)
    with pytest.raises(FutuQuoteError, match="business code -11") as error:
        client.basic_info_many(["HK.02822"])
    assert len(session.calls) == 3 and waits == [15.0, 15.0]
    assert error.value.business_code == -11
    assert len(error.value.captures) == 1
    assert "retry after" not in str(error.value)


def test_business_rate_limit_respects_max_wait_and_does_not_retry_other_business_errors():
    client, session, waits = client_for([business_rate_limit("too many requests, retry after 120s")])
    with pytest.raises(FutuQuoteError, match="120s.*60s.*retry later") as caught:
        client.basic_info_many(["HK.02822"])
    assert len(session.calls) == 1 and not waits
    assert caught.value.business_code == -11
    assert len(caught.value.captures) == 1
    for code, error_code in [(-3, "rate_limited"), (-11, "permission_denied")]:
        client, session, waits = client_for([Response({"ret_code": code, "error": {
            "code": error_code, "message": "too many requests, retry after 15s",
        }})])
        with pytest.raises(FutuQuoteError):
            client.basic_info_many(["HK.02822"])
        assert len(session.calls) == 1 and not waits


def test_observed_hk2822_inconsistent_open_is_retained_without_price_repair():
    original = bar("2025-08-29", open=14.65, low=14.67, high=14.93, close=14.86)
    response = success({"kline_list": [original]})
    client, _, _ = client_for([response])
    result = client.history("HK.02822", date(2025, 8, 29), date(2025, 8, 29))
    row = result.rows[0]
    assert row["ohlc_quality"] == "inconsistent"
    assert {key: row[key] for key in ("open", "low", "high", "close")} == {
        "open": 14.65, "low": 14.67, "high": 14.93, "close": 14.86,
    }
    assert result.captures[0].body == response.body
    assert result.captures[0].payload["data"]["kline_list"][0] == original


@pytest.mark.parametrize("close", [13.0, 8.0])
def test_positive_close_outside_bar_range_is_rejected(close):
    client, _, _ = client_for([success({"kline_list": [bar(close=close)]})])
    with pytest.raises(FutuQuoteError, match="close conflicts") as caught:
        client.history("HK.02822", date(2026, 9, 30), date(2026, 9, 30))
    assert caught.value.captures  # The rejected provider response remains inspectable evidence.


def test_complete_action_capture_preserves_unsupported_events_and_empty_is_explicit():
    event = {"ex_div_date": "2025-12-18", "action_types": ["DIVIDEND"],
             "per_cash_div": .38573, "desc_sc": "1股派股息0.38573HKD"}
    complex_event = {"ex_div_date": "2026-01-02", "action_types": ["SPINOFF"]}
    client, session, _ = client_for([success({"rehabs": [complex_event, event]}), success({"rehabs": []})])
    result = client.corporate_actions("HK.02822")
    assert result.rows == (event, complex_event)
    assert session.calls[0][1].endswith("/quote/HK.02822/corporate-actions/rehab")
    assert session.calls[0][2]["params"] == {"divi_mode": "include_divi"}
    assert "PRIVATE-BEARER" not in str(result.captures)
    assert client.corporate_actions("HK.02822").rows == ()


@pytest.mark.parametrize("data", [{}, {"rehabs": None}, {"rehabs": [{}]},
    {"rehabs": [{"ex_div_date": "2026-01-02", "action_types": []}]},
    {"rehabs": [{"ex_div_date": "2026-99-99", "action_types": ["DIVIDEND"]}]}])
def test_action_shape_failure_retains_original_capture(data):
    client, _, _ = client_for([success(data)])
    with pytest.raises(FutuQuoteError) as failure:
        client.corporate_actions("HK.02822")
    assert len(failure.value.captures) == 1
