"""Bounded public search and original-page evidence for sector research."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from io import BytesIO
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from watchlist_app.services.deepseek_config import deepseek_endpoint, deepseek_model

_TIMEOUT = 30
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_TEXT_CHARS = 12000
_REDIRECTS = {301, 302, 303, 307, 308}
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([^;\s\"']+)", re.I)


class SectorWebError(RuntimeError):
    """Evidence could not be acquired; this does not mean nothing happened."""


def _public_destination(url: str):
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise SectorWebError("Invalid public webpage URL") from exc
    if (parsed.scheme not in {"http", "https"} or not hostname
            or parsed.username is not None or parsed.password is not None
            or "%" in hostname or any(ord(c) < 33 for c in url)):
        raise SectorWebError("Only public HTTP(S) URLs without credentials are allowed")
    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise SectorWebError("Local or private network destinations are not allowed")
    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    except (OSError, ValueError) as exc:
        raise SectorWebError("Could not resolve a public webpage address") from exc
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise SectorWebError("Local or private network destinations are not allowed")
    return parsed, str(ips[0]), port


def _exchange(parsed, address: str, port: int, *, method: str, headers: dict, body: bytes | None,
              timeout: int = _TIMEOUT, response_reader=None):
    """Connect to the validated IP, preserving the original Host and TLS name."""
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    try:
        connection.sock = socket.create_connection((address, port), timeout=timeout)
        if parsed.scheme == "https":
            connection.sock = ssl.create_default_context().wrap_socket(
                connection.sock, server_hostname=parsed.hostname,
            )
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        if response_reader is None:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise SectorWebError("Web response exceeded the evidence size limit")
        else:
            # The caller consumes a bounded stream while the validated connection
            # is open. DNS pinning, TLS identity and redirect handling stay here.
            payload = response_reader(response)
        return response.status, {k.lower(): v for k, v in response.getheaders()}, payload
    except (OSError, http.client.HTTPException) as exc:
        raise SectorWebError("Web request failed or timed out") from exc
    finally:
        connection.close()


def _request(url: str, *, method="GET", headers=None, body=None, timeout: int = _TIMEOUT, response_reader=None):
    parsed, address, port = _public_destination(url)
    options = {"response_reader": response_reader} if response_reader is not None else {}
    return _exchange(parsed, address, port, method=method, headers=headers or {}, body=body, timeout=timeout, **options)


def search_web(query: str) -> dict:
    query = query.strip()
    if not query:
        raise SectorWebError("Search query is empty")
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SectorWebError("DEEPSEEK_API_KEY is not configured")
    body = {
        "model": deepseek_model(), "max_tokens": 4096,
        "messages": [{"role": "user", "content": [{
            "type": "text", "text": f"Perform a web search for the query: {query}",
        }]}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
    }
    status, _, payload = _request(deepseek_endpoint(search=True), method="POST", headers={
        "x-api-key": key, "authorization": f"Bearer {key}",
        "anthropic-version": "2023-06-01", "content-type": "application/json",
        "accept": "application/json", "user-agent": "InvestmentStudio-SectorResearch/1.0",
    }, body=json.dumps(body).encode())
    # Never forward the credential-bearing request to a redirect destination.
    if not 200 <= status < 300:
        raise SectorWebError(f"DeepSeek search failed (HTTP {status})")
    try:
        document = json.loads(payload)
        blocks = document.get("content", [])
    except (ValueError, AttributeError) as exc:
        raise SectorWebError("DeepSeek search returned an invalid response") from exc
    if not isinstance(blocks, list):
        raise SectorWebError("DeepSeek search returned invalid content blocks")
    results = [block for block in blocks if isinstance(block, dict)
               and block.get("type") == "web_search_tool_result"]
    if not results:
        if any(isinstance(block, dict) and block.get("type") == "tool_use" for block in blocks):
            raise SectorWebError("Configured search endpoint did not execute native web search")
        raise SectorWebError("DeepSeek returned no structured web search result blocks")
    snippets = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for citation in block.get("citations") or []:
            if isinstance(citation, dict) and citation.get("url") and citation.get("cited_text"):
                snippets.setdefault(citation["url"], citation["cited_text"])
    sources, seen, coverage = [], set(), []
    discovered_at = datetime.now(timezone.utc).isoformat()
    for result in results:
        items = result.get("content", [])
        if isinstance(items, dict) and items.get("type") == "web_search_tool_result_error":
            coverage.append("Native search reported an incomplete or failed search")
            continue
        if not isinstance(items, list):
            coverage.append("Native search returned an unreadable result block")
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            source = {"source_id": str(uuid4()), "url": url, "title": item.get("title") or "",
                      "published_at_raw": item.get("page_age"), "time_status": "search_metadata",
                      "discovered_at": discovered_at}
            if url in snippets:
                source["excerpt"] = snippets[url]
            sources.append(source)
    return {"query": query, "sources": sources, "coverage": coverage}


class _HTMLCharset(HTMLParser):
    def __init__(self):
        super().__init__()
        self.charset = None

    def handle_starttag(self, tag, attrs):
        if tag != "meta" or self.charset:
            return
        attrs = dict(attrs)
        if attrs.get("charset"):
            self.charset = attrs["charset"].strip()
        elif (attrs.get("http-equiv") or "").lower() == "content-type":
            match = _CHARSET.search(attrs.get("content") or "")
            if match:
                self.charset = match.group(1)


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts, self.parts, self.article_parts, self.main_parts = [], [], [], []
        self.publication_values, self.json_ld = [], []
        self.skipped, self.in_title, self.in_article, self.in_main = 0, 0, 0, 0
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("property") or attrs.get("name", "")).lower() == "article:published_time":
            if attrs.get("content"):
                self.publication_values.append(("article:published_time", attrs["content"]))
        if tag == "script":
            self.script = [] if attrs.get("type", "").lower() == "application/ld+json" else None
        if tag in {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}:
            self.skipped += 1
        if tag == "title": self.in_title += 1
        if tag == "article": self.in_article += 1
        if tag == "main": self.in_main += 1
        if tag in {"p", "div", "section", "li", "br", "h1", "h2", "h3"}:
            self.handle_data("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.json_ld.append("".join(self.script))
            self.script = None
        if tag in {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}:
            self.skipped = max(0, self.skipped - 1)
        if tag == "title": self.in_title = max(0, self.in_title - 1)
        if tag == "article": self.in_article = max(0, self.in_article - 1)
        if tag == "main": self.in_main = max(0, self.in_main - 1)

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.in_title:
            self.title_parts.append(data)
        elif not self.skipped:
            self.parts.append(data)
            if self.in_article: self.article_parts.append(data)
            if self.in_main: self.main_parts.append(data)


def _json_publications(value):
    if isinstance(value, list):
        for item in value:
            yield from _json_publications(item)
    elif isinstance(value, dict):
        # Follow the page's graph/main entity, not unrelated linked recommendations.
        if isinstance(value.get("datePublished"), str):
            yield "JSON-LD datePublished", value["datePublished"]
        for field in ("@graph", "mainEntity"):
            yield from _json_publications(value.get(field))


def _publication_time(values, cutoff: datetime):
    evidence, instants, dates = [], set(), set()
    for field, raw in dict.fromkeys(values):
        parsed = None
        try:
            value = raw.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                parsed = date.fromisoformat(value).isoformat()
                dates.add(parsed)
            else:
                candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if candidate.tzinfo is not None and candidate.utcoffset() is not None:
                    instant = candidate.astimezone(timezone.utc)
                    instants.add(instant)
                    parsed = instant.isoformat()
        except ValueError:
            pass
        evidence.append({"field": field, "raw": raw,
                         "parsed_at": parsed})
    if len(instants) == 1:
        instant = next(iter(instants))
        published = instant.isoformat()
        time_status = "future" if instant > cutoff else "verified"
    elif not instants and len(dates) == 1:
        published = next(iter(dates))
        time_status = "future" if published > cutoff.astimezone(timezone.utc).date().isoformat() else "date_only"
    else:
        published, time_status = None, "unknown"
    return {
        "published_at": published,
        "published_at_raw": "; ".join(dict.fromkeys(raw for _, raw in values)) or None,
        "date_evidence": evidence, "time_status": time_status,
    }


def _pdf_evidence(payload: bytes, url: str, cutoff: datetime) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(BytesIO(payload))
        total_pages = len(reader.pages)
        parts, empty_pages = [], []
        length, pages_read = 0, 0
        for number, page in enumerate(reader.pages, 1):
            content = (page.extract_text() or "").strip()
            pages_read = number
            if content:
                part = f"[第 {number} 页]\n{content}"
                parts.append(part)
                length += len(part) + (1 if len(parts) > 1 else 0)
            else:
                empty_pages.append(number)
            if length >= _MAX_TEXT_CHARS:
                break
        title = reader.metadata.title if reader.metadata else None
    except (PyPdfError, ValueError) as exc:
        raise SectorWebError("Original PDF text could not be extracted; the document was not read") from exc
    text = "\n".join(parts)
    if not text:
        raise SectorWebError("Original PDF has no readable text layer; scanned pages require OCR and were not read")
    truncated = len(text) > _MAX_TEXT_CHARS or pages_read < total_pages
    coverage = ["仅提取 PDF 文字层，未识别图片或扫描页；文件创建/修改时间不作为发布日期。"]
    if truncated:
        coverage.append(f"正文已截断：最多保留前 {_MAX_TEXT_CHARS} 字符，处理至第 {pages_read} 页，共 {total_pages} 页。")
    if empty_pages:
        coverage.append("以下页无可提取文字，未读取其图像内容：" + "、".join(map(str, empty_pages)) + "。")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    return {"source_id": str(uuid4()), "url": url, "title": title if isinstance(title, str) else "",
        "text": text[:_MAX_TEXT_CHARS], "text_truncated": truncated, "content_type": "application/pdf",
        "page_count": total_pages, "pages_read": pages_read, "pages_without_text": empty_pages,
        "coverage": coverage, **_publication_time([], cutoff),
        "retrieved_at": retrieved_at, "discovered_at": retrieved_at}


def fetch_web(url: str, cutoff: datetime) -> dict:
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise SectorWebError("Evidence cutoff must be a timezone-aware timestamp")
    current_url = url
    for redirect in range(6):
        status, headers, payload = _request(current_url, headers={
            "user-agent": "InvestmentStudio-SectorResearch/1.0",
            "accept": "text/html,application/xhtml+xml,application/pdf", "accept-encoding": "identity",
        })
        if status not in _REDIRECTS:
            break
        if redirect == 5 or not headers.get("location"):
            raise SectorWebError("Webpage redirect could not be followed")
        current_url = urljoin(current_url, headers["location"])
    if not 200 <= status < 300:
        raise SectorWebError(f"Original webpage could not be read (HTTP {status})")
    content_type = headers.get("content-type", "").lower()
    if "application/pdf" in content_type or payload.lstrip().startswith(b"%PDF-"):
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise SectorWebError("Original PDF ignored the requested uncompressed encoding")
        return _pdf_evidence(payload, current_url, cutoff)
    if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml")):
        raise SectorWebError("Original document is not readable HTML or PDF")
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise SectorWebError("Original webpage ignored the requested uncompressed HTML encoding")
    charset = _CHARSET.search(content_type)
    if charset:
        encoding = charset.group(1)
    else:
        declaration = _HTMLCharset()
        # HTML encoding declarations are ASCII; retain raw bytes during this prescan.
        declaration.feed(payload.decode("latin-1"))
        encoding = declaration.charset or "utf-8"
    try:
        html = payload.decode(encoding)
    except LookupError as exc:
        raise SectorWebError("Original webpage declared an unsupported character encoding") from exc
    except UnicodeDecodeError as exc:
        raise SectorWebError("Original webpage text could not be decoded; the article was not read") from exc
    page = _Page()
    page.feed(html)
    text = "\n".join(line.strip() for line in "".join(
        page.article_parts or page.main_parts or page.parts,
    ).splitlines() if line.strip())
    if not text or any(marker in text.lower() for marker in (
        "subscribe to continue reading", "subscribe to read the full article",
        "sign in to continue reading", "订阅后阅读", "登录后查看全文",
        "enable javascript and cookies to continue",
    )):
        raise SectorWebError("Original article body is unavailable or access-restricted")
    for block in page.json_ld:
        try:
            page.publication_values.extend(_json_publications(json.loads(block)))
        except ValueError:
            continue
    retrieved_at = datetime.now(timezone.utc).isoformat()
    return {
        "source_id": str(uuid4()), "url": current_url,
        "title": "".join(page.title_parts).strip(),
        "text": text[:_MAX_TEXT_CHARS], "text_truncated": len(text) > _MAX_TEXT_CHARS,
        **_publication_time(page.publication_values, cutoff),
        "retrieved_at": retrieved_at, "discovered_at": retrieved_at,
    }
