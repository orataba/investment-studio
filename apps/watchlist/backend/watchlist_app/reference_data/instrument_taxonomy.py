from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any


INSTRUMENT_TAXONOMY_CODE = "instrument_taxonomy"
INSTRUMENT_TAXONOMY_LABEL = "Instrument Taxonomy"
INSTRUMENT_TAXONOMY_MAX_LEVELS = 7
INSTRUMENT_TAXONOMY_DERIVED_KEYS = {
    "instrument_taxonomy_leaf",
    "instrument_taxonomy_path",
    *{
        f"instrument_taxonomy_level_{level}"
        for level in range(1, INSTRUMENT_TAXONOMY_MAX_LEVELS + 1)
    },
}


_PUBLIC_FUND_TAXONOMY = [
    {
        "node_id": "fund-public-equity",
        "label": "权益基金",
        "children": [
            {"node_id": "fund-public-equity-active", "label": "主动权益"},
            {"node_id": "fund-public-equity-index", "label": "指数权益"},
            {"node_id": "fund-public-equity-sector", "label": "行业权益"},
            {"node_id": "fund-public-equity-theme", "label": "主题权益"},
            {"node_id": "fund-public-equity-long-short", "label": "权益多空"},
        ],
    },
    {
        "node_id": "fund-public-fixed-income",
        "label": "固定收益基金",
        "children": [
            {"node_id": "fund-public-fixed-income-government", "label": "政府债"},
            {"node_id": "fund-public-fixed-income-investment-grade", "label": "投资级信用"},
            {"node_id": "fund-public-fixed-income-high-yield", "label": "高收益债"},
            {"node_id": "fund-public-fixed-income-aggregate", "label": "综合债券"},
            {"node_id": "fund-public-fixed-income-short-duration", "label": "短久期"},
            {"node_id": "fund-public-fixed-income-convertible", "label": "可转债"},
            {"node_id": "fund-public-fixed-income-index", "label": "债券指数"},
        ],
    },
    {
        "node_id": "fund-public-allocation",
        "label": "配置基金",
        "children": [
            {"node_id": "fund-public-allocation-equity-biased", "label": "偏股配置"},
            {"node_id": "fund-public-allocation-balanced", "label": "均衡配置"},
            {"node_id": "fund-public-allocation-bond-biased", "label": "偏债配置"},
            {"node_id": "fund-public-allocation-flexible", "label": "灵活配置"},
            {"node_id": "fund-public-allocation-target", "label": "目标日期 / 目标风险"},
        ],
    },
    {
        "node_id": "fund-public-alternative",
        "label": "另类策略基金",
        "children": [
            {"node_id": "fund-public-alternative-market-neutral", "label": "市场中性"},
            {"node_id": "fund-public-alternative-managed-futures", "label": "管理期货"},
            {"node_id": "fund-public-alternative-macro", "label": "宏观策略"},
            {"node_id": "fund-public-alternative-event-driven", "label": "事件驱动"},
            {"node_id": "fund-public-alternative-relative-value", "label": "相对价值"},
            {"node_id": "fund-public-alternative-multi-strategy", "label": "另类多策略"},
        ],
    },
    {
        "node_id": "fund-public-commodity",
        "label": "商品型",
        "children": [
            {"node_id": "fund-public-commodity-precious-metals", "label": "贵金属基金"},
            {"node_id": "fund-public-commodity-broad", "label": "综合 / 其他商品"},
        ],
    },
    {
        "node_id": "fund-public-real-assets",
        "label": "实物资产基金",
        "children": [
            {"node_id": "fund-public-real-assets-real-estate", "label": "房地产 / REITs"},
            {"node_id": "fund-public-real-assets-infrastructure", "label": "基础设施"},
        ],
    },
    {"node_id": "fund-public-money-market", "label": "货币市场"},
    {
        "node_id": "fund-public-fof",
        "label": "基金中基金",
        "children": [
            {"node_id": "fund-public-fof-equity", "label": "权益 FOF"},
            {"node_id": "fund-public-fof-fixed-income", "label": "固定收益 FOF"},
            {"node_id": "fund-public-fof-allocation", "label": "配置 FOF"},
            {"node_id": "fund-public-fof-alternative", "label": "另类 FOF"},
        ],
    },
    {"node_id": "fund-public-other", "label": "其他"},
]


