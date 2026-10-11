from http.client import IncompleteRead
import urllib.error

import pytest

from studio_market.numeric.collect import failure_summary
from studio_market.numeric.providers import cn_futures, fmp


class Response:
    status = 200
    headers = {}

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
    client = fmp.FmpClient("test-key", max_attempts=3)
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
    client = fmp.FmpClient("test-key", max_attempts=1)
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


@pytest.mark.parametrize('mode', ['stalled', 'short_content_length', 'short_chunked'])
def test_default_fmp_transport_bounds_real_stalled_and_truncated_responses(monkeypatch, mode):
    """Exercise the actual default HTTP reader, including bulk CSV truncation."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import time

    release = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            assert self.headers['apikey'] == 'test-key'
            self.send_response(200)
            if mode == 'short_chunked':
                self.send_header('Transfer-Encoding', 'chunked')
            else:
                self.send_header('Content-Length', '100')
            self.end_headers()
            if mode == 'stalled':
                release.wait(2)
            else:
                self.wfile.write(b'64\r\npartial' if mode == 'short_chunked' else b'partial')
                self.wfile.flush()
            self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(fmp, 'BASE_URL', f'http://127.0.0.1:{server.server_port}')
    client = fmp.FmpClient('test-key', timeout_seconds=0.05, max_attempts=3)
    monkeypatch.setattr(client, '_wait', lambda _attempt: None)
    started = time.monotonic()
    try:
        with pytest.raises(fmp.FmpTransportError) as captured:
            client.get_bytes('profile-bulk', {'part': 0})
        assert len(requests) == 3
        assert time.monotonic() - started < 2
        assert 'partial' not in str(captured.value)
        assert 'test-key' not in str(captured.value)
    finally:
        release.set()
        client.close()
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize('mode', ['complete', 'short_content_length', 'short_gzip', 'bad_crc', 'bad_deflate', 'oversized'])
def test_fmp_gzip_transport_preserves_decoded_bound_and_complete_payload(monkeypatch, mode):
    import gzip
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    body = b'symbol,companyName\n' + b'GOOGL,Alphabet\n' * 400
    encoded = gzip.compress(body)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            assert self.headers['Accept-Encoding'] == 'gzip'
            assert self.headers['apikey'] == 'test-key'
            payload = encoded
            if mode == 'short_gzip':
                payload = encoded[:-8]
            elif mode == 'bad_crc':
                payload = encoded[:-8] + bytes([encoded[-8] ^ 1]) + encoded[-7:]
            elif mode == 'bad_deflate':
                # Reserved DEFLATE block type exercises zlib.error, not gzip CRC.
                payload = encoded[:10] + bytes([(encoded[10] & 0xF8) | 0x07]) + encoded[11:]
            self.send_response(200)
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(payload) + (20 if mode == 'short_content_length' else 0)))
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(fmp, 'BASE_URL', f'http://127.0.0.1:{server.server_port}')
    if mode == 'oversized':
        assert len(encoded) < 1024 < len(body)
        monkeypatch.setattr(fmp, 'MAX_RESPONSE_BYTES', 1024)
    client = fmp.FmpClient('test-key', timeout_seconds=0.5, max_attempts=3)
    monkeypatch.setattr(client, '_wait', lambda _attempt: None)
    try:
        if mode == 'complete':
            assert client.get_bytes('profile-bulk', {'part': 0}).body == body
            assert len(requests) == 1
        elif mode == 'oversized':
            with pytest.raises(fmp.FmpResponseError, match='exceeded'):
                client.get_bytes('profile-bulk', {'part': 0})
            assert len(requests) == 1
        else:
            with pytest.raises(fmp.FmpTransportError) as captured:
                client.get_bytes('profile-bulk', {'part': 0})
            assert len(requests) == 3
            assert 'Alphabet' not in str(captured.value)
            assert 'test-key' not in str(captured.value)
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        worker.join()
