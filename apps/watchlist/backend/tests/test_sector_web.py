import json
import socket
from io import BytesIO
from datetime import datetime, timezone

import pytest

from watchlist_app.services import sector_web as web


CUTOFF = datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def public_dns(monkeypatch):
    def resolve(host, port, **kwargs):
        address = "127.0.0.1" if host == "private.example" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    monkeypatch.setattr(web.socket, "getaddrinfo", resolve)


def page_transport(monkeypatch, html, *, headers=None, status=200):
    def exchange(*args, **kwargs):
        return status, headers or {"content-type": "text/html; charset=utf-8"}, html.encode()
    monkeypatch.setattr(web, "_exchange", exchange)


@pytest.mark.parametrize("reader_fails", [False, True])
def test_stream_reader_uses_pinned_connection_and_always_closes(monkeypatch, public_dns, reader_fails):
    from types import SimpleNamespace
    calls = []
    response = SimpleNamespace(status=200, getheaders=lambda: [("Content-Type", "text/event-stream")])
    class Connection:
        def __init__(self, host, port, timeout):
            calls.append(("connection", host, port, timeout))
        def request(self, method, path, **kwargs):calls.append(("request", method, path))
        def getresponse(self):return response
        def close(self):calls.append(("closed",))
    monkeypatch.setattr(web.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(web.socket, "create_connection", lambda address, **kwargs: calls.append(("pinned", address)) or "socket")
    def wrap(sock, *, server_hostname):
        calls.append(("tls", server_hostname));return sock
    monkeypatch.setattr(web.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=wrap))
    def reader(value):
        assert value is response
        if reader_fails:raise TimeoutError("private transport detail")
        return b"complete stream"
    if reader_fails:
        with pytest.raises(web.SectorWebError) as failure:
            web._request("https://provider.example/v1/chat/completions", timeout=300, response_reader=reader)
        assert isinstance(failure.value.__cause__, TimeoutError)
    else:
        assert web._request("https://provider.example/v1/chat/completions", timeout=300, response_reader=reader) == (
            200, {"content-type": "text/event-stream"}, b"complete stream")
    assert ("pinned", ("93.184.216.34", 443)) in calls and ("tls", "provider.example") in calls
    assert calls[-1] == ("closed",)


@pytest.mark.parametrize("base,endpoint", [(None, "https://gateway.hzxxf.cn/v1/messages"),
    ("https://provider.example/v1/", "https://provider.example/v1/messages"),
    ("https://provider.example", "https://provider.example/messages")])
@pytest.mark.parametrize("configured_model", [None, "test-configured-model"])
def test_search_uses_native_blocks_citation_join_and_explicit_output_limit(monkeypatch, public_dns, base, endpoint, configured_model):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME", configured_model or "")
    monkeypatch.delenv("DEEPSEEK_SEARCH_URL", raising=False)
    if base is None:
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("DEEPSEEK_BASE_URL", base)
    results = [{"type": "web_search_result", "url": f"https://news.example/{i}",
                "title": f"Source {i}", "page_age": "2 days ago"} for i in range(10)]
    payload = {"content": [
        {"type": "text", "text": "https://fabricated.example/not-a-source", "citations": [
            {"url": "https://news.example/0", "cited_text": "Cited excerpt"},
        ]},
        {"type": "web_search_tool_result", "content": results + [results[0]]},
    ]}
    requests = []

    def exchange(parsed, address, port, **kwargs):
        requests.append((parsed, address, port, kwargs))
        return 200, {"content-type": "application/json"}, json.dumps(payload).encode()

    monkeypatch.setattr(web, "_exchange", exchange)
    before = datetime.now(timezone.utc)
    result = web.search_web("sector developments")
    after = datetime.now(timezone.utc)
    assert len(result["sources"]) == 10
    assert result["sources"][0]["excerpt"] == "Cited excerpt"
    assert result["sources"][0]["published_at_raw"] == "2 days ago"
    assert result["sources"][0]["time_status"] == "search_metadata"
    assert all(before <= datetime.fromisoformat(s["discovered_at"]) <= after
               for s in result["sources"])
    assert len({s["source_id"] for s in result["sources"]}) == 10
    assert all("fabricated" not in s["url"] for s in result["sources"])
    assert result["coverage"] == []
    parsed, address, _, request = requests[0]
    assert parsed.geturl() == endpoint
    assert address == "93.184.216.34" and request["method"] == "POST"
    assert request["headers"]["x-api-key"] == "fixture-secret"
    assert request["headers"]["authorization"] == "Bearer fixture-secret"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert json.loads(request["body"])["model"] == (configured_model or "deepseek-v4.1-flash")
    assert json.loads(request["body"])["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 2},
    ]


def test_search_prose_is_not_search_evidence_and_redirects_are_not_followed(monkeypatch, public_dns):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    page_transport(monkeypatch, json.dumps({"content": [{"type": "text", "text": "https://example.com"}]}))
    with pytest.raises(web.SectorWebError, match="no structured"):
        web.search_web("news")
    page_transport(monkeypatch, "", status=302, headers={"location": "https://other.example"})
    with pytest.raises(web.SectorWebError, match="HTTP 302"):
        web.search_web("news")
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(web.SectorWebError, match="not configured"):
        web.search_web("news")


