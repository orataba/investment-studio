"""Scoped FMP company publications, retained in the shared immutable text corpus.

News feeds contain excerpts, SEC search contains document metadata, and the
transcript endpoint supplies its full provider text. These are distinct claims.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import re
from urllib.parse import urlencode, urlsplit

from .bundle import canonical_json, digest
from .store import TextStore
from ..numeric.providers.fmp import BASE_URL, FmpClient, FmpResponseError


CHANNELS = {
    "fmp:press_releases": {"endpoint": "news/press-releases", "information_type": "company_announcement", "content_scope": "source_excerpt"},
    "fmp:stock_news": {"endpoint": "news/stock", "information_type": "company_news", "content_scope": "source_excerpt"},
    "fmp:earnings_transcripts": {"endpoint": "earning-call-transcript", "information_type": "earnings_call_transcript", "content_scope": "full_text"},
    "fmp:sec_filings": {"endpoint": "sec-filings-search/symbol", "information_type": "regulatory_filing", "content_scope": "metadata"},
}
FUND_CHANNELS = ("fmp:press_releases", "fmp:stock_news")
PAGE_SIZE = 250


def _rows(response):
    if not isinstance(response.payload, list) or any(not isinstance(row, dict) for row in response.payload):
        raise FmpResponseError("FMP publication response must be a list of records")
    return response.payload


def _source_date(value):
    """Do not assign a timezone to a provider's unqualified wall-clock time."""
    if value is None or value == "":
        return None
    value = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(UTC).isoformat() if stamp.tzinfo else stamp.date().isoformat()
    except ValueError:
        raise FmpResponseError("FMP publication has an invalid source date") from None


def _public_url(value):
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise FmpResponseError("FMP publication has no valid public source URL")
    # Provider responses must never smuggle a credential into source metadata.
    if any(name in parsed.query.lower() for name in ("apikey=", "api_key=", "access_token=")):
        raise FmpResponseError("FMP publication URL contains a credential parameter")
    return url


def _record(response, row, symbol, channel, *, fiscal_year=None, quarter=None):
    definition = CHANNELS[channel]
    if str(row.get("symbol") or "").strip().upper() != symbol:
        raise FmpResponseError("FMP publication symbol does not match its requested company")
    publication = row.get("publishedDate") or row.get("acceptedDate") or row.get("filingDate")
    occurrence = None
    warnings = []
    if channel == "fmp:earnings_transcripts":
        year = int(row.get("year") or 0)
        period = str(row.get("period") or (f"Q{row['quarter']}" if row.get("quarter") else ""))
        if year != fiscal_year or period.upper() != f"Q{quarter}":
            raise FmpResponseError("FMP transcript financial period does not match its request")
        body = row.get("content")
        if not isinstance(body, str) or not body.strip():
            raise FmpResponseError("FMP transcript has no text")
        title = f"{symbol} · {year} Q{quarter} Earnings Call Transcript"
        url = BASE_URL + "/earning-call-transcript?" + urlencode({"symbol": symbol, "year": year, "quarter": quarter})
        identity = f"fmp-transcript-{symbol}-{year}-Q{quarter}"
        occurrence = row.get("date")
        # The API's date is the call date, not the date FMP published its text.
        publication = None
        completeness = "full_text"
    elif channel == "fmp:sec_filings":
        form = str(row.get("formType") or "").strip()
        if not form:
            raise FmpResponseError("FMP SEC record has no form type")
        url = _public_url(row.get("finalLink") or row.get("link"))
        title = f"{symbol} · SEC {form} · {str(row.get('filingDate') or '')[:10]}"
        body = title + "\n" + url
        identity = "fmp-sec-" + digest(canonical_json({"symbol": symbol, "url": row.get("link") or url}))
        completeness = "title_only"
        warnings.append("filing_metadata_only; document_body_not_acquired")
    else:
        title = str(row.get("title") or "").strip()
        if not title:
            raise FmpResponseError("FMP news record has no title")
        url = _public_url(row.get("url"))
        body = str(row.get("text") or "").strip() or title
        identity = "fmp-" + channel.split(":")[1] + "-" + digest(canonical_json({"symbol": symbol, "url": url}))
        completeness = "source_excerpt" if row.get("text") else "title_only"
        warnings.append("provider_excerpt; original_article_not_acquired")
    for value in (publication, occurrence):
        if value and len(str(value)) > 10 and len(_source_date(value) or "") == 10:
            warnings.append("source_timezone_unspecified; date_only")
    document = {
        "document_id": identity, "channel_id": channel,
        "source_name": "FMP" + (f" · {row['publisher']}" if row.get("publisher") else ""),
        "title": title, "url": url, "information_type": definition["information_type"],
        "status": "active", "published_at": _source_date(publication), "occurred_at": _source_date(occurrence),
        "observed_at": response.received_at.isoformat(), "raw_format": "application/json",
        "content_completeness": completeness, "content_warnings": warnings,
        "entities": [{"entity_id": "fmp:" + symbol, "label": symbol, "kind": "security"}], "event_ids": [],
        "provenance": {"provider": "fmp", "endpoint": response.endpoint, "symbol": symbol,
            "published_at_raw": publication, "occurred_at_raw": occurrence,
            "fiscal_year": fiscal_year, "quarter": quarter,
            "content_scope": definition["content_scope"], "publisher": row.get("publisher"),
            "filing_index_url": row.get("link") if channel == "fmp:sec_filings" else None},
    }
    return document, body, canonical_json(row)


