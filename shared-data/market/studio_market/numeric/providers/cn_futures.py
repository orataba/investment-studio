

"""Provider normalization and request contracts owned by Investment Studio."""

from __future__ import annotations
from .cn_futures_contracts import parse_contract_identity

import gzip

import hashlib

import json

import re

import time

import urllib.error

import urllib.request

from collections.abc import Callable, Mapping

from dataclasses import dataclass

from datetime import date, datetime, timedelta, timezone

from pathlib import Path

from typing import Any

from zoneinfo import ZoneInfo

MAX_RESPONSE_BYTES = 64 * 1024 * 1024

SHANGHAI = ZoneInfo("Asia/Shanghai")

CN_FUTURES_RETENTION_START = date(2018, 1, 1)

CN_FUTURES_OBSERVATION_NORMALIZATION_VERSION = "gtja_observation_v4"

CN_FUTURES_SNAPSHOT_LOOKBACK_DAYS = 31

_EXCHANGE_ALIASES = {"ZCE": "CZCE"}

class CnFuturesError(RuntimeError):
    """Sanitized China-futures collection error."""

@dataclass(frozen=True)
class CnFuturesProduct:
    product_id: str
    exchange_id: str
    name: str
    active: bool

@dataclass(frozen=True)
class EndpointSpec:
    name: str
    path: str
    dataset: str
    records_path: str
    scope: str
    metric: str
    clock_capability: str
    clock_precision: str
    revision_scope: str
    enabled: bool = True
    notes: str = ""

@dataclass(frozen=True)
class RequestContext:
    product: CnFuturesProduct | None = None
    exchange_id: str | None = None
    products_by_id: Mapping[str, CnFuturesProduct] | None = None

@dataclass(frozen=True)
class GTJAResponse:
    endpoint: str
    params: dict[str, object]
    received_at: datetime
    http_status: int
    body: bytes
    payload: Any
    json_valid: bool

    @property
    def business_code(self) -> int | None:
        if not isinstance(self.payload, dict) or "code" not in self.payload:
            return None
        try:
            return int(self.payload["code"])
        except (TypeError, ValueError):
            return None

    @property
    def message(self) -> str | None:
        if isinstance(self.payload, dict) and self.payload.get("msg") is not None:
            return str(self.payload["msg"])
        return None

    @property
    def succeeded(self) -> bool:
        return (
            200 <= self.http_status < 300
            and self.json_valid
            and self.business_code in (None, 0)
        )

