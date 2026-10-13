"""Retain scoped Wind supplier originals for the bound research run."""
from datetime import UTC, datetime
from uuid import uuid4

from studio_market import wind as client
from studio_market.wind import WindDomain, WindError
from watchlist_app.core.settings import get_settings


def _key() -> str:
    secret = get_settings().wind_api_key
    if secret is None or not secret.get_secret_value().strip():
        raise WindError("研究服务尚未配置 Wind 授权。", 503)
    return secret.get_secret_value().strip()


def list_tools(domain: WindDomain):
    return client.list_tools(domain, key=_key())


def query(domain: WindDomain, tool: str, arguments: dict):
    return client.query(domain, tool, arguments, key=_key())


def source(instrument_id, domain, tool, arguments, payload):
    now = datetime.now(UTC).isoformat()
    return {"source_id": "computed:" + uuid4().hex, "source_type": "computed_metric",
        "instrument_id": instrument_id, "scope": "instrument", "title": f"Wind 供应商原件 · {tool}",
        "as_of": now, "retrieved_at": now, "analysis_kind": "provider_snapshot", "provider": "wind",
        "methodology": {"operation": "provider_read", "no_calculation": True, "provider": "wind",
            "domain": domain, "tool": tool, "arguments": arguments, "endpoint": client.endpoint(domain),
            "clock": "as_of/retrieved_at 是本次获取时间；报告期、原始披露时间、单位与量级以原响应为准。未证明在此前冻结时点已经可得。"},
        "data": {"analysis_kind": "provider_snapshot", "provider": "wind", "domain": domain,
            "tool": tool, "payload": payload,
            "limitations": ["供应商原始返回，不是内部计算或独立核证；不得把文档检索片段当作全文。",
                "保留原单位、量级、日期与口径；供应商未提供的字段保持未知。",
                "INVALID及空值表示不可用，不得当作0；excelTotalCount等声明总数不证明完整性，核对实际返回条数和分页。"]}}


def reusable_source(session, run, instrument_id, domain, tool, arguments):
    from watchlist_app.services.research_run_evidence import retained_computed_sources, read_computed_source
    for item in reversed(retained_computed_sources(session, run.entry_id, metadata_only=True)):
        if item.get("instrument_id") != instrument_id or (item.get("data") or {}).get("analysis_kind") != "provider_snapshot":
            continue
        original = read_computed_source(session, run.entry_id, item["source_id"])
        method = original.get("methodology") or {}
        if (method.get("provider"), method.get("domain"), method.get("tool"), method.get("arguments")) == ("wind", domain, tool, arguments):
            return original
    return None


def receipt(value, *, reused=False):
    return {**{key: value[key] for key in ("source_id", "title", "instrument_id", "analysis_kind", "provider", "retrieved_at", "as_of")},
        "reused": reused,
        "read": {"tool": "read_research_numbers", "source_id": value["source_id"], "section": "data"},
        "evidence_read": {"tool": "read_research_numbers", "source_id": value["source_id"], "section": "evidence"}}