class FmpTextCollector:
    def __init__(self, settings, *, store=None, client=None):
        self.store = store or TextStore(settings)
        self.client = client or FmpClient(settings.read_secret("fmp_api_key"))
        self.captured_source_ids = set()
        self.attempt_document_count = 0

    def _capture(self, response, row, symbol, channel, **kwargs):
        saved = self.store.capture_provider_document(*_record(response, row, symbol, channel, **kwargs))
        self.attempt_document_count += 1
        if saved["observed_at"] == response.received_at.isoformat():
            self.captured_source_ids.add(saved["source_id"])
        return saved

    def close(self):
        self.store.close()
        self.client.close()

    def _news_or_filings(self, symbol, channel, start, end):
        definition = CHANNELS[channel]
        size = 100 if channel == "fmp:sec_filings" else PAGE_SIZE
        count, fingerprints = 0, set()
        # SEC limits each request to a 90-day window. Split longer downtime,
        # rather than advancing the cursor over a provider-truncated response.
        windows = []
        cursor = start
        while cursor <= end:
            stop = min(cursor + timedelta(days=89), end)
            windows.append((cursor, stop))
            cursor = stop + timedelta(days=1)
        for lower, upper in windows:
            for page in range(100):
                params = {"symbol" if channel == "fmp:sec_filings" else "symbols": symbol,
                          "from": lower.isoformat(), "to": upper.isoformat(), "page": page, "limit": size}
                response = self.client.get_json(definition["endpoint"], params)
                rows = _rows(response)
                fingerprint = digest(response.body)
                if rows and fingerprint in fingerprints:
                    raise FmpResponseError("FMP publication pagination repeated a page")
                fingerprints.add(fingerprint)
                for row in rows:
                    self._capture(response, row, symbol, channel)
                    count += 1
                if len(rows) < size:
                    break
            else:
                raise FmpResponseError("FMP publication pagination is incomplete")
        return count, start, {"lookback_overlap_days": 7}

    def _transcripts(self, symbol, start, end):
        response = self.client.get_json("earning-call-transcript-dates", {"symbol": symbol})
        dates = []
        for row in _rows(response):
            try:
                call_date = date.fromisoformat(str(row["date"])[:10])
                year, quarter = int(row["fiscalYear"]), int(row["quarter"])
            except (ValueError, KeyError, TypeError):
                raise FmpResponseError("FMP transcript directory has an invalid financial period") from None
            if quarter not in {1, 2, 3, 4}:
                raise FmpResponseError("FMP transcript directory has an invalid quarter")
            if call_date <= end:
                dates.append((call_date, year, quarter))
        dates = sorted(set(dates), reverse=True)
        # Two most recent calls establish the current/previous-quarter baseline;
        # the overlap also includes newly available calls within the active window.
        selected = [item for index, item in enumerate(dates) if index < 2 or item[0] >= start]
        count, fetched = 0, 0
        for call_date, year, quarter in selected:
            # The provider can correct an older call that is still part of the
            # current comparison baseline. Re-read selected calls; the immutable
            # store deduplicates unchanged text without inventing new evidence.
            transcript = self.client.get_json("earning-call-transcript", {"symbol": symbol, "year": year, "quarter": quarter})
            rows = _rows(transcript)
            if len(rows) != 1:
                raise FmpResponseError("Listed FMP transcript is not yet available as one complete record")
            self._capture(transcript, rows[0], symbol, "fmp:earnings_transcripts", fiscal_year=year, quarter=quarter)
            count += 1
            fetched += 1
        return count, min([start, *(item[0] for item in selected)]), {
            "baseline": "latest_two_calls_and_calls_in_incremental_window", "directory_count": len(dates),
            "fetched_count": fetched, "selected_periods": [{"call_date": day.isoformat(), "fiscal_year": year, "quarter": quarter} for day, year, quarter in selected],
        }

    def collect(self, subjects, *, now=None):
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("FMP collection clock requires a timezone")
        self.captured_source_ids.clear()
        end, results = now.astimezone(UTC).date(), []
        kinds = {}
        for item in subjects:
            symbol = str(item["symbol"]).strip().upper()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,29}", symbol):
                raise ValueError("Invalid explicit FMP symbol")
            kind = item.get("kind", "company")
            if kind not in {"company", "fund"}:
                raise ValueError("FMP content subject must be a company or fund")
            if symbol in kinds and kinds[symbol] != kind:
                raise ValueError("Conflicting FMP subject types")
            kinds[symbol] = kind
        for symbol, kind in sorted(kinds.items()):
            for channel in CHANNELS if kind == "company" else FUND_CHANNELS:
                self.attempt_document_count = 0
                prior = self.store.last_successful_collection(symbol, channel)
                start = (prior["window_end"] - timedelta(days=7)) if prior else end - timedelta(days=30)
                receipt = {"symbol": symbol, "channel_id": channel, "window_start": start,
                    "window_end": end, "status": "ready", "document_count": 0,
                    "content_scope": CHANNELS[channel]["content_scope"], "error_type": None, "details": {}}
                try:
                    count, start, details = (self._transcripts(symbol, start, end) if channel == "fmp:earnings_transcripts"
                                             else self._news_or_filings(symbol, channel, start, end))
                    receipt.update(document_count=count, window_start=start, details=details)
                except Exception as exc:
                    # No raw exception text: upstream URLs may contain credentials.
                    receipt.update(status="partial" if self.attempt_document_count else "failed",
                                   document_count=self.attempt_document_count, error_type=type(exc).__name__,
                                   details={"http_status": getattr(exc, "status", None), "cursor_advanced": False})
                receipt["checked_at"] = datetime.now(UTC)
                results.append(self.store.record_collection(receipt))
        return {"status": "partial" if any(row["status"] != "ready" for row in results) else "ready",
                "subjects": len(kinds), "sources": results, "source_ids": sorted(self.captured_source_ids)}