ENDPOINTS = (
    EndpointSpec(
        "inventory_by_code",
        "/api/unicorn.cloudApi.inventoryData.queryByCode.do",
        "gtja_cn_futures_inventory",
        "data",
        "product_snapshot",
        "inventory",
        "provider_snapshot_date",
        "date",
        "provider_report_date_versions",
        notes=(
            "reportDate is the provider historical snapshot date, not publication time; "
            "the response does not provide a unit field"
        ),
    ),
    EndpointSpec(
        "process_profit_by_code",
        "/api/unicorn.cloudApi.processProfitData.queryByCode.do",
        "gtja_cn_futures_process_profit",
        "data",
        "product_snapshot",
        "process_profit",
        "provider_snapshot_date",
        "date",
        "provider_report_date_versions",
        notes=(
            "processing and import-profit history by requested reportDate; the "
            "response does not provide a unit field"
        ),
    ),
    EndpointSpec(
        "basis",
        "/api/unicorn.cloudApi.basisData.query.do",
        "gtja_cn_futures_basis",
        "data",
        "product_report_day",
        "basis",
        "provider_observation_date",
        "date",
        "versions_since_first_capture",
    ),
    EndpointSpec(
        "warehouse_stock",
        "/api/unicorn.cloudApi.fut.warehouseStock.query.do",
        "gtja_cn_futures_warehouse_stock",
        "data",
        "product_report_day",
        "warehouse_on_warrant",
        "provider_observation_date",
        "date",
        "versions_since_first_capture",
        notes="the response does not provide a physical unit field",
    ),
    EndpointSpec(
        "member_rank",
        "/api/unicorn.cloudApi.dragonTigerList.queryByCode.do",
        "gtja_cn_futures_member_rank",
        "data",
        "product_trading_day",
        "member_rank",
        "provider_observation_date",
        "date",
        "versions_since_first_capture",
        notes="the response does not provide a unit field for value or change",
    ),
    EndpointSpec(
        "money_flow",
        "/api/unicorn.cloudApi.moneyFlowDateRangeQuery.do",
        "gtja_cn_futures_money_flow",
        "data",
        "product_date_range",
        "money_flow",
        "provider_update_timestamp_when_present",
        "timestamp_or_date",
        "versions_since_first_capture",
        notes=(
            "the response does not provide an inflow unit; historical updateTime "
            "values can reflect a provider bulk-load timestamp"
        ),
    ),
    EndpointSpec(
        "futures_trade_params",
        "/api/unicorn.cloudApi.contractTradeParams.query.do",
        "gtja_cn_futures_trade_params",
        "data",
        "exchange_trading_day",
        "trade_rule",
        "provider_effective_date",
        "date",
        "effective_dated_versions",
    ),
    EndpointSpec(
        "futures_exchange_fee",
        "/api/unicorn.cloudApi.exchangeCharge.query.do",
        "gtja_cn_futures_exchange_fees",
        "data",
        "exchange_trading_day",
        "exchange_fee_rule",
        "provider_effective_date",
        "date",
        "effective_dated_versions",
        notes="exchange fees only; broker commission is excluded",
    ),
    EndpointSpec(
        "declaration_fee",
        "/api/unicorn.cloudApi.declaredRate.queryByCode.do",
        "gtja_cn_futures_declaration_fees",
        "data",
        "product_exchange_trading_day",
        "declaration_fee_rule",
        "provider_effective_date",
        "date",
        "effective_dated_versions",
    ),
    EndpointSpec(
        "futures_contract_price",
        "/api/unicorn.cloudApi.futuresContractPrice.queryByCode.do",
        "gtja_cn_futures_contract_prices",
        "data",
        "product_compact_trading_day",
        "contract_daily_price",
        "provider_update_timestamp_when_present",
        "timestamp_or_date",
        "provider_recent_history_versions_since_capture",
        notes="raw totalPosition is retained; zero active positions are not trusted as OI",
    ),
    EndpointSpec(
        "exchange_daily_reminder",
        "/api/unicorn.cloudApi.tradingcalendar.dailyReminder.do",
        "gtja_cn_futures_exchange_reminders",
        "data.list",
        "exchange_report_day",
        "exchange_reminder",
        "provider_event_date_not_calendar",
        "date",
        "archive_versions_since_capture",
        notes="despite its API title, this is an event reminder, not an open-day calendar",
    ),
    EndpointSpec(
        "exchange_notice",
        "/api/unicorn.cloudApi.news.query.do",
        "gtja_cn_futures_exchange_notices",
        "data.recordList",
        "notice_archive",
        "exchange_notice",
        "provider_publication_clock_when_present",
        "date_or_timestamp",
        "archive_versions_since_capture",
    ),
    EndpointSpec(
        "daily_viewpoint",
        "/api/unicorn.cloudApi.commodity.daily.viewpoint.query.do",
        "gtja_cn_futures_daily_viewpoints",
        "data.recordList",
        "daily_viewpoint_archive",
        "daily_viewpoint",
        "provider_report_date",
        "date",
        "archive_versions_since_capture",
    ),
    EndpointSpec(
        "weekly_viewpoint",
        "/api/unicorn.cloudApi.commodity.weekly.viewpoint.query.do",
        "gtja_cn_futures_weekly_viewpoints",
        "data.recordList",
        "weekly_viewpoint_archive",
        "weekly_viewpoint",
        "provider_report_date",
        "date",
        "archive_versions_since_capture",
    ),
    EndpointSpec(
        "monthly_viewpoint",
        "/api/unicorn.cloudApi.commodityMonthlyViewpoint.query.do",
        "gtja_cn_futures_monthly_viewpoints",
        "data",
        "monthly_viewpoint_archive",
        "monthly_viewpoint",
        "provider_report_date",
        "date",
        "archive_versions_since_capture",
    ),
    EndpointSpec(
        "quick_comment",
        "/api/unicorn.cloudApi.quickCommentList.do",
        "gtja_cn_futures_quick_comments",
        "data",
        "quick_comment_archive",
        "quick_comment",
        "provider_timestamp_when_present",
        "timestamp_or_date",
        "not_collected_without_entitlement",
        enabled=False,
        notes="the current account returned business code 300006",
    ),
    EndpointSpec(
        "vendor_factor_nav",
        "/api/unicorn.cloudApi.investdata.factor.nav.get.do",
        "gtja_cn_futures_vendor_factor_nav",
        "data.factorList",
        "factor_archive",
        "vendor_factor_nav",
        "provider_observation_date",
        "date",
        "archive_versions_since_capture",
    ),
    EndpointSpec(
        "research_report_attachment",
        "/api/unicorn.cloudApi.researchReportAttachmentQuery.do",
        "gtja_cn_futures_research_report_attachments",
        "data",
        "disabled",
        "research_report_attachment",
        "unknown",
        "unknown",
        "not_collected_without_entitlement",
        enabled=False,
        notes="the current account returned business code 300006",
    ),
    EndpointSpec(
        "inventory_page_discovery",
        "/api/unicorn.cloudApi.inventoryData.query.do",
        "gtja_cn_futures_inventory_discovery",
        "data.recordList",
        "disabled",
        "inventory_discovery",
        "no_provider_snapshot_date",
        "unknown",
        "not_collected_missing_report_date",
        enabled=False,
    ),
    EndpointSpec(
        "process_profit_page_discovery",
        "/api/unicorn.cloudApi.processProfitData.query.do",
        "gtja_cn_futures_process_profit_discovery",
        "data.recordList",
        "disabled",
        "process_profit_discovery",
        "no_provider_snapshot_date",
        "unknown",
        "not_collected_missing_report_date",
        enabled=False,
    ),
)