_PRIVATE_FUND_TAXONOMY = [
    {
        "node_id": "fund-private-equity",
        "label": "股票策略",
        "children": [
            {
                "node_id": "fund-private-equity-discretionary-long",
                "label": "主观多头",
            },
            {
                "node_id": "fund-private-equity-quant-long",
                "label": "量化多头",
                "children": [
                    {
                        "node_id": "fund-private-equity-quant-index-enhanced",
                        "label": "指数增强",
                    },
                    {
                        "node_id": "fund-private-equity-quant-stock-selection",
                        "label": "量化选股",
                    },
                ],
            },
            {"node_id": "fund-private-equity-long-short", "label": "股票多空"},
            {"node_id": "fund-private-equity-market-neutral", "label": "股票市场中性"},
        ],
    },
    {
        "node_id": "fund-private-credit",
        "label": "信用策略",
        "children": [
            {"node_id": "fund-private-credit-long-only", "label": "信用多头"},
            {"node_id": "fund-private-credit-long-short", "label": "信用多空"},
            {"node_id": "fund-private-credit-distressed", "label": "困境债 / 特殊机会"},
            {"node_id": "fund-private-credit-structured", "label": "结构化信用"},
        ],
    },
    {
        "node_id": "fund-private-macro",
        "label": "宏观策略",
        "children": [
            {"node_id": "fund-private-macro-discretionary", "label": "主观宏观"},
            {"node_id": "fund-private-macro-systematic", "label": "系统化宏观"},
        ],
    },
    {
        "node_id": "fund-private-managed-futures",
        "label": "管理期货",
        "children": [
            {"node_id": "fund-private-managed-futures-trend", "label": "趋势跟踪"},
            {"node_id": "fund-private-managed-futures-relative-value", "label": "期货相对价值"},
            {"node_id": "fund-private-managed-futures-multi", "label": "CTA 多策略"},
        ],
    },
    {
        "node_id": "fund-private-event-driven",
        "label": "事件驱动",
        "children": [
            {"node_id": "fund-private-event-driven-merger-arbitrage", "label": "并购套利"},
            {"node_id": "fund-private-event-driven-special-situations", "label": "特殊事件"},
        ],
    },
    {
        "node_id": "fund-private-relative-value",
        "label": "相对价值",
        "children": [
            {"node_id": "fund-private-relative-value-fixed-income", "label": "固定收益相对价值"},
            {"node_id": "fund-private-relative-value-convertible", "label": "可转债套利"},
            {"node_id": "fund-private-relative-value-volatility", "label": "波动率 / 期权"},
            {"node_id": "fund-private-relative-value-multi-asset", "label": "跨资产相对价值"},
        ],
    },
    {"node_id": "fund-private-multi-strategy", "label": "多策略"},
    {
        "node_id": "fund-private-fund-of-funds",
        "label": "组合基金",
        "children": [
            {"node_id": "fund-private-fund-of-funds-fof", "label": "FOF"},
            {"node_id": "fund-private-fund-of-funds-mom", "label": "MOM"},
        ],
    },
    {"node_id": "fund-private-other", "label": "其他"},
]


_EQUITY_SECTORS = (
    ("energy", "能源"),
    ("materials", "原材料"),
    ("industrials", "工业"),
    ("consumer-discretionary", "可选消费"),
    ("consumer-staples", "日常消费"),
    ("health-care", "医疗保健"),
    ("financials", "金融"),
    ("information-technology", "信息技术"),
    ("communication-services", "通信服务"),
    ("utilities", "公用事业"),
    ("real-estate", "房地产"),
)


def _equity_market_branch(market_code: str, label: str) -> dict[str, Any]:
    return {
        "node_id": f"equity-market-{market_code}",
        "label": label,
        "instrument_type": "equity",
        "children": [
            *[
                {
                    "node_id": f"equity-{market_code}-{sector_code}",
                    "label": sector_label,
                }
                for sector_code, sector_label in _EQUITY_SECTORS
            ],
            {
                "node_id": f"equity-{market_code}-unclassified",
                "label": "待分类",
            },
        ],
    }


def _typed_roots(
    nodes: list[dict[str, Any]],
    instrument_type: str,
) -> list[dict[str, Any]]:
    return [
        {**deepcopy(node), "instrument_type": instrument_type}
        for node in nodes
    ]