def test_configured_search_endpoint_tool_call_is_not_executed_search_evidence(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("DEEPSEEK_SEARCH_URL", "https://provider.example/v1/messages")
    requests = []
    def request(url, **kwargs):
        requests.append((url, kwargs))
        return 200, {}, json.dumps({"stop_reason": "tool_use", "content": [{"type": "tool_use",
            "name": "web_search", "input": {"query": "news"}}]}).encode()
    monkeypatch.setattr(web, "_request", request)
    with pytest.raises(web.SectorWebError, match="did not execute native web search"):
        web.search_web("news")
    assert len(requests) == 1 and requests[0][0] == "https://provider.example/v1/messages"
    assert json.loads(requests[0][1]["body"])["model"] == "deepseek-v4.1-flash"


def test_original_publication_is_distinct_from_modification_and_http_dates(monkeypatch, public_dns):
    html = '''<html><head><title>Original article</title>
      <script type="application/ld+json">{"@type":"NewsArticle",
        "datePublished":"2026-09-01T12:00:00+08:00",
        "dateModified":"2026-09-05T12:00:00+08:00"}</script></head>
      <body><nav>Navigation</nav><article><p>Original body &amp; evidence.</p></article></body></html>'''
    page_transport(monkeypatch, html, headers={"content-type": "text/html",
                                            "last-modified": "Sat, 05 Sep 2026 12:00:00 GMT"})
    before = datetime.now(timezone.utc)
    result = web.fetch_web("https://news.example/article", CUTOFF)
    after = datetime.now(timezone.utc)
    assert result["time_status"] == "verified"
    assert result["published_at"] == "2026-09-01T04:00:00+00:00"
    assert result["text"] == "Original body & evidence."
    assert result["title"] == "Original article"
    assert result["discovered_at"] == result["retrieved_at"]
    assert before <= datetime.fromisoformat(result["discovered_at"]) <= after
    assert result["date_evidence"] == [{"field": "JSON-LD datePublished",
        "raw": "2026-09-01T12:00:00+08:00", "parsed_at": "2026-09-01T04:00:00+00:00"}]
    assert "dateModified" not in json.dumps(result)


@pytest.mark.parametrize("declaration", [
    '<meta charset="gbk">',
    '<meta content="text/html; charset=gb2312" http-equiv="Content-Type">',
])
def test_html_declared_chinese_encoding_preserves_original_text(monkeypatch, public_dns, declaration):
    # A short retained same-language source excerpt, encoded as an HTML response.
    title = "核心财务数据概览"
    body = "来源：速途网"
    payload = f'<html><head>{declaration}<title>{title}</title></head><body><nav>下载客户端登录</nav><article>{body}</article></body></html>'.encode("gb2312")
    monkeypatch.setattr(web, "_exchange", lambda *a, **kw: (200, {"content-type": "text/html"}, payload))
    result = web.fetch_web("https://news.example/article", CUTOFF)
    assert result["title"] == title
    assert result["text"] == body
    assert "�" not in result["text"]


def test_http_encoding_takes_precedence_and_undecodable_text_is_not_retained_as_read(monkeypatch, public_dns):
    payload = '<meta charset="utf-8"><article>原始公告</article>'.encode("gbk")
    monkeypatch.setattr(web, "_exchange", lambda *a, **kw: (200, {"content-type": "text/html; charset=gbk"}, payload))
    assert web.fetch_web("https://news.example/article", CUTOFF)["text"] == "原始公告"
    monkeypatch.setattr(web, "_exchange", lambda *a, **kw: (200, {"content-type": "text/html"}, payload))
    with pytest.raises(web.SectorWebError, match="could not be decoded"):
        web.fetch_web("https://news.example/article", CUTOFF)


@pytest.mark.parametrize("published,status,exact", [
    ("2026-09-05T12:00:00+08:00", "verified", "2026-09-05T04:00:00+00:00"),
    ("2026-09-05T12:00:00", "unknown", None),
    ("2026-09-05", "date_only", "2026-09-05"),
    ("2026-09-07", "future", "2026-09-07"),
    ("2026-09-07T00:00:00Z", "future", "2026-09-07T00:00:00+00:00"),
])
def test_publication_preserves_precision_without_guessing_timezone(monkeypatch, public_dns, published, status, exact):
    page_transport(monkeypatch,
        f'<meta property="article:published_time" content="{published}"><article>Evidence</article>')
    result = web.fetch_web("https://news.example/article", CUTOFF)
    assert result["published_at_raw"] == published
    assert result["time_status"] == status and result["published_at"] == exact


def test_modified_only_and_conflicting_publication_remain_unknown(monkeypatch, public_dns):
    for metadata in (
        '<script type="application/ld+json">{"dateModified":"2026-09-05T12:00:00Z"}</script>',
        '<meta property="article:published_time" content="2026-09-05T12:00:00Z">'
        '<script type="application/ld+json">{"@graph":[{"datePublished":"2026-09-01T12:00:00Z"}]}</script>',
    ):
        page_transport(monkeypatch, metadata + "<article>Article evidence</article>")
        result = web.fetch_web("https://news.example/article", CUTOFF)
        assert result["time_status"] == "unknown" and result["published_at"] is None


def test_invalid_pdf_paywall_and_unavailable_body_fail_clearly(monkeypatch, public_dns):
    page_transport(monkeypatch, "%PDF-1.7", headers={"content-type": "application/pdf"})
    with pytest.raises(web.SectorWebError, match="PDF text could not be extracted"):
        web.fetch_web("https://news.example/report.pdf", CUTOFF)
    page_transport(monkeypatch, "compressed bytes", headers={
        "content-type": "text/html", "content-encoding": "gzip",
    })
    with pytest.raises(web.SectorWebError, match="uncompressed"):
        web.fetch_web("https://news.example/article", CUTOFF)
    page_transport(monkeypatch, "<article>Subscribe to continue reading</article>")
    with pytest.raises(web.SectorWebError, match="access-restricted"):
        web.fetch_web("https://news.example/article", CUTOFF)
    page_transport(monkeypatch, "<html><script>app()</script></html>")
    with pytest.raises(web.SectorWebError, match="unavailable"):
        web.fetch_web("https://news.example/article", CUTOFF)
    page_transport(monkeypatch, "<article>" + "a" * 12001 + "</article>")
    result = web.fetch_web("https://news.example/article", CUTOFF)
    assert len(result["text"]) == 12000 and result["text_truncated"]


def pdf_bytes(pages, metadata=None):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=600, height=800)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            contents = DecodedStreamObject()
            contents.set_data(f"BT /F1 12 Tf 20 700 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(contents)
    if metadata:
        writer.add_metadata(metadata)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_public_pdf_text_retains_pages_without_promoting_pdf_file_dates(monkeypatch, public_dns):
    body = pdf_bytes(["Original speech September 1 2026", "Second page evidence"], {
        "/Title": "Policy speech", "/CreationDate": "D:20260905120000Z", "/ModDate": "D:20260905130000Z"})
    monkeypatch.setattr(web, "_exchange", lambda *a, **kw: (200, {"content-type": "application/pdf", "last-modified": "Sat, 05 Sep 2026 12:00:00 GMT"}, body))
    result = web.fetch_web("https://news.example/speech.pdf", CUTOFF)
    assert result["title"] == "Policy speech"
    assert result["text"] == "[第 1 页]\nOriginal speech September 1 2026\n[第 2 页]\nSecond page evidence"
    assert result["page_count"] == result["pages_read"] == 2
    assert not result["text_truncated"]
    assert result["published_at"] is None and result["published_at_raw"] is None
    assert result["time_status"] == "unknown" and result["date_evidence"] == []
    assert result["retrieved_at"] == result["discovered_at"]
    assert "20260905" not in json.dumps(result)


def test_pdf_truncation_and_unreadable_pages_are_explicit(monkeypatch, public_dns):
    body = pdf_bytes([None, "a" * 12001, "Unread later page"])
    monkeypatch.setattr(web, "_exchange", lambda *a, **kw: (200, {"content-type": "application/octet-stream"}, body))
    result = web.fetch_web("https://news.example/download", CUTOFF)
    assert len(result["text"]) == 12000 and result["text"].startswith("[第 2 页]")
    assert result["text_truncated"] and result["pages_read"] == 2 and result["page_count"] == 3
    assert result["pages_without_text"] == [1]
    assert any("正文已截断" in note for note in result["coverage"])
    assert any("未读取其图像内容" in note for note in result["coverage"])
    body = pdf_bytes([None])
    with pytest.raises(web.SectorWebError, match="no readable text layer"):
        web.fetch_web("https://news.example/scanned.pdf", CUTOFF)


@pytest.mark.parametrize("url", [
    "http://localhost/a", "http://sub.localhost/a", "http://private.example/a",
    "file:///etc/passwd", "http://user:pass@news.example/a",
])
def test_nonpublic_destinations_are_rejected_before_transport(monkeypatch, public_dns, url):
    monkeypatch.setattr(web, "_exchange", lambda *a, **kw: pytest.fail("Transport must not run"))
    with pytest.raises(web.SectorWebError):
        web.fetch_web(url, CUTOFF)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fd00::1"])
def test_private_literal_or_dns_address_is_blocked(monkeypatch, address):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)),
    ])
    monkeypatch.setattr(web, "_exchange", lambda *a, **kw: pytest.fail("Transport must not run"))
    with pytest.raises(web.SectorWebError, match="private"):
        web.fetch_web("http://untrusted.example", CUTOFF)


def test_redirect_is_rechecked_and_public_ip_is_pinned(monkeypatch, public_dns):
    destinations = []

    def exchange(parsed, address, port, **kwargs):
        destinations.append((parsed.hostname, address))
        return 302, {"location": "http://private.example/metadata"}, b""

    monkeypatch.setattr(web, "_exchange", exchange)
    with pytest.raises(web.SectorWebError, match="private"):
        web.fetch_web("https://news.example/article", CUTOFF)
    assert destinations == [("news.example", "93.184.216.34")]