class GTJAFuturesClient:
    """Read-only client; credentials are sent only in request headers."""

    def __init__(
        self,
        base_url: str,
        access_key_id: str,
        access_key_secret: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not access_key_id or not access_key_secret:
            raise ValueError("GTJA credentials are required")
        self.base_url = base_url.rstrip("/")
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.timeout_seconds = timeout_seconds

    def post_json(
        self,
        endpoint: str,
        params: Mapping[str, object],
    ) -> GTJAResponse:
        safe_params = {
            str(key): value for key, value in params.items() if value is not None
        }
        body = json.dumps(
            safe_params,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=body,
            method="POST",
            headers={
                "accessKeyId": self.access_key_id,
                "accessKeySecret": self.access_key_secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "investment-studio-market/0.1 personal-research",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status = int(response.status)
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            response_body = exc.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CnFuturesError(
                f"GTJA transport failed before a response was received: {type(exc).__name__}"
            ) from None
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise CnFuturesError("GTJA response exceeded 64 MB")
        try:
            payload: Any = json.loads(response_body.decode("utf-8"))
            json_valid = True
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
            json_valid = False
        return GTJAResponse(
            endpoint=endpoint,
            params=safe_params,
            received_at=datetime.now(timezone.utc),
            http_status=status,
            body=response_body,
            payload=payload,
            json_valid=json_valid,
        )

    def close(self) -> None:
        return None

def load_cn_futures_coverage(
    path: Path,
) -> tuple[list[CnFuturesProduct], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    exchange_names = {
        str(item["exchange_id"]).upper(): str(item["name"])
        for item in payload.get("exchanges", [])
    }
    products = [
        CnFuturesProduct(
            product_id=str(item["product_id"]).upper(),
            exchange_id=str(item["exchange_id"]).upper(),
            name=str(item["name"]),
            active=bool(item.get("active", True)),
        )
        for item in payload.get("products", [])
    ]
    if not exchange_names or not products:
        raise ValueError("China futures coverage must contain exchanges and products")
    product_ids = [item.product_id for item in products]
    if len(product_ids) != len(set(product_ids)):
        raise ValueError("China futures coverage contains duplicate product IDs")
    unknown = sorted(
        {item.exchange_id for item in products}.difference(exchange_names)
    )
    if unknown:
        raise ValueError(f"China futures products reference unknown exchanges: {unknown}")
    return products, exchange_names

def previous_weekday(reference_date: date) -> date:
    """Return the preceding weekday; exchange holidays remain explicit API empty captures."""
    candidate = reference_date - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate

def _requests_for_endpoint(
    spec: EndpointSpec,
    products: list[CnFuturesProduct],
    exchange_names: dict[str, str],
    as_of: date,
) -> list[tuple[dict[str, object], RequestContext]]:
    iso = as_of.isoformat()
    compact = as_of.strftime("%Y%m%d")
    if spec.scope == "product_snapshot":
        start_date = max(
            CN_FUTURES_RETENTION_START,
            as_of - timedelta(days=CN_FUTURES_SNAPSHOT_LOOKBACK_DAYS),
        )
        return [
            (
                {
                    "code": item.product_id.lower(),
                    "reportDate": iso,
                    "startDataDate": start_date.isoformat(),
                    "endDataDate": iso,
                },
                RequestContext(product=item, exchange_id=item.exchange_id),
            )
            for item in products
        ]
    if spec.scope == "product_report_day":
        start = (as_of - timedelta(days=7)).isoformat()
        return [
            (
                {
                    "code": item.product_id,
                    "startReportDate": start,
                    "endReportDate": iso,
                },
                RequestContext(product=item, exchange_id=item.exchange_id),
            )
            for item in products
        ]
    if spec.scope == "product_trading_day":
        return [
            (
                {"code": item.product_id.lower(), "tradingDay": iso},
                RequestContext(product=item, exchange_id=item.exchange_id),
            )
            for item in products
        ]
    if spec.scope == "product_date_range":
        start = (as_of - timedelta(days=7)).isoformat()
        return [
            (
                {
                    "code": item.product_id.lower(),
                    "startDate": start,
                    "endDate": iso,
                },
                RequestContext(product=item, exchange_id=item.exchange_id),
            )
            for item in products
        ]
    if spec.scope == "product_compact_trading_day":
        return [
            (
                {"code": item.product_id.lower(), "tradingDay": compact},
                RequestContext(product=item, exchange_id=item.exchange_id),
            )
            for item in products
        ]
    if spec.scope == "exchange_trading_day":
        return [
            (
                {"exchangeCode": exchange_id, "tradingDay": compact},
                RequestContext(exchange_id=exchange_id),
            )
            for exchange_id in sorted(exchange_names)
        ]
    if spec.scope == "product_exchange_trading_day":
        return [
            (
                {
                    "code": item.product_id.lower(),
                    "exchangeCode": item.exchange_id,
                    "tradingDay": compact,
                },
                RequestContext(product=item, exchange_id=item.exchange_id),
            )
            for item in products
        ]
    if spec.scope == "exchange_report_day":
        return [
            (
                {"exchangeCode": exchange_id, "date": iso},
                RequestContext(exchange_id=exchange_id),
            )
            for exchange_id in sorted(exchange_names)
        ]
    if spec.scope == "notice_archive":
        start = (as_of - timedelta(days=7)).isoformat()
        return [
            (
                {"startDate": start, "endDate": iso, "pageNo": 1, "pageSize": 1000},
                RequestContext(),
            )
        ]
    if spec.scope in {"daily_viewpoint_archive", "weekly_viewpoint_archive"}:
        days = 7 if spec.scope.startswith("daily") else 21
        start = (as_of - timedelta(days=days)).isoformat()
        return [
            (
                {"page": 1, "size": 1000, "startReportDate": start, "endReportDate": iso},
                RequestContext(),
            )
        ]
    if spec.scope == "monthly_viewpoint_archive":
        return [
            (
                {
                    "startReportDate": (as_of - timedelta(days=62)).isoformat(),
                    "endReportDate": iso,
                },
                RequestContext(),
            )
        ]
    if spec.scope == "quick_comment_archive":
        return [
            (
                {
                    "startReportDate": (as_of - timedelta(days=7)).isoformat(),
                    "endReportDate": iso,
                },
                RequestContext(),
            )
        ]
    if spec.scope == "factor_archive":
        return [
            (
                {
                    "page": 1,
                    "size": 1000,
                    "factorType": "cta",
                    "startDate": (as_of - timedelta(days=7)).isoformat(),
                    "endDate": iso,
                },
                RequestContext(),
            )
        ]
    raise ValueError(f"unsupported request scope: {spec.scope}")

def _extract_records(payload: Any, records_path: str) -> list[dict[str, Any]]:
    current = payload
    for part in records_path.split("."):
        if not isinstance(current, dict):
            return []
        current = current.get(part)
    if current is None:
        return []
    if isinstance(current, list):
        return [item if isinstance(item, dict) else {"value": item} for item in current]
    if isinstance(current, dict):
        return [current]
    return [{"value": current}]

def normalize_gtja_records(
    spec: EndpointSpec,
    records: list[dict[str, Any]],
    *,
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in records:
        if context.products_by_id is not None:
            record_product_id = _upper(record.get("code"))
            if record_product_id not in context.products_by_id:
                continue
        if spec.name == "inventory_by_code":
            rows.append(_inventory_row(spec, record, context, raw_sha256, collected_at))
        elif spec.name == "process_profit_by_code":
            rows.append(_profit_row(spec, record, context, raw_sha256, collected_at))
        elif spec.name == "basis":
            rows.append(_basis_row(spec, record, context, raw_sha256, collected_at))
        elif spec.name == "warehouse_stock":
            rows.append(_warehouse_row(spec, record, context, raw_sha256, collected_at))
        elif spec.name == "member_rank":
            rows.extend(_member_rows(spec, record, context, raw_sha256, collected_at))
        elif spec.name == "money_flow":
            rows.append(_money_flow_row(spec, record, context, raw_sha256, collected_at))
        elif spec.name in {"futures_trade_params", "futures_exchange_fee"}:
            rows.append(_rule_row(spec, record, context, raw_sha256, collected_at))
        elif spec.name == "declaration_fee":
            rows.extend(_declaration_rows(spec, record, context, raw_sha256, collected_at))
        elif spec.name == "futures_contract_price":
            rows.append(_price_row(spec, record, context, raw_sha256, collected_at))
        else:
            rows.append(_archive_row(spec, record, context, raw_sha256, collected_at))
    return [
        row
        for row in rows
        if row["observation_date"] is None
        or row["observation_date"] >= CN_FUTURES_RETENTION_START
    ]

def _inventory_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    _require(row, {"inventoryValue", "dataDate", "reportDate", "dataType"}, spec.name)
    product = _context_product(context)
    observed = _parse_date(row["dataDate"])
    snapshot = _parse_date(row["reportDate"])
    entity = str(row["dataType"])
    return _finish_row(
        spec,
        row,
        observation_key=f"{product.product_id}|{entity}|{observed.isoformat()}",
        observation_date=observed,
        snapshot_date=snapshot,
        exchange_id=product.exchange_id,
        product_id=product.product_id,
        entity_id=entity,
        metric=spec.metric,
        value=_number(row.get("inventoryValue")),
        unit=None,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _profit_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    _require(row, {"DataDate", "DataType", "ProfitValue", "ReportDate"}, spec.name)
    product = _context_product(context)
    observed = _parse_date(row["DataDate"])
    snapshot = _parse_date(row["ReportDate"])
    entity = str(row["DataType"])
    return _finish_row(
        spec,
        row,
        observation_key=f"{product.product_id}|{entity}|{observed.isoformat()}",
        observation_date=observed,
        snapshot_date=snapshot,
        exchange_id=product.exchange_id,
        product_id=product.product_id,
        entity_id=entity,
        metric=spec.metric,
        value=_number(row.get("ProfitValue")),
        unit=None,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _basis_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    _require(row, {"reportDate", "code", "contractCode", "basisValue"}, spec.name)
    observed = _parse_date(row["reportDate"])
    product_id = _upper(row.get("code")) or _context_product(context).product_id
    contract = _upper(row.get("contractCode"))
    exchange_id = _context_exchange_for_product(context, product_id)
    provider_row, economic_contract_id = _with_contract_identity(
        row,
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=contract,
        trading_day=observed,
    )
    district = _text(row.get("district")) or ""
    spot_index = _text(row.get("spotIndexName")) or ""
    entity = "|".join(
        (economic_contract_id or contract or product_id, district, spot_index)
    )
    return _finish_row(
        spec,
        provider_row,
        observation_key=f"{product_id}|{entity}|{observed.isoformat()}",
        observation_date=observed,
        snapshot_date=None,
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=contract,
        entity_id=entity,
        metric=spec.metric,
        value=_number(row.get("basisValue")),
        unit=_text(row.get("unitName")),
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _warehouse_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    _require(row, {"tradingDay", "code", "exchangeCode", "onWarrant"}, spec.name)
    observed = _parse_date(row["tradingDay"])
    product_id = _upper(row.get("code")) or _context_product(context).product_id
    exchange_id = _canonical_exchange(_upper(row.get("exchangeCode")))
    if exchange_id is None:
        exchange_id = _context_exchange_for_product(context, product_id)
    return _finish_row(
        spec,
        row,
        observation_key=f"{exchange_id}|{product_id}|{observed.isoformat()}",
        observation_date=observed,
        snapshot_date=None,
        exchange_id=exchange_id,
        product_id=product_id,
        entity_id=f"{exchange_id}|{product_id}",
        metric=spec.metric,
        value=_number(row.get("onWarrant")),
        unit=None,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _member_rows(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    _require(row, {"tradingDay", "code", "exchangeCode", "contractCode"}, spec.name)
    observed = _parse_date(row["tradingDay"])
    product_id = _upper(row.get("code")) or _context_product(context).product_id
    exchange_id = _canonical_exchange(
        _upper(row.get("exchangeCode")) or context.exchange_id
    )
    contract = _upper(row.get("contractCode"))
    identity_row = dict(row)
    contract_product_id = _product_from_instrument(contract)
    trusted_contract_product = (
        contract_product_id is not None
        and (
            (
                context.product is not None
                and context.product.product_id == contract_product_id
            )
            or (
                context.products_by_id is not None
                and contract_product_id in context.products_by_id
            )
        )
    )
    if contract_product_id != product_id and trusted_contract_product:
        identity_row["_providerProductId"] = product_id
        identity_row["_productIdentityStatus"] = "repaired_from_contract_code"
        product_id = contract_product_id
    elif contract_product_id == product_id:
        identity_row["_productIdentityStatus"] = "matched"
    else:
        identity_row["_productIdentityStatus"] = "unresolved_mismatch"
    provider_row, economic_contract_id = _with_contract_identity(
        identity_row,
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=contract,
        trading_day=observed,
    )
    dimensions = (
        ("LONG", "longPositionMemberName", "longPositionRank", "longPosition", "longPositionChange"),
        ("SHORT", "shortMemberName", "shortPositionRank", "shortPosition", "shortPositionChange"),
        ("TURNOVER", "turnoverMemberName", "turnoverRand", "turnover", "turnoverChange"),
    )
    output: list[dict[str, object]] = []
    for rank_type, member_field, rank_field, value_field, change_field in dimensions:
        rank = _integer(row.get(rank_field))
        member = _text(row.get(member_field))
        augmented = dict(provider_row)
        augmented.update(
            {
                "_rankType": rank_type,
                "_memberName": member,
                "_rank": rank,
                "_value": _number(row.get(value_field)),
                "_change": _number(row.get(change_field)),
            }
        )
        rank_key = str(rank) if rank is not None else member or "unknown"
        contract_key = economic_contract_id or contract
        entity = f"{contract_key}|{rank_type}|{rank_key}"
        output.append(
            _finish_row(
                spec,
                augmented,
                observation_key=(
                    f"{exchange_id}|{product_id}|{contract_key}|{observed.isoformat()}|"
                    f"{rank_type}|{rank_key}"
                ),
                observation_date=observed,
                snapshot_date=None,
                exchange_id=exchange_id,
                product_id=product_id,
                instrument_id=contract,
                entity_id=entity,
                metric=f"member_rank_{rank_type.lower()}",
                value=_number(row.get(value_field)),
                unit=None,
                raw_sha256=raw_sha256,
                collected_at=collected_at,
            )
        )
    return output

def _money_flow_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    _require(row, {"tradingDay", "code", "inflow"}, spec.name)
    observed = _parse_date(row["tradingDay"])
    product_id = _upper(row.get("code")) or _context_product(context).product_id
    exchange_id = _canonical_exchange(
        _upper(row.get("exchange")) or context.exchange_id
    )
    return _finish_row(
        spec,
        row,
        observation_key=f"{exchange_id}|{product_id}|{observed.isoformat()}",
        observation_date=observed,
        snapshot_date=None,
        source_updated_at=_parse_source_timestamp(row.get("updateTime")),
        exchange_id=exchange_id,
        product_id=product_id,
        entity_id=f"{exchange_id}|{product_id}",
        metric=spec.metric,
        value=_number(row.get("inflow")),
        unit=None,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _rule_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    _require(row, {"tradingDay", "contractCode"}, spec.name)
    observed = _parse_date(row["tradingDay"])
    instrument_id = _upper(row.get("contractCode"))
    exchange_id = _canonical_exchange(
        _upper(row.get("exchangeCode")) or context.exchange_id
    )
    product_id = _product_from_instrument(instrument_id)
    purpose = _upper(row.get("tradePurp")) or ""
    provider_row, economic_contract_id = _with_contract_identity(
        row,
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=instrument_id,
        trading_day=observed,
    )
    contract_key = economic_contract_id or instrument_id
    return _finish_row(
        spec,
        provider_row,
        observation_key=(
            f"{exchange_id}|{contract_key}|{observed.isoformat()}|{purpose}"
        ),
        observation_date=observed,
        snapshot_date=None,
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=instrument_id,
        entity_id=contract_key,
        metric=spec.metric,
        value=None,
        unit=None,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _declaration_rows(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    _require(row, {"tradingDay", "code", "feeRules"}, spec.name)
    observed = _parse_date(row["tradingDay"])
    product_id = _upper(row.get("code")) or _context_product(context).product_id
    exchange_id = _canonical_exchange(
        _upper(row.get("exchangeCode")) or context.exchange_id
    )
    output: list[dict[str, object]] = []
    fee_rules = row.get("feeRules")
    if not isinstance(fee_rules, list):
        raise ValueError("declaration_fee feeRules must be a list")
    for rule in fee_rules:
        if not isinstance(rule, dict):
            continue
        amount_range = rule.get("messageAmountRange")
        amount_range = amount_range if isinstance(amount_range, dict) else {}
        minimum = _integer(amount_range.get("min"))
        maximum = _integer(amount_range.get("max"))
        augmented = {key: value for key, value in row.items() if key != "feeRules"}
        augmented["feeRule"] = rule
        output.append(
            _finish_row(
                spec,
                augmented,
                observation_key=(
                    f"{exchange_id}|{product_id}|{observed.isoformat()}|"
                    f"{minimum}|{maximum}"
                ),
                observation_date=observed,
                snapshot_date=None,
                exchange_id=exchange_id,
                product_id=product_id,
                entity_id=f"{exchange_id}|{product_id}|{minimum}|{maximum}",
                metric=spec.metric,
                value=None,
                unit=None,
                raw_sha256=raw_sha256,
                collected_at=collected_at,
            )
        )
    return output

def _price_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    _require(row, {"tradingDay", "code", "contractCode", "closePrice"}, spec.name)
    observed = _parse_date(row["tradingDay"])
    product_id = _upper(row.get("code")) or _context_product(context).product_id
    instrument_id = _upper(row.get("contractCode"))
    exchange_id = _context_exchange_for_product(context, product_id)
    provider_row, economic_contract_id = _with_contract_identity(
        row,
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=instrument_id,
        trading_day=observed,
    )
    contract_key = economic_contract_id or instrument_id
    return _finish_row(
        spec,
        provider_row,
        observation_key=(
            f"{exchange_id}|{contract_key}|{observed.isoformat()}"
        ),
        observation_date=observed,
        snapshot_date=None,
        source_updated_at=_parse_source_timestamp(row.get("updateTime")),
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=instrument_id,
        entity_id=contract_key,
        metric=spec.metric,
        value=_number(row.get("closePrice")),
        unit=None,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _archive_row(
    spec: EndpointSpec,
    row: dict[str, Any],
    context: RequestContext,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    observation_date = _first_date(
        row,
        ("reportDate", "tradingDay", "date", "publishTime", "updateTime"),
    )
    exchange_id = _canonical_exchange(
        _upper(row.get("exchangeCode")) or context.exchange_id
    )
    product_id = _upper(row.get("code")) or (
        context.product.product_id if context.product is not None else None
    )
    instrument_id = _upper(row.get("contractCode"))
    identity_values = [
        row.get(name)
        for name in (
            "id",
            "newsId",
            "reportId",
            "name",
            "code",
            "reportDate",
            "date",
            "publishTime",
            "type",
            "title",
        )
        if row.get(name) not in (None, "")
    ]
    if not identity_values:
        identity_values = [_canonical_hash(row)]
    identity = "|".join(str(value) for value in identity_values)
    return _finish_row(
        spec,
        row,
        observation_key=f"{spec.name}|{identity}",
        observation_date=observation_date,
        snapshot_date=None,
        source_updated_at=_parse_source_timestamp(
            row.get("updateTime") or row.get("publishTime")
        ),
        exchange_id=exchange_id,
        product_id=product_id,
        instrument_id=instrument_id,
        entity_id=identity,
        metric=spec.metric,
        value=_number(row.get("value") or row.get("score")),
        unit=None,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
    )

def _finish_row(
    spec: EndpointSpec,
    provider_row: dict[str, Any],
    *,
    observation_key: str,
    observation_date: date | None,
    snapshot_date: date | None,
    exchange_id: str | None,
    product_id: str | None,
    entity_id: str | None,
    metric: str,
    value: float | None,
    unit: str | None,
    raw_sha256: str,
    collected_at: datetime,
    instrument_id: str | None = None,
    source_updated_at: datetime | None = None,
) -> dict[str, object]:
    provider_row_json = json.dumps(
        provider_row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_identity = provider_row
    if spec.scope == "product_snapshot":
        content_identity = {
            key: value
            for key, value in provider_row.items()
            if key.lower() != "reportdate"
        }
    content_identity_json = json.dumps(
        content_identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    row_content_sha256 = hashlib.sha256(
        content_identity_json.encode("utf-8")
    ).hexdigest()
    snapshot_clock = snapshot_date.isoformat() if snapshot_date else ""
    version_id = hashlib.sha256(
        "\0".join(
            (
                CN_FUTURES_OBSERVATION_NORMALIZATION_VERSION,
                spec.dataset,
                observation_key,
                snapshot_clock,
                row_content_sha256,
            )
        ).encode("utf-8")
    ).hexdigest()
    return {
        "version_id": version_id,
        "provider": "gtja_futures",
        "dataset": spec.dataset,
        "endpoint": spec.name,
        "observation_key": observation_key,
        "observation_date": observation_date,
        "snapshot_date": snapshot_date,
        "source_updated_at": source_updated_at,
        "exchange_id": exchange_id,
        "product_id": product_id,
        "instrument_id": instrument_id,
        "entity_id": entity_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "clock_capability": spec.clock_capability,
        "clock_precision": spec.clock_precision,
        "revision_scope": spec.revision_scope,
        "provider_row_json": provider_row_json,
        "row_content_sha256": row_content_sha256,
        "raw_sha256": raw_sha256,
        "collected_at": collected_at,
        "normalization_version": CN_FUTURES_OBSERVATION_NORMALIZATION_VERSION,
    }

def _dataset_catalog_row(spec: EndpointSpec) -> dict[str, object]:
    return {
        "dataset": spec.dataset,
        "endpoint": spec.name,
        "endpoint_path": spec.path,
        "enabled": spec.enabled,
        "clock_capability": spec.clock_capability,
        "clock_precision": spec.clock_precision,
        "revision_scope": spec.revision_scope,
        "notes": spec.notes,
    }

def _context_product(context: RequestContext) -> CnFuturesProduct:
    if context.product is None:
        raise ValueError("product request context is required")
    return context.product

def _context_exchange_for_product(
    context: RequestContext,
    product_id: str | None,
) -> str:
    exchange_id = _canonical_exchange(context.exchange_id)
    if exchange_id is not None:
        return exchange_id
    if context.product is not None:
        return context.product.exchange_id
    if context.products_by_id is not None and product_id is not None:
        product = context.products_by_id.get(product_id)
        if product is not None:
            return product.exchange_id
    raise ValueError(f"missing exchange context for product {product_id!r}")

def _canonical_exchange(value: object) -> str | None:
    exchange_id = _upper(value)
    return _EXCHANGE_ALIASES.get(exchange_id, exchange_id)

def _with_contract_identity(
    provider_row: Mapping[str, Any],
    *,
    exchange_id: str | None,
    product_id: str | None,
    instrument_id: str | None,
    trading_day: date,
) -> tuple[dict[str, Any], str | None]:
    augmented = dict(provider_row)
    if not exchange_id or not product_id or not instrument_id:
        augmented["_contractIdentityStatus"] = "not_applicable"
        return augmented, None
    try:
        identity = parse_contract_identity(
            exchange_id,
            product_id,
            instrument_id,
            trading_day,
        )
    except ValueError:
        augmented["_contractIdentityStatus"] = "non_standard_contract"
        return augmented, None
    augmented.update(
        {
            "_contractIdentityStatus": "resolved",
            "_deliveryYear": identity.delivery_year,
            "_deliveryMonth": identity.delivery_month,
            "_economicContractId": identity.economic_contract_id,
        }
    )
    return augmented, identity.economic_contract_id

def _require(row: dict[str, Any], fields: set[str], endpoint: str) -> None:
    missing = sorted(fields.difference(row))
    if missing:
        raise ValueError(f"{endpoint} response is missing fields: {missing}")

def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None

def _upper(value: object) -> str | None:
    text = _text(value)
    return text.upper() if text else None

def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None

def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None

def _parse_date(value: object) -> date:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], pattern).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date value: {text!r}")

def _first_date(row: dict[str, Any], names: tuple[str, ...]) -> date | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            try:
                return _parse_date(value)
            except ValueError:
                continue
    return None

def _parse_source_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S"):
        try:
            local = datetime.strptime(text[:19], pattern).replace(tzinfo=SHANGHAI)
            return local.astimezone(timezone.utc)
        except ValueError:
            continue
    return None

def _product_from_instrument(instrument_id: str | None) -> str | None:
    if not instrument_id:
        return None
    match = re.match(r"^([A-Z]+)", instrument_id)
    return match.group(1) if match else None

def _canonical_hash(value: object) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()
