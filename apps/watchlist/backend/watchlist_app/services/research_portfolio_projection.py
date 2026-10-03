"""Concise account facts and lossless pages of retained Portfolio evidence.

Balances come from Portfolio's canonical account ledger. This module only
selects fields and totals already-derived cash balances in the same currency.
"""
from decimal import Decimal

from watchlist_app.services.research_read_projection import checked_overview, read_page, shape


def account_summary(workspace, *, as_of_date):
    rows = []
    for item in workspace["accounts"]:
        account = item["account"]
        row = {key: account.get(key) for key in (
            "account_id", "account_name", "account_type", "account_category", "cash_purpose", "currency", "status")}
        row.update(as_of_date=as_of_date, settled_cash=item.get("derived_cash_balance"),
                   pending_settlement=item.get("pending_settlement"))
        row["missing_fields"] = [key for key in ("account_name", "currency", "settled_cash", "pending_settlement")
                                 if row[key] is None]
        row["balance_status"] = "unavailable" if row["settled_cash"] is None else "available"
        rows.append(row)
    total = workspace.get("summary", {}).get("account_count", len(rows))
    return {"status": "complete" if total == len(rows) and not any(row["missing_fields"] for row in rows) else "partial",
            "total_rows": total, "returned_rows": len(rows), "as_of_date": as_of_date,
            "base_currency": workspace.get("base_currency"), "rows": rows,
            "currency_balances": cash_by_currency(rows),
            "basis": "Canonical account ledger at as_of_date; signed native-currency settled cash and pending settlement. "
                     "These are not account NAV, security valuations or broker buying power."}


def cash_by_currency(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["currency"], []).append(row)
    result = []
    for currency, accounts in groups.items():
        item = {"currency": currency, "account_count": len(accounts),
                "as_of_date": accounts[0]["as_of_date"], "missing_fields": []}
        for field in ("settled_cash", "pending_settlement"):
            values = [row[field] for row in accounts]
            available = currency is not None and all(value is not None for value in values)
            amounts = [Decimal(str(value)) for value in values] if available else []
            item["net_" + field] = str(sum(amounts, Decimal(0))) if available else None
            item["gross_" + field] = str(sum((abs(value) for value in amounts), Decimal(0))) if available else None
            if not available:
                item["missing_fields"].append(field)
        item["net_monetary_balance"] = (str(Decimal(item["net_settled_cash"]) + Decimal(item["net_pending_settlement"]))
                                         if not item["missing_fields"] else None)
        item["gross_monetary_balance"] = (str(Decimal(item["gross_settled_cash"]) + Decimal(item["gross_pending_settlement"]))
                                           if not item["missing_fields"] else None)
        item["netting_status"] = ("unavailable" if item["missing_fields"] else
            "zero_balances" if Decimal(item["gross_monetary_balance"]) == 0 else
            "fully_offset" if Decimal(item["net_monetary_balance"]) == 0 else "net_exposure")
        result.append(item)
    return result


def portfolio_sections(result):
    accounts = result.get("account_summary") or {}
    return {"accounts": accounts.get("rows"), "currency_balances": accounts.get("currency_balances"),
            "workspace": {key: value for key, value in result.items() if key != "account_summary"},
            "risk": {"forward_risk": result.get("forward_risk"), "risk_basis": result.get("risk_basis"),
                     "risk_coverage_summary": result.get("risk_coverage_summary"),
                     "risk_context": result.get("risk_context"),
                     "read_error": (result.get("detail_errors") or {}).get("risk_context"),
                     "scope_note": "仅包含本轮实际留存的风险事实。risk_context只在原风险页面读取；"
                                   "其为null不代表完整风险模块已读取或风险为零。请按原始状态、coverage和errors解释。"},
            "selected_holding": result.get("selected_holding"), "selected_account": result.get("selected_account"),
            "ledger_positions": result.get("ledger_positions")}


def read_portfolio_evidence(evidence, *, section="overview", offset=0, limit=20, path=None):
    result = evidence["result"]
    metadata = {key: evidence[key] for key in ("source_id", "retrieved_at")}
    metadata.update(portfolio_id=result.get("portfolio_id"), as_of_date=result.get("as_of_date"),
                    base_currency=result.get("base_currency"))
    sections = portfolio_sections(result)
    if section != "overview":
        if section not in sections:
            raise ValueError("请选择当前组合快照返回的分区。")
        return read_page(sections[section], {**metadata, "section": section}, offset=offset, limit=limit, path=path)
    if offset or path:
        raise ValueError("组合概览不使用offset/path；请按分区及返回的读取路径续读。")
    read = {"tool": "read_portfolio_holdings", "source_id": evidence["source_id"]}
    accounts = dict(result["account_summary"]) if result.get("account_summary") is not None else None
    overview = {**metadata, "section": "overview", "portfolio_name": result.get("portfolio_name"),
        "account_summary": accounts, "totals": result.get("totals"),
        "valuation_status": result.get("valuation_status", "available" if result.get("totals", {}).get("nav") is not None else "unavailable"),
        "valuation_error": result.get("valuation_error"), "forward_risk": result.get("forward_risk"),
        "page_scope": result.get("page_scope"), "detail_errors": result.get("detail_errors", {}),
        "ledger_errors": result.get("ledger_errors", {}),
        **({key: result[key] for key in ("available", "reason") if key in result}),
        "sections": {key: {**shape(value), "read": {**read, "section": key}} for key, value in sections.items()},
        "read_note": "先逐项读取account_summary中的全部账户事实；余额为0是已知零，null及missing_fields才是缺失。"
                     "currency_balances只汇总同币种账户现金，不代表证券、衍生品或全组合所有风险。"
                     "净额完全抵销与风险历史缺失不同；Forward RC仍按forward_risk的状态、errors和coverage解释，"
                     "零组合方差下RC无定义不等于全部风险为零。不得用NAV合计反推现成账户余额。"
                     "完整持仓、历史与风险按section和同一source_id读取，跟随next_offset及全部deferred.path；"
                     "未读资料不代表缺失。续读不重取市场数据、不重算、不新建对话或分析。"}
    pageable = [(["forward_risk"], {**read, "section": "risk"}),
                (["page_scope"], {**read, "section": "workspace", "path": ["page_scope"]})]
    if accounts is not None:
        pageable.extend([(["account_summary", "currency_balances"], {**read, "section": "currency_balances"}),
                         (["account_summary", "rows"], {**read, "section": "accounts"})])
    return checked_overview(overview, pageable_fields=pageable)
