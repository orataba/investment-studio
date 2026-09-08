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
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .collect import Collector
from .providers.http_client import get_public_bytes
from .raw import archive_response
from .store import NumericStore

UTC = timezone.utc
HSIL_CODES = {
    "HSCI.HI": "00011.00", "HSCIEN.HI": "00011.01", "HSCIMT.HI": "00011.02",
    "HSCIIN.HI": "00011.03", "HSCITC.HI": "00011.06", "HSCIUT.HI": "00011.07",
    "HSCIFN.HI": "00011.08", "HSCIPC.HI": "00011.09", "HSCIIT.HI": "00011.10",
    "HSCICO.HI": "00011.11", "HSCICD.HI": "00011.12", "HSCICS.HI": "00011.13", "HSCIH.HI": "00011.14",
}


class DataHubIntermittentPermissionError(RuntimeError):
    """Observed gateway code 40203, retried by the existing page controller."""


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

    def _record_read(self, rows):
        captures = {(row["batch_id"], row.get("raw_ref"), row["observed_at"]) for row in rows}
        self.provenance.extend({"batch_id": batch, "raw_ref": raw, "observed_at": observed} for batch, raw, observed in sorted(captures))

    def _publish(self, response, rows, *, source, series):
        _, reference = archive_response(self.settings, source, response.body)
        normalized = []
        for row in rows:
            row = dict(row)
            row.setdefault("series_id", series)
            row["raw_ref"] = reference
            normalized.append(row)
        result = self.store.ingest("regime_market_daily", [normalized], source=source, observed_at=response.received_at,
            details={"endpoint": response.endpoint, "parameters": response.params, "series": series})
        self.provenance.append({"batch_id": result["batch_id"], "raw_ref": reference, "observed_at": response.received_at.isoformat()})
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
                    self.provenance.extend(batches)
                    rows = [row for batch in batches for row in self._rows("raw_eod_daily", batch_id=batch["batch_id"])]
                else:
                    rows = self._rows("raw_eod_daily", symbols=[symbol], start=start.isoformat(), end=end.isoformat())
                    self._record_read(rows)
                self._prepared_eod[identity] = rows
            rows = self._prepared_eod[identity]
            if endpoint.endswith("dividend-adjusted"):
                return [{"symbol": symbol, "date": row["date"], "adjOpen": row["adjusted_open"], "adjHigh": row["adjusted_high"], "adjLow": row["adjusted_low"], "adjClose": row["adjusted_close"], "volume": row["volume"]} for row in rows]
            return [{"symbol": symbol, "date": row["date"], "adjOpen": row["open"], "adjHigh": row["high"], "adjLow": row["low"], "adjClose": row["close"], "volume": row["volume"]} for row in rows]
        if endpoint != "historical-price-eod/full":
            raise ValueError("Unsupported Regime FMP history endpoint")
        # Index, spot metal, currency and BTC histories retain their own raw basis.
        series = "fmp:raw:" + symbol
        if self.role == "replica":
            rows = self._rows("regime_market_daily", symbols=[series], start=start.isoformat(), end=end.isoformat())
            self._record_read(rows)
            return [{**row, "symbol": row["provider_symbol"]} for row in rows]
        cursor = start
        captured_rows = []
        while cursor <= end:
            stop = min(cursor + timedelta(days=1459), end)
            response = self.collector.fmp.get_json(endpoint, {"symbol": symbol, "from": cursor.isoformat(), "to": stop.isoformat()})
            if not isinstance(response.payload, list):
                raise ValueError("FMP raw daily response must be a list")
            rows = []
            for incoming in response.payload:
                if incoming.get("symbol") != symbol:
                    raise ValueError("FMP raw daily symbol does not match its requested series")
                day = date.fromisoformat(str(incoming["date"]))
                if not cursor <= day <= stop:
                    continue
                close = float(incoming["close"])
                if not math.isfinite(close) or close <= 0:
                    raise ValueError("FMP raw close must be finite and positive")
                rows.append({**incoming, "date": day, "provider_symbol": symbol, "ohlc_adjustment": "provider_raw", "adjustment_factor": 1.0})
            captured_rows.extend(self._publish(response, rows, source="fmp", series=series))
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
        # This credential is the existing DataHub gateway key, not a Tushare token.
        with requests.Session(trust_env=False) as session:
            response = session.get(url, headers={"X-API-Key": key, "Accept": "application/json"}, params=parameters, timeout=30)
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
        captured = SimpleNamespace(body=body, received_at=observed, endpoint=url, params=parameters)
        rows = []
        for item in items:
            row = dict(zip(fields, item))
            day = str(row.get("trade_date") or row.get("cal_date"))
            series = "datahub:" + endpoint + ":" + str(row.get("ts_code") or row.get("exchange") or parameters.get("exchange") or "all")
            rows.append({**row, "date": datetime.strptime(day, "%Y%m%d").date(), "series_id": series})
        stored = self._publish(captured, rows, source="datahub", series="datahub:" + endpoint)
        return [{field: row.get(field) for field in fields} for row in stored], has_more
