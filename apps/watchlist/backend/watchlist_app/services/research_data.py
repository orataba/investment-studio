"""Semantic, read-only access to retained public data, using the run's information clock."""
from datetime import date
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from studio_market.numeric.datasets import DATASETS
from studio_market.numeric.store import cutoff_instant

from watchlist_app.services.sector_market_data import numeric_store


# These are supported read contracts, never promises that collection succeeded.
# Raw provider fields remain intact; retrieval does not invent standardized units.
DATA_METHODS = {
    "financial_statements": ("公司财报目录", "公司、三表、原财期和披露时间；目录不能代替科目或发行人原文。"),
    "financial_facts": ("公司标准财报科目", "按statement_content_sha256关联同版本财报；保留原财期、币种、科目单位，不假定季度现金流为单季。"),
    "as_reported_statements": ("原报表目录", "发行人报表概念目录；不同公司的XBRL概念不自动可比。"),
    "as_reported_facts": ("原报表概念科目", "保留concept、单位和上下文；不可只按相似名称相加或替换标准科目。"),
    "sec_filings": ("SEC披露目录", "这是披露元数据与链接，不表示已取得文件正文。"),
    "cn_financials": ("A股财务资料", "保留ann_date、财期、报表类型及来源单位；累计与单季必须另行核实。"),
    "cn_equity_daily_basic": ("A股估值与交易指标", "保留来源交易日和指标单位；PE、PB、股本及市值不得混用单位。"),
    "company_profiles": ("公司业务资料", "资料是本次捕获的公司背景，不等于历史当时的业务结构。"),
    "analyst_estimates": ("盈利预期", "仅比较同公司、同目标财期、频率、指标和币种；财期滚动不等于上修下修。"),
    "analyst_price_targets": ("分析师目标价", "卖方观点而非公允价值或预测事实，核对报价币种和采集日期。"),
    "analyst_rating_consensus": ("分析师评级共识", "机构观点快照，不是独立事实或本系统投资建议。"),
    "analyst_grade_events": ("分析师评级事件", "区分原发布日期与收录日期；机构评级变化不证明基本面已经变化。"),
    "earnings_surprises": ("盈利公布与预期差", "核对公告期、实际值与当时共识口径，不能由价格反应反推预期差。"),
    "etf_info": ("ETF结构与条款", "核实底层资产、费用、币种与条款日期；载体类型不能代替敞口。"),
    "etf_holdings": ("ETF当前留存成份", "采集日与持仓报告期分开；保持完整权重与未知部分，不将覆盖子集重新加权成全基金。"),
    "etf_disclosures": ("ETF历次披露", "按报告期与实际可知时间读取，披露持仓不等于每日实时持仓。"),
    "index_constituent_events": ("指数成份变更", "区分公布日、生效日及采集日；指数成份不等于基金实际持仓。"),
    "index_membership_snapshots": ("指数成份快照", "仅证明留存观察时点的成份，不回填为历史成份。"),
    "macro_series": ("宏观数据", "观察期、公布及修订时间分开；按来源单位与频率比较。"),
    "market_series_daily": ("跨资产市场序列", "利率、利差、汇率及商品按各自单位比较；百分比点变化不等于价格收益。"),
    "market_series_catalog": ("跨资产序列说明", "目录描述数据合同，不证明观察值已采集或足够新鲜。"),
    "cn_futures_products": ("中国期货品种", "品种与合约不同；单位、交割品和市场必须匹配。"),
    "cn_futures_dataset_catalog": ("期货事实目录", "数据集目录不是实际库存、仓单、现货或期限结构的观察。"),
    "cn_futures_observations": ("中国期货与商品事实", "按dataset、品种、观察日和单位解读库存、仓单、现货及合约事实；不把合约切换当成连续收益。"),
    "provider_reference": ("登记资产补充资料", "按section读取原提供方资料；来源字段与单位保持不变，空表不表示经济事实为零。"),
}


class StoredDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["catalogue", "records"] = "catalogue"
    dataset: str | None = None
    symbols: list[str] = Field(default_factory=list, max_length=12)
    start: date | None = None
    end: date | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def valid_selection(self):
        if self.dataset is not None and self.dataset not in DATA_METHODS:
            raise ValueError("请选择共享资料目录中已支持的数据集")
        if self.action == "records" and self.dataset is None:
            raise ValueError("读取资料须指定数据集")
        if self.start and self.end and self.start > self.end:
            raise ValueError("资料起始日期不能晚于结束日期")
        if any(not symbol.strip() for symbol in self.symbols) or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("资料标识不能为空或重复")
        return self


def stored_market_data(request: StoredDataRequest, *, as_of, store=None):
    cutoff = cutoff_instant(as_of)
    if request.dataset is None:
        return {"as_of": cutoff.isoformat(), "datasets": [
            {"dataset": key, "title": title, "semantics": semantics,
             "symbol_field": DATASETS[key].symbol, "date_field": DATASETS[key].date,
             "historical_use": DATASETS[key].historical_use, "coverage": "not_checked"}
            for key, (title, semantics) in DATA_METHODS.items()],
            "note": "这是可读取的数据合同；指定dataset查询本截止时间的实际记录。目录存在不等于已有数据。"}
    store = store or numeric_store()
    count = request.limit
    while True:
        page = store.query(request.dataset, symbols=request.symbols or None,
            start=request.start.isoformat() if request.start else None,
            end=request.end.isoformat() if request.end else None,
            as_of=cutoff, limit=count, offset=request.offset)
        rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in page["rows"]]
        title, semantics = DATA_METHODS[request.dataset]
        data = {"analysis_kind": "stored_data", "dataset": request.dataset,
            "status": "available" if rows else "unavailable", "rows": rows,
            "total": page["total"], "offset": request.offset,
            "next_offset": request.offset + len(rows) if request.offset + len(rows) < page["total"] else None,
            "semantics": semantics, "provenance": page.get("provenance"),
            "limitations": ["仅包含本轮信息截止前已采集且已可知的记录；缺失不表示未披露或事实为零。",
                "结构化数据可以支持其口径内的分析；取得标准科目不等于已读财报原文，原文缺口只限制依赖原文的判断。"]}
        result = {"source_id": f"computed:{uuid4().hex}", "source_type": "computed_metric",
            "scope": "public_market", "title": title, "as_of": cutoff.isoformat(),
            "methodology": {"analysis_kind": "stored_data", "operation": "read_retained_records",
                "description": "读取留存原始字段，不计算、填充或转换单位。", "request": request.model_dump(mode="json"),
                "source_ids": list(dict.fromkeys(row["source_id"] for row in rows if row.get("source_id")))},
            "data": data}
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 48000:
            return result
        if count <= 1:
            raise ValueError("单条留存资料超过工具返回上限；未截断或声称已读，请使用适用的财报科目或原文分页工具。")
        count = max(1, count // 2)
