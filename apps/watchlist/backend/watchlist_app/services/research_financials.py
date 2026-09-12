"""Read complete retained company statements without using the page's FY summary."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
from uuid import uuid4

from studio_market.numeric.store import cutoff_instant
from watchlist_app.services.sector_market_data import all_rows, numeric_store


def financial_page(asset, *, as_of, offset=0, limit=20, statement_type=None, fiscal_period=None,
                   period_end=None, view="statements", store=None):
    """Page original financial line items, with their exact statement version headers.

    The provider's financial period is retained, not converted into a trailing,
    quarter-only or cumulative accounting period. No ratio/amount units are inferred.
    """
    reference = asset.get("reference_data") or {}
    symbol = reference.get("provider_symbol")
    if asset.get("instrument_type") != "equity" or reference.get("provider") != "fmp" or not symbol:
        raise ValueError("本轮未绑定可读取完整财报的公司与FMP标识；不能用基金或相似公司替代。")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("财报分页offset必须是非负整数。")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("财报分页limit必须为1至100。")
    if statement_type not in {None, "income", "balance_sheet", "cash_flow"}:
        raise ValueError("请选择利润表、资产负债表或现金流量表。")
    if fiscal_period not in {None, "FY", "Q1", "Q2", "Q3", "Q4"}:
        raise ValueError("请选择来源披露的FY或Q1至Q4财期。")
    if view not in {"statements", "facts"}:
        raise ValueError("请选择财报目录或原始科目。")
    if period_end is not None:
        from datetime import date
        period_end = date.fromisoformat(str(period_end)).isoformat()
    cutoff = cutoff_instant(as_of)
    store = store or numeric_store()
    date_filters = {"start": period_end, "end": period_end} if period_end else {}
    statements = [row for row in all_rows(store, "financial_statements", symbols=[symbol], as_of=cutoff, **date_filters)
                  if (statement_type is None or row["statement_type"] == statement_type)
                  and (fiscal_period is None or row["fiscal_period"] == fiscal_period)]
    by_version = {row["statement_content_sha256"]: row for row in statements}
    # Restated statements and their old line items must never be combined. The
    # numeric store independently enforces observation and release cutoffs.
    facts = [row for row in all_rows(store, "financial_facts", symbols=[symbol], as_of=cutoff, **date_filters)
             if row.get("statement_content_sha256") in by_version]
    facts.sort(key=lambda row: (row["period_end"], row["statement_type"], row["fiscal_period"], row["line_item"]), reverse=True)
    facts_by_statement = Counter(row["statement_content_sha256"] for row in facts)
    rows = statements if view == "statements" else facts
    if offset > len(rows):
        raise ValueError("财报分页超出本轮已取得的原始行范围。")
    known_versions = {row["statement_content_sha256"] for row in facts}
    count = min(limit, len(rows) - offset)
    request = {"offset": offset, "limit": limit, "statement_type": statement_type, "fiscal_period": fiscal_period,
               "period_end": period_end, "view": view}
    source_id = f"financials:{uuid4().hex}"

    def original(row):
        return {key: value for key, value in row.items() if not key.startswith("_")}

    while True:
        end = offset + count
        page = rows[offset:end]
        versions = {row["statement_content_sha256"] for row in page}
        result = {"source_id": source_id, "source_type": "company_snapshot", "instrument_id": asset["instrument_id"],
            "title": f"FMP · {symbol} · {'财报目录' if view == 'statements' else '已取得的财报原始行'}", "run_cutoff": cutoff.isoformat(),
            "retrieved_at": datetime.now(UTC).isoformat(), "request": request,
            "company": {"symbol": symbol, "page_kind": view,
                "financials": [original(row) for row in page] if view == "facts" else [],
                "statements": [original(row) | {"matching_fact_count": facts_by_statement[row["statement_content_sha256"]]}
                               for row in statements if row["statement_content_sha256"] in versions],
                "available": bool(rows), "offset": offset, "next_offset": end if end < len(rows) else None,
                "total_rows": len(rows), "statement_count": len(statements),
                "statement_types": dict(Counter(row["statement_type"] for row in statements)),
                "statements_without_matching_facts": sum(row["statement_content_sha256"] not in known_versions for row in statements),
                "pagination_note": "statements目录按原财报期与类型选择，再以financial_view=facts和period_end/statement_type/fiscal_period读取该表科目；每页跟随next_offset，科目按statement_content_sha256关联同页statements。目录不代表已阅读科目。",
                "semantics": ["本页为FMP留存的财报科目与来源元数据；读取这些数据不等于已经阅读发行人公告原文。",
                    "period_end/fiscal_period是财报期；accepted_at/filing_date是来源披露信息，observed_at/available_at限定本轮实际可知版本。",
                    "reported_currency是报表币种，不代表每个科目均为金额；EPS、股数、比率及缺少单位的字段按源科目核实，不补造单位。",
                    "保留来源原财期，不自行假定Q2现金流是单季或累计，不把最近FY利润表当完整财务覆盖。",
                    "available仅表示本截止时间取得当前页类的目录或科目；未取得不等于公司没有披露。"]}}
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 48000:
            return result
        if count > 1:
            count = max(1, count // 2)
        else:
            raise ValueError("单条财报及其版本信息超过工具返回上限，未截断或把未读部分当作缺失。")
