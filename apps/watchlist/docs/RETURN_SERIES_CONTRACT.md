# 收益序列与区间边界契约

## 1. 图表展示什么

Watchlist 主图把所选区间锚点归一为 `100` 的增长指数，不展示复权净值的绝对点位：

`growth_index(t) = level(t) / level(anchor) * 100`

图例、末值标签和 tooltip 继续展示累计收益百分比：

`cumulative_return_pct(t) = growth_index(t) - 100`

- `total_return_nav`、`adjusted_close` 标记为 `Cumulative Total Return`。
- 指数的 `close`、`last` 只是供应商字段身份，不决定经济收益口径。必须读取 Registry 的
  `source_settings.return_semantics`：`total_return` 标记为 `Cumulative Total Return`，
  `price_return` 标记为 `Cumulative Price Return`；未声明或无法确认时保持 Unknown。
- 非指数资产的 `close`、`last` 标记为 `Cumulative Price Return`，不得暗示包含分红。
- `official_nav` 若用于显式分析，只能标记为 `Cumulative Unit NAV Return`。

选择 benchmark 时，主序列与 benchmark 必须同时具有明确且相同的 `return_kind` 才能
计算 tracking error、information ratio、beta、capture ratio 等相对指标。Unknown 或口径
不一致时可以保留独立曲线，但相对指标必须 fail closed。

私募基金的窗口前复权因子会在比值中约去。因此，可靠的单位净值、窗口内现金累计披露和已确认行为足以计算窗口内收益；这不代表系统知道窗口前全部分红历史。

## 2. 边界定义

| 场景 | 请求起点 | 实际锚点 | 请求终点 | 实际终点 |
| --- | --- | --- | --- | --- |
| 自定义 2 日至 8 日 | 2 日 | 2 日或此前最近有效收盘 | 8 日 | 8 日或此前最近有效收盘 |
| MTD | 当月 1 日 | 严格早于当月 1 日的最近有效收盘 | 最新日期 | 不晚于最新日期的最近有效收盘 |
| YTD | 当年 1 月 1 日 | 严格早于 1 月 1 日的最近有效收盘 | 最新日期 | 不晚于最新日期的最近有效收盘 |
| 1D / 1W / 1M / 1Y | 最新日期减对应日历跨度 | 不晚于请求起点的最近有效收盘 | 最新日期 | 不晚于最新日期的最近有效收盘 |

因此，自定义 2 日至 8 日的收益是 `close(8) / close(2) - 1`，不是从 1 日收盘起算。MTD 和 YTD 则必须包含期初第一天的收益，所以使用期初前一有效收盘。

周频、节假日或停牌序列不得制造虚拟边界点。API 必须返回 `requested_start_date`、`anchor_date`、`requested_end_date` 和 `end_date`，让调用方看到实际采用的观测日期。

详情页 Overview 主图不提供固定窗口快捷按钮，只保留双端拖动范围条。拖动手柄绑定真实观测点，图例同时展示实际锚点和实际终点；MTD、YTD 和滚动窗口仍用于指标卡与 Sparkline，并继续调用同一份边界契约。

## 3. 私募基金断段规则

- `window_normalized_anchor` 只表示观测窗口起点归一为因子 `1`；它不声称累计现金差额为零。
- 窗口内疑似现金分红但尚未确认时，复权收益序列在事件前停止，并标记 `partial`。
- 管理员确认分红及再投资净值后，投影、Watchlist read model 和 Portfolio 相关任务按统一队列重算。
- 不得用“单位净值加历史现金分红”的现金累计值冒充再投资复权累计净值。

## 4. 单一实现来源

- 后端区间策略：`watchlist_app.services.return_windows`。
- 前端交互图策略：`src/lib/returnWindows.ts`，与后端使用同一组黄金边界测试。
- Watchlist performance snapshot 使用 `canonical-performance/v5` 和 `return-window/v1`。
- Sparkline 字段为 `return_chart_1d / 1w / 1m / 1y`；旧 `price_chart_*` 字段已迁移并删除。
