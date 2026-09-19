from http.client import IncompleteRead
import urllib.error

import pytest

from studio_market.numeric.collect import failure_summary
from studio_market.numeric.providers import cn_futures, fmp


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, _limit):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@pytest.mark.parametrize("recover", [True, False])
def test_incomplete_fmp_response_uses_existing_bounded_transport_retry(monkeypatch, recover):
    calls, waits = [], []

    def open_url(*args, **kwargs):
        calls.append(True)
        return Response(b'[{"symbol":"GOOGL"}]' if recover and len(calls) == 2
                        else IncompleteRead(b"partial-private-response", 50))

    monkeypatch.setattr(fmp.urllib.request, "urlopen", open_url)
    client = fmp.FmpClient("test-key", prefer_standard_https=True, max_attempts=3)
    monkeypatch.setattr(client, "_wait", waits.append)
    try:
        if recover:
            result = client.get_json("profile-bulk", {"part": 2})
            assert result.payload == [{"symbol": "GOOGL"}]
            assert len(calls) == 2 and waits == [1]
        else:
            with pytest.raises(fmp.FmpTransportError) as captured:
                client.get_json("profile-bulk", {"part": 2})
            assert len(calls) == 3 and waits == [1, 2]
            summary = failure_summary(captured.value)
            assert "IncompleteRead" in summary["error"]
            assert "partial-private-response" not in str(summary)
    finally:
        client.close()


def test_transport_diagnostics_keep_error_type_without_raw_url_or_response(monkeypatch):
    def failed_open(*args, **kwargs):
        raise urllib.error.URLError(OSError("https://secret.example/?apikey=private-value"))

    monkeypatch.setattr(fmp.urllib.request, "urlopen", failed_open)
    client = fmp.FmpClient("test-key", prefer_standard_https=True, max_attempts=1)
    try:
        with pytest.raises(fmp.FmpTransportError) as captured:
            client.get_json("profile-bulk")
        assert "URLError (OSError)" in str(captured.value)
        assert "private-value" not in str(failure_summary(captured.value))
    finally:
        client.close()


def test_gtja_incomplete_response_is_reported_as_sanitized_transport_failure(monkeypatch):
    monkeypatch.setattr(cn_futures.urllib.request, "urlopen",
                        lambda *args, **kwargs: Response(IncompleteRead(b"private-value", 50)))
    client = cn_futures.GTJAFuturesClient("https://example.test", "test-key", "test-secret")
    with pytest.raises(cn_futures.CnFuturesError) as captured:
        client.post_json("/query", {})
    summary = failure_summary(captured.value)
    assert "IncompleteRead" in summary["error"]
    assert "private-value" not in str(summary)
