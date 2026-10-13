"""Shared read-only Wind MCP transport; callers own credentials and persistence."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

import httpx2
from jsonschema import Draft202012Validator
from referencing import Registry
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client


WindDomain = Literal["financial_docs", "fund_data", "stock_data", "index_data", "economic_data"]
READ_TOOLS = {
    "financial_docs": {"get_company_announcements", "get_financial_news"},
    "fund_data": {"get_fund_price_indicators", "get_fund_kline", "get_fund_financials", "get_fund_holdings",
        "get_fund_company_info", "get_fund_quote", "get_fund_info", "get_fund_holders", "get_fund_performance", "search_funds"},
    "stock_data": {"get_stock_price_indicators", "get_risk_metrics", "get_stock_events", "get_stock_kline",
        "get_stock_basicinfo", "get_stock_equity_holders", "get_stock_fundamentals", "get_stock_quote", "get_stock_technicals", "search_stocks"},
    "index_data": {"get_index_technicals", "get_index_quote", "get_index_kline", "get_index_fundamentals",
        "get_index_price_indicators", "get_index_basicinfo"},
    "economic_data": {"search_economic_indicator", "query_economic_indicator_data"},
}


class WindError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def endpoint(domain: str) -> str:
    if domain not in READ_TOOLS:
        raise WindError("不支持该 Wind 查询领域；analytics 和写入工具未开放。", 422)
    return f"https://mcp.wind.com.cn/vserver_{domain}/mcp/"


def _without_secret(value, key):
    # A provider may echo request headers in an error/result. Never retain them.
    if key in json.dumps(value, ensure_ascii=False):
        raise WindError("Wind 响应包含敏感认证信息，未保存或转发。")
    return value


async def _tools(session, domain, key):
    result, cursor, seen = [], None, set()
    while True:
        page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
        for tool in page.tools:
            if tool.name in READ_TOOLS[domain]:
                schema = tool.input_schema
                try:
                    Draft202012Validator.check_schema(schema)
                except Exception:
                    raise WindError("Wind 工具参数合同无效，未执行数据查询。") from None
                result.append(_without_secret({"name": tool.name, "description": tool.description,
                    "inputSchema": schema}, key))
        cursor = page.next_cursor
        if not cursor:
            return result
        if cursor in seen:
            raise WindError("Wind 工具目录分页没有推进，未执行数据查询。")
        seen.add(cursor)


def _check_result(result, key):
    payload = _without_secret(result.model_dump(mode="json", by_alias=True, exclude_unset=True), key)
    if payload.get("isError"):
        raise WindError("Wind 返回查询错误；请核对授权、额度与参数，未取得可用数据。")
    for block in payload.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            inner = json.loads(block["text"])
        except (ValueError, KeyError):
            continue
        if not isinstance(inner, dict):
            continue
        data = inner.get("data")
        code = data.get("code") if isinstance(data, dict) else None
        numeric_code = None
        if type(code) is int or isinstance(code, str):
            try:
                numeric_code = int(code)
            except ValueError:
                pass
        error = inner.get("error")
        business_code = inner.get("mcp_tool_error_code")
        if (business_code not in (None, 0)
                or error
                or (numeric_code is not None and numeric_code != 0 and not 200 <= numeric_code < 300)):
            code = business_code if business_code not in (None, 0) else (
                error.get("code") if isinstance(error, dict) and error.get("code") is not None else numeric_code)
            safe_code = str(code) if type(code) in (int, float) or (isinstance(code, str)
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", code)) else "未提供"
            raise WindError(f"Wind 返回业务错误（code={safe_code}）；请按接口合同核对参数、授权或额度，本次未取得数据，请勿直接重试。")
    return payload


async def _exchange(domain, tool=None, arguments=None, *, key):
    url = endpoint(domain)
    if not isinstance(key, str) or not key.strip():
        raise WindError("数据服务尚未配置 Wind 授权。", 503)
    key = key.strip()
    if tool is not None and tool not in READ_TOOLS[domain]:
        raise WindError("该工具不在 Wind 只读查询范围。", 422)
    failure_status = None
    async def observe_status(response):
        # The SDK translates transport exceptions into protocol errors. Retain
        # only the HTTP status for safe diagnostics, never its body or headers.
        nonlocal failure_status
        if response.request.method == "POST" and response.status_code >= 400:
            failure_status = response.status_code
    try:
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"},
                timeout=120, follow_redirects=False, trust_env=False,
                event_hooks={"response": [observe_status]}) as http:
            async with streamable_http_client(url, http_client=http) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=120) as session:
                    await session.initialize()
                    tools = await _tools(session, domain, key)
                    if tool is None:
                        return {"provider": "wind", "domain": domain, "tools": tools,
                            "note": "按具体资料缺口查询；接口返回可能是片段或覆盖有限的数值。INVALID及空值表示不可用，不得当作0；excelTotalCount等声明总数不证明完整性，核对实际返回条数和分页。先复用本轮留存原件；本目录不证明某标的有数据，也不报告账户剩余额度。"}
                    selected = next((row for row in tools if row["name"] == tool), None)
                    if selected is None:
                        raise WindError("Wind 当前目录没有该查询工具。", 422)
                    _without_secret(arguments, key)
                    # Resolve local definitions only. A schema must never turn
                    # validation into an unapproved HTTP request via remote $ref.
                    errors = list(Draft202012Validator(selected["inputSchema"], registry=Registry()).iter_errors(arguments))
                    if errors:
                        path = ".".join(map(str, errors[0].absolute_path)) or "arguments"
                        raise WindError(f"Wind 参数不符合当前接口合同（{path}；{errors[0].validator}），请按工具目录修正。", 422)
                    return _check_result(await session.call_tool(tool, arguments, read_timeout_seconds=120), key)
    except WindError:
        raise
    except Exception as error:
        # AnyIO transport contexts wrap errors from their body in exception
        # groups. Preserve only our deliberately safe errors, never upstream text.
        pending, causes = [error], []
        while pending:
            item = pending.pop()
            if isinstance(item, WindError):
                raise item from None
            if isinstance(item, BaseExceptionGroup):
                pending.extend(item.exceptions)
            else:
                causes.append(item)
        if failure_status is not None:
            if failure_status in {401, 403}:
                raise WindError(f"Wind 授权未通过（HTTP {failure_status}）；请核对供应商授权，本次未取得数据，请勿直接重试。") from None
            if failure_status == 429:
                raise WindError("Wind 查询受到限流或额度限制（HTTP 429）；本次未取得数据，请先核对额度。", 429) from None
            if failure_status >= 500:
                raise WindError(f"Wind 供应商服务暂不可用（HTTP {failure_status}）；本次未取得数据。") from None
            raise WindError(f"Wind 拒绝查询（HTTP {failure_status}）；请核对当前接口合同，本次未取得数据，请勿直接重试。") from None
        if any(isinstance(item, httpx2.TimeoutException) for item in causes):
            raise WindError("Wind 请求超时；本次未取得数据，未自动重试。", 504) from None
        # Do not forward upstream bodies, request headers, exception repr or URLs.
        raise WindError("Wind 服务请求未完成，请核对网络、授权与额度；本次没有取得可用数据。") from None


def list_tools(domain: WindDomain, *, key: str):
    return asyncio.run(_exchange(domain, key=key))


def query(domain: WindDomain, tool: str, arguments: dict, *, key: str):
    return asyncio.run(_exchange(domain, tool, arguments, key=key))
