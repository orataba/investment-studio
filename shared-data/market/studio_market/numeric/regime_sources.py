"""Regime's public source acquisition, owned and archived by Studio's numeric layer.

Regime keeps its model input snapshots and transformations. HTTP responses are
preserved here and observations are published through NumericStore before use.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import io
import gzip
import os
import json
import math
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .collect import Collector
from .providers.http_client import get_public_bytes
from .raw import archive_response
from .store import NumericStore
from .schema import batches

UTC = timezone.utc
HSIL_CODES = {
    "HSCI.HI": "00011.00", "HSCIEN.HI": "00011.01", "HSCIMT.HI": "00011.02",
    "HSCIIN.HI": "00011.03", "HSCITC.HI": "00011.06", "HSCIUT.HI": "00011.07",
    "HSCIFN.HI": "00011.08", "HSCIPC.HI": "00011.09", "HSCIIT.HI": "00011.10",
    "HSCICO.HI": "00011.11", "HSCICD.HI": "00011.12", "HSCICS.HI": "00011.13", "HSCIH.HI": "00011.14",
}


class DataHubIntermittentPermissionError(RuntimeError):
    """Observed gateway code 40203, retried by the existing page controller."""


def _fmp_raw_dates(payload, *, symbol, start, end):
    """Isolate invalid known dates without changing raw or close-only semantics."""
    if not isinstance(payload, list):
        raise ValueError("FMP raw daily response must be a list")
    accepted, rejected, rejected_dates = {}, [], set()
    for index, incoming in enumerate(payload):
        if not isinstance(incoming, Mapping) or incoming.get("symbol") != symbol:
            raise ValueError("FMP raw daily symbol or row does not match its requested series")
        try:
            day = date.fromisoformat(str(incoming["date"]))
        except (KeyError, ValueError):
            raise ValueError("FMP raw daily date cannot be identified") from None
        if not start <= day <= end:
            continue
        row = {**incoming, "date": day, "provider_symbol": symbol,
               "ohlc_adjustment": "provider_raw", "adjustment_factor": 1.0}
        reason = None
        for field in ("close", "open", "high", "low", "volume"):
            if (field != "close" and field not in incoming) or (field == "volume" and incoming[field] is None):
                continue
            try:
                value = float(incoming.get(field))
                valid = math.isfinite(value) and (value >= 0 if field == "volume" else value > 0)
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                reason = f"FMP raw {field} must be finite and {'nonnegative' if field == 'volume' else 'positive'}"
                break
        if reason is None and day in accepted and accepted[day] != row:
            reason = "FMP raw daily response contains conflicting rows for this date"
        if reason:
            accepted.pop(day, None)
            rejected_dates.add(day)
            rejected.append({"date": day.isoformat(), "row_index": index, "reason": reason})
        elif day not in rejected_dates:
            accepted[day] = row
    return [accepted[day] for day in sorted(accepted)], rejected


class RegimeSources:
    def __init__(self, settings=None, *, store=None, fmp_client=None, role=None):
        if settings is None:
            from studio_market.config import MarketSettings
            settings = MarketSettings.from_environment()
        self.settings = settings
        self.role = role or os.environ.get("INVESTMENT_STUDIO_MARKET_ROLE", "replica")
        if self.role not in {"collector", "replica"}:
            raise ValueError("Market role must be collector or replica")
        self.store = store or NumericStore(settings)
        self.collector = Collector(settings, store=self.store, client=fmp_client)
        self._prepared_eod = {}
        self.provenance: list[dict] = []

    def close(self):
        if self.collector._client is not None:
            self.collector._client.close()
        self.store.close()

    def _rows(self, name, *, symbols=None, start=None, end=None, batch_id=None):
        result, offset = [], 0
        while True:
            page = self.store.query(name, symbols=symbols, start=start, end=end, batch_id=batch_id, limit=100000, offset=offset)
            result.extend(page["rows"])
            offset += len(page["rows"])
            if offset >= page["total"]:
                return result
            if not page["rows"]:
                raise ValueError("Shared numeric pagination stopped before the declared total")

    def _record_read(self, rows, *, series=None, start=None, end=None):
        captures = {row["batch_id"]: {"batch_id": row["batch_id"], "raw_ref": row.get("raw_ref"),
                                    "observed_at": row["observed_at"],
                                    **({"adjusted_raw_ref": row["adjusted_raw_ref"]} if row.get("adjusted_raw_ref") else {})} for row in rows}
        scope = batches.c.id.in_(list(captures))
        if series:
            scope = scope | ((batches.c.dataset == "regime_market_daily") & (batches.c.status == "ready") &
                (batches.c.details["series"].as_string() == series) &
                (batches.c.details["parameters"]["from"].as_string() <= end.isoformat()) &
                (batches.c.details["parameters"]["to"].as_string() >= start.isoformat()))
        with self.store.engine.connect() as connection:
            details = dict(connection.execute(select(batches.c.id, batches.c.details).where(scope)).all())
        complete = [detail for detail in details.values() if series and detail.get("series") == series
                    and not detail.get("validation") and detail.get("observed_at")]

        def corrected(observed, lower, upper):
            return any(datetime.fromisoformat(detail["observed_at"]) > observed
                       and detail["parameters"]["from"] <= lower
                       and detail["parameters"]["to"] >= upper for detail in complete)

        for batch, detail in details.items():
            validation = detail.get("validation")
            if not validation:
                continue
            scoped = validation
            if series and detail.get("series") == series:
                observed = datetime.fromisoformat(detail["observed_at"])
                if validation["status"] == "partial":
                    rejected = [item for item in validation["rejected_rows"]
                        if start.isoformat() <= item["date"] <= end.isoformat()
                        and not corrected(observed, item["date"], item["date"])
                        and not any(row["date"] == item["date"] and datetime.fromisoformat(row["observed_at"]) > observed for row in rows)]
                    scoped = {**validation, "rejected_rows": rejected} if rejected else None
                elif validation["status"] == "failed":
                    lower = max(start.isoformat(), detail["parameters"]["from"])
                    upper = min(end.isoformat(), detail["parameters"]["to"])
                    if lower > upper or corrected(observed, lower, upper):
                        scoped = None
            if scoped:
                # Include a relevant rejection even when its batch contributes
                # no selected facts; complete newer captures resolve the warning.
                capture = captures.setdefault(batch, {"batch_id": batch, "raw_ref": detail.get("raw_ref"),
                                                      "observed_at": detail["observed_at"]})
                capture["validation"] = scoped
        self.provenance.extend(captures[batch] for batch in sorted(captures))

    def _publish(self, response, rows, *, source, series, raw_ref=None, validation=None):
        reference = raw_ref or archive_response(self.settings, source, response.body)[1]
        normalized = []
        for row in rows:
            row = dict(row)
            row.setdefault("series_id", series)
            row["raw_ref"] = reference
            normalized.append(row)
        evidence = {"validation": validation} if validation else {}
        result = self.store.ingest("regime_market_daily", [normalized], source=source, observed_at=response.received_at,
            details={"endpoint": response.endpoint, "parameters": response.params, "series": series,
                     "raw_ref": reference, "observed_at": response.received_at.isoformat(), **evidence})
        self.provenance.append({"batch_id": result["batch_id"], "raw_ref": reference, "observed_at": response.received_at.isoformat(), **evidence})
        return self._rows("regime_market_daily", batch_id=result["batch_id"])

    def fmp_payload(self, endpoint: str, params: dict) -> list[dict]:
        symbol = str(params["symbol"])
        start, end = date.fromisoformat(str(params["from"])), date.fromisoformat(str(params["to"]))
        if endpoint in {"historical-price-eod/non-split-adjusted", "historical-price-eod/dividend-adjusted"}:
            identity = (symbol, start, end)
            if identity not in self._prepared_eod:
                if self.role == "collector":
                    before = len(self.collector.results)
                    acquired = self.collector.raw_eod(start, end, [symbol])
                    if acquired['status'] != 'ready':
                        raise ValueError('Shared Regime price acquisition or revision is incomplete')
                    batches = self.collector.results[before:]
                    rows = [row for batch in batches for row in self._rows("raw_eod_daily", batch_id=batch["batch_id"])]
                else:
                    rows = self._rows("raw_eod_daily", symbols=[symbol], start=start.isoformat(), end=end.isoformat())
                self._prepared_eod[identity] = rows
            rows = self._prepared_eod[identity]
            self._record_read(rows)
            if endpoint.endswith("dividend-adjusted"):
                return [{"symbol": symbol, "date": row["date"], "adjOpen": row["adjusted_open"], "adjHigh": row["adjusted_high"], "adjLow": row["adjusted_low"], "adjClose": row["adjusted_close"], "volume": row["volume"]} for row in rows]
            return [{"symbol": symbol, "date": row["date"], "adjOpen": row["open"], "adjHigh": row["high"], "adjLow": row["low"], "adjClose": row["close"], "volume": row["volume"]} for row in rows]
        if endpoint != "historical-price-eod/full":
            raise ValueError("Unsupported Regime FMP history endpoint")
        # Index, spot metal, currency and BTC histories retain their own raw basis.
        series = "fmp:raw:" + symbol
        if self.role == "replica":
            rows = self._rows("regime_market_daily", symbols=[series], start=start.isoformat(), end=end.isoformat())
            self._record_read(rows, series=series, start=start, end=end)
            return [{**row, "symbol": row["provider_symbol"]} for row in rows]
        cursor = start
        captured_rows = []
        while cursor <= end:
            stop = min(cursor + timedelta(days=1459), end)
            response = self.collector.fmp.get_json(endpoint, {"symbol": symbol, "from": cursor.isoformat(), "to": stop.isoformat()})
            _, reference = archive_response(self.settings, "fmp", response.body)
            try:
                rows, rejected = _fmp_raw_dates(response.payload, symbol=symbol, start=cursor, end=stop)
            except ValueError as error:
                self._publish(response, [], source="fmp", series=series, raw_ref=reference,
                    validation={"status": "failed", "accepted_row_count": 0, "reason": str(error)})
                raise
            validation = {"status": "partial", "accepted_row_count": len(rows), "rejected_rows": rejected} if rejected else None
            captured_rows.extend(self._publish(response, rows, source="fmp", series=series,
                raw_ref=reference, validation=validation))
            cursor = stop + timedelta(days=1)
        return [{**row, "symbol": row["provider_symbol"]} for row in captured_rows]

    def hk_public_payload(self, url: str) -> dict:
        hsi_urls = {f"https://www.hsi.com.hk/data/eng/indexes/{code}/chart.json": symbol for symbol, code in HSIL_CODES.items()}
        parsed = urlsplit(url)
        if url not in hsi_urls and not (parsed.scheme == "https" and parsed.netloc == "www.hkab.org.hk" and parsed.path == "/api/hibor"):
            raise ValueError("Regime HK source must be a configured official index or HIBOR URL")
        if self.role == "replica":
            if url in hsi_urls:
                rows = self.store.latest("regime_market_daily", symbols=["hsil:" + hsi_urls[url]])["rows"]
            else:
                query = parse_qs(parsed.query)
                day = date(int(query["year"][0]), int(query["month"][0]), int(query["day"][0])).isoformat()
                rows = self._rows("regime_market_daily", symbols=["hkab:hibor"], start=day, end=day)
            if not rows:
                raise ValueError("Shared HK source is unavailable; wait for collector delivery")
            self._record_read(rows)
            return json.loads(gzip.decompress((self.settings.data_root / rows[0]["raw_ref"]).read_bytes()))
        response = get_public_bytes(url)
        payload = json.loads(response.body)
        if not isinstance(payload, dict):
            raise ValueError("HK official response must be an object")
        if url in hsi_urls:
            symbol = hsi_urls[url]
            if str(payload.get("indexCode", "")).strip() != HSIL_CODES[symbol]:
                raise ValueError("HSIL response identity mismatch")
            points = payload.get("indexLevels-5y")
            if not isinstance(points, list) or not points:
                raise ValueError("HSIL official five-year history is empty")
            rows = [{"date": datetime.fromtimestamp(float(point[0]) / 1000, UTC).astimezone(ZoneInfo("Asia/Hong_Kong")).date(), "close": float(point[1])} for point in points]
            if any(not math.isfinite(row["close"]) or row["close"] <= 0 for row in rows):
                raise ValueError("HSIL levels must be finite and positive")
            self._publish(response, rows, source="hsil", series="hsil:" + symbol)
        else:
            query = parse_qs(parsed.query)
            day = date(int(query["year"][0]), int(query["month"][0]), int(query["day"][0]))
            holiday = bool(payload.get("isHoliday"))
            rows = [{"date": day, "hibor_1m": None if holiday else payload.get("1 Month"), "hibor_3m": None if holiday else payload.get("3 Months"), "is_holiday": holiday}]
            for field in ("hibor_1m", "hibor_3m"):
                value = rows[0][field]
                if value is not None:
                    value = float(value)
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("HIBOR must be finite and nonnegative")
                    rows[0][field] = value
            self._publish(response, rows, source="hkab", series="hkab:hibor")
        return payload

    def fred_rows(self, series: str, start: date, end: date) -> list[dict]:
        if series != "BAMLH0A0HYM2":
            raise ValueError("Regime FRED intake is scoped to its declared OAS series")
        if self.role == "replica":
            rows = self._rows("regime_market_daily", symbols=["fred:" + series], start=start.isoformat(), end=end.isoformat())
            self._record_read(rows)
            return [{"date": row["date"], "value": row["value"]} for row in rows]
        response = get_public_bytes("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": series, "cosd": start.isoformat(), "coed": end.isoformat()})
        reader = csv.DictReader(io.StringIO(response.body.decode("utf-8-sig")))
        rows = []
        for row in reader:
            day = date.fromisoformat(row.get("observation_date") or row["DATE"])
            if start <= day <= end and row[series] not in {"", "."}:
                value = float(row[series])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("FRED OAS contains an invalid observation")
                rows.append({"date": day, "value": value})
        stored = self._publish(response, rows, source="fred", series="fred:" + series)
        return [{"date": row["date"], "value": row["value"]} for row in stored]

    def datahub_page(self, endpoint: str, parameters: dict) -> tuple[list[dict], bool]:
        if endpoint not in {"trade_cal", "fund_daily", "fund_adj", "index_daily"}:
            raise ValueError("Regime DataHub intake is scoped to its declared daily endpoints")
        if self.role == "replica":
            identity = parameters.get("ts_code") or parameters.get("exchange") or "all"
            def day(value):
                return datetime.strptime(str(value), "%Y%m%d").date().isoformat() if value else None
            rows = self._rows("regime_market_daily", symbols=["datahub:" + endpoint + ":" + str(identity)], start=day(parameters.get("start_date")), end=day(parameters.get("end_date")))
            rows.sort(key=lambda row: row["date"], reverse=True)
            offset, limit = int(parameters.get("offset", 0)), int(parameters.get("limit", 6000))
            selected = rows[offset:offset+limit]
            self._record_read(selected)
            fields = [field for field in str(parameters.get("fields", "")).split(",") if field]
            return [{field: row.get(field) for field in fields} for row in selected], offset + limit < len(rows)
        from curl_cffi import requests
        key = self.settings.read_secret("datahub_api_key")
        url = self.settings.datahub_api_url.rstrip("/") + "/" + endpoint.replace("_", "-")
        # The DataHub REST gateway accepts at most 5,000 rows per page, while
        # callers may use Tushare's larger page size. The caller advances by
        # actual returned rows and uses has_more, so the smaller page is complete.
        limit = int(parameters.get("limit", 5000))
        if limit < 1:
            raise ValueError("DataHub daily page limit must be positive")
        effective_parameters = {**parameters, "limit": min(limit, 5000)}
        # This credential is the existing DataHub gateway key, not a Tushare token.
        with requests.Session(trust_env=False) as session:
            response = session.get(url, headers={"X-API-Key": key, "Accept": "application/json"}, params=effective_parameters, timeout=30)
            if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                # Do not let deterministic request/authentication failures enter
                # the consumer's transport retry path or expose response secrets.
                raise ValueError(f"DataHub daily request rejected with HTTP {response.status_code}")
            response.raise_for_status()
            body = response.content
        observed = datetime.now(UTC)
        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("code") == 40203:
            raise DataHubIntermittentPermissionError("DataHub daily gateway returned intermittent permission response")
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError("DataHub daily response rejected the authorized request")
        data = payload.get("data") or {}
        fields, items, has_more = data.get("fields"), data.get("items"), data.get("has_more")
        if not isinstance(fields, list) or not fields or any(not isinstance(field, str) or not field for field in fields) or not isinstance(items, list) or not isinstance(has_more, bool):
            raise ValueError("DataHub daily response has an invalid page envelope")
        if len(set(fields)) != len(fields) or any(not isinstance(item, list) or len(item) != len(fields) for item in items):
            raise ValueError("DataHub daily page has invalid columns or row widths")
        if not items and has_more:
            raise ValueError("DataHub empty page incorrectly reports more rows")
        from types import SimpleNamespace
        captured = SimpleNamespace(body=body, received_at=observed, endpoint=url, params=effective_parameters)
        rows = []
        for item in items:
            row = dict(zip(fields, item))
            day = str(row.get("trade_date") or row.get("cal_date"))
            series = "datahub:" + endpoint + ":" + str(row.get("ts_code") or row.get("exchange") or parameters.get("exchange") or "all")
            rows.append({**row, "date": datetime.strptime(day, "%Y%m%d").date(), "series_id": series})
        stored = self._publish(captured, rows, source="datahub", series="datahub:" + endpoint)
        return [{field: row.get(field) for field in fields} for row in stored], has_more