INSTRUMENT_TAXONOMY_TREE = [
    {"node_id": "crypto-native", "label": "原生加密资产", "instrument_type": "crypto"},
    *_typed_roots(_PUBLIC_FUND_TAXONOMY, "public_fund"),
    *_typed_roots(_PRIVATE_FUND_TAXONOMY, "private_fund"),
    {
        "node_id": "etf-equity",
        "label": "权益",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-equity-broad-market", "label": "宽基"},
            {"node_id": "etf-equity-size-style", "label": "规模 / 风格"},
            {"node_id": "etf-equity-sector", "label": "行业"},
            {"node_id": "etf-equity-theme", "label": "主题"},
            {"node_id": "etf-equity-factor", "label": "因子 / 股息"},
        ],
    },
    {
        "node_id": "etf-fixed-income",
        "label": "固定收益",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-fixed-income-government", "label": "利率债"},
            {"node_id": "etf-fixed-income-investment-grade", "label": "投资级信用"},
            {"node_id": "etf-fixed-income-high-yield", "label": "高收益债"},
            {"node_id": "etf-fixed-income-aggregate", "label": "综合债券"},
            {"node_id": "etf-fixed-income-short-duration", "label": "短久期"},
            {"node_id": "etf-fixed-income-inflation-linked", "label": "通胀挂钩"},
            {"node_id": "etf-fixed-income-municipal", "label": "市政债"},
            {"node_id": "etf-fixed-income-convertible", "label": "可转债"},
        ],
    },
    {
        "node_id": "etf-commodity",
        "label": "商品",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-commodity-precious-metals", "label": "贵金属"},
            {"node_id": "etf-commodity-broad", "label": "综合商品"},
            {"node_id": "etf-commodity-single", "label": "单一商品"},
        ],
    },
    {
        "node_id": "etf-real-assets",
        "label": "实物资产",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-real-assets-real-estate", "label": "房地产 / REITs"},
            {"node_id": "etf-real-assets-infrastructure", "label": "基础设施"},
        ],
    },
    {
        "node_id": "etf-multi-asset",
        "label": "多资产",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-multi-asset-allocation", "label": "资产配置"},
            {"node_id": "etf-multi-asset-alternative", "label": "另类多资产"},
        ],
    },
    {
        "node_id": "etf-tactical",
        "label": "战术工具",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-tactical-leveraged", "label": "杠杆"},
            {"node_id": "etf-tactical-inverse", "label": "反向"},
            {"node_id": "etf-tactical-volatility", "label": "波动率"},
        ],
    },
    {"node_id": "etf-cash", "label": "现金管理", "instrument_type": "etf"},
    {"node_id": "etf-other", "label": "其他", "instrument_type": "etf"},
    _equity_market_branch("us", "美股"),
    _equity_market_branch("hk", "港股"),
    _equity_market_branch("cn-a", "A股"),
    _equity_market_branch("eu", "欧洲股市"),
    {
        "node_id": "index-equity",
        "label": "权益指数",
        "instrument_type": "index",
        "children": [
            {"node_id": "index-equity-broad-market", "label": "宽基"},
            {"node_id": "index-equity-size-style", "label": "规模 / 风格"},
            {"node_id": "index-equity-sector", "label": "行业"},
            {"node_id": "index-equity-theme", "label": "主题"},
            {"node_id": "index-equity-factor", "label": "因子 / 股息"},
        ],
    },
    {
        "node_id": "index-fixed-income",
        "label": "固定收益指数",
        "instrument_type": "index",
        "children": [
            {"node_id": "index-fixed-income-government", "label": "政府债"},
            {"node_id": "index-fixed-income-investment-grade", "label": "投资级信用"},
            {"node_id": "index-fixed-income-high-yield", "label": "高收益债"},
            {"node_id": "index-fixed-income-aggregate", "label": "综合债券"},
            {"node_id": "index-fixed-income-inflation-linked", "label": "通胀挂钩"},
            {"node_id": "index-fixed-income-convertible", "label": "可转债"},
        ],
    },
    {
        "node_id": "index-commodity",
        "label": "商品指数",
        "instrument_type": "index",
        "children": [
            {"node_id": "index-commodity-broad", "label": "综合商品"},
            {"node_id": "index-commodity-precious-metals", "label": "贵金属"},
            {"node_id": "index-commodity-single", "label": "单一商品"},
        ],
    },
    {
        "node_id": "index-real-assets",
        "label": "实物资产指数",
        "instrument_type": "index",
        "children": [
            {"node_id": "index-real-assets-real-estate", "label": "房地产 / REITs"},
            {"node_id": "index-real-assets-infrastructure", "label": "基础设施"},
        ],
    },
    {"node_id": "index-multi-asset-allocation", "label": "多资产配置", "instrument_type": "index"},
    {"node_id": "index-alternative", "label": "另类策略", "instrument_type": "index"},
    {"node_id": "index-other", "label": "其他", "instrument_type": "index"},
]


def _flatten_nodes(
    nodes: Iterable[dict[str, Any]],
    *,
    parent_node_id: str | None,
    level_index: int,
    path_labels: list[str],
    path_node_ids: list[str],
    inherited_instrument_type: str | None = None,
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for display_order, node in enumerate(nodes, start=1):
        label = str(node["label"])
        node_id = str(node["node_id"])
        children = [dict(item) for item in node.get("children", []) if isinstance(item, dict)]
        next_path_labels = [*path_labels, label]
        next_path_node_ids = [*path_node_ids, node_id]
        instrument_type = str(node.get("instrument_type") or inherited_instrument_type or "")
        if not instrument_type:
            raise ValueError(f'Taxonomy root node "{node_id}" requires an instrument_type')
        flattened.append(
            {
                "node_id": node_id,
                "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
                "taxonomy_label": INSTRUMENT_TAXONOMY_LABEL,
                "instrument_type": instrument_type,
                "label": label,
                "parent_node_id": parent_node_id,
                "level_index": level_index,
                "display_order": int(node.get("display_order") or display_order),
                "is_leaf": not children,
                "path_labels_json": next_path_labels,
                "path_node_ids_json": next_path_node_ids,
            }
        )
        flattened.extend(
            _flatten_nodes(
                children,
                parent_node_id=node_id,
                level_index=level_index + 1,
                path_labels=next_path_labels,
                path_node_ids=next_path_node_ids,
                inherited_instrument_type=instrument_type,
            )
        )
    return flattened


def instrument_taxonomy_nodes() -> list[dict[str, Any]]:
    return _flatten_nodes(
        deepcopy(INSTRUMENT_TAXONOMY_TREE),
        parent_node_id=None,
        level_index=1,
        path_labels=[],
        path_node_ids=[],
        inherited_instrument_type=None,
    )
