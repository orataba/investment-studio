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
| MTD | 请求 `as_of_date` 所在月 1 日 | 严格早于月初的最近有效收盘 | 请求 `as_of_date` | 不晚于请求终点的最近有效收盘 |
| YTD | 请求 `as_of_date` 所在年 1 月 1 日 | 严格早于年初的最近有效收盘 | 请求 `as_of_date` | 不晚于请求终点的最近有效收盘 |
| 1D / 1W / 1M / 3M / 6M / 1Y | 请求 `as_of_date` 减对应日历跨度 | 不晚于请求起点的最近有效收盘 | 请求 `as_of_date` | 不晚于请求终点的最近有效收盘 |

因此，自定义 2 日至 8 日的收益是 `close(8) / close(2) - 1`，不是从 1 日收盘起算。MTD 和 YTD 则必须包含期初第一天的收益，所以使用期初前一有效收盘。

滚动窗口的请求起点始终由**请求的** `as_of_date` 推导。即使序列实际终点早于请求终点，也不得把请求起点随 stale 终点整体向前移动；否则所谓 1M 会悄悄变成另一个长度。1M / 3M / 6M / 1Y 按自然月/年回看，不用固定 30 / 90 / 180 / 365 日替代。

周频、节假日或停牌序列不得制造虚拟边界点。API 必须返回 `requested_start_date`、`anchor_date`、`requested_end_date` 和 `end_date`，让调用方看到请求边界与实际采用观测日期的差异。

Performance 页的自然期矩阵另有一条闭合规则：月度收益使用当月最后一个有效收盘/净值与上一个自然月最后一个有效收盘/净值相除；年度收益使用相邻自然年的最后一个有效收盘/净值。缺少整个月或整年时，不把更早期间的点跨接后冒充当前月/年收益。基于月度收益的滚动波动率、Beta 和连续下跌月数也只使用连续自然期，遇到缺口即重新开始/暂不发布该窗口。

矩阵单元格的 tooltip 会显示实际 `anchor_date` 与 `end_date`；因此当最新自然月/自然年尚未完整结束，或基金只提供月中/周频净值时，界面不会把实际采用的观察日伪装成月末/年末。

少于一个完整日历年的区间不得展示年化收益。达到首个日历周年后，年化指数按实际日历周年分数计算，不用 `365.25 / days` 把短历史外推成年化数字。

详情页 Overview 主图不提供固定窗口快捷按钮，只保留双端拖动范围条。拖动手柄绑定真实观测点，图例同时展示实际锚点和实际终点。标量 performance 字段覆盖 `1W / 1M / 3M / 6M / MTD / YTD / 1Y`；Sparkline 仍只物化 `1D / 1W / 1M / 1Y` 的有界展示路径。两类输出都使用本节边界规则，但字段集合不要求相同。

## 3. Watchlist 的逐 instrument as-of

- Watchlist 没有一个强制所有行共用的业务 `as_of_date`。每个 instrument 都以自己所选 calculation series 的最新有效观测日为终点，再独立回看 1W / 1M / 3M / 6M / MTD / YTD / 1Y。
- Screener 每行始终返回 `metric_as_of_date`。页级 `snapshot_metadata.as_of_date` 只为兼容保留，含义是当前结果中的最晚行终点，不得当成所有行的共同终点；同时返回 `as_of_date_min / as_of_date_max / has_mixed_as_of_dates / as_of_date_missing_count`。
- 分组中的收益、波动率、回撤、Sharpe 和 peer 指标只有在所有有值行的 `metric_as_of_date` 相同时才展示等权横截面平均；终点混合或缺失时必须显示不可用。
- taxonomy peer 排名只纳入与目标 instrument **同一 snapshot as-of** 的候选；不同终点的候选被排除并记录数量，不能拿 24 日结果与 27 日结果直接排名。
- benchmark 比较只使用双方日期完全相同的共同观测收盘点。基金自身独立指标仍用自己的 as-of；一旦展示 benchmark，矩阵中的基金值要重算到最晚共同观测终点，SI 从最早共同观测起点开始，所有相对风险统计也只能链接连续的共同 `(start_date, end_date)`。不得把基金目标日前的周五收盘与 benchmark 的周四收盘当作同一期相减。

## 4. 频率、缺点与风险

- 计算频率优先使用 Registry `source_settings.expected_frequency`；单点历史标签和样本间隔只用于审计或在注册设置不可用时推断，不能让一条早期 weekly 标签把当前 daily 序列整体降采样。
- daily 数据若配置 `market_calendar`，内部缺点按该交易所真实 session 检测，春节、周末等休市不算缺失；未配置或日历不可解析时才使用保守的日历日阈值。
- 尾部新鲜度按该 instrument 自己的 expected frequency、market calendar 和 `release_lag_days` 判断，不拿 Watchlist 中其他 instrument 的最新日期做基准。`release_lag_days` 是自然日发布滞后，不是交易日数量；当日盘中不会强制要求“计划于当日发布”的观测已经到达。
- 标量端点收益在起点和终点可信时仍可计算；最大回撤、当前回撤、波动率、downside deviation、Sharpe、Sortino 等路径指标在存在预期观测缺失时 fail closed。downside deviation 以全部期间为分母，非负期间的 downside 为 0；Sharpe/Sortino 当前风险自由利率为 0。

## 5. 私募基金断段规则

- `window_normalized_anchor` 只表示观测窗口起点归一为因子 `1`；它不声称累计现金差额为零。
- 窗口内疑似现金分红但尚未确认时，复权收益序列在事件前停止，并标记 `partial`。
- 管理员确认分红及再投资净值后，投影、Watchlist read model 和 Portfolio 相关任务按统一队列重算。
- 不得用“单位净值加历史现金分红”的现金累计值冒充再投资复权累计净值。

## 6. Watchlist 内单一实现来源与跨 app 边界

- 后端区间策略：`watchlist_app.services.return_windows`。
- 前端交互图策略：`src/lib/returnWindows.ts`，与后端使用同一组黄金边界测试。
- Watchlist performance/risk snapshot 使用 `canonical-performance/v7`、`canonical-risk/v6` 和 `return-window/v2`；Watchlist row 使用 `watchlist-materialization/v5`。
- `return_3m / return_6m` 与其他标量 performance 字段一样，从 canonical performance snapshot 投影到 watchlist row read model；迁移、repository、serializer、field registry 和导出不得缺少其中任一层。
- Sparkline 字段为 `return_chart_1d / 1w / 1m / 1y`；旧 `price_chart_*` 字段已迁移并删除。
- Portfolio 与 Watchlist 不共享业务 helper、read model 或运行时 API。两边以本文边界、Registry series identity 和黄金用例保持一致：同一 instrument、请求日期、已确认 total-return basis 和窗口下，Watchlist 标量收益与 Portfolio Holdings instrument row 必须相同；Portfolio group / total 的当前权重篮子计算仍只属于 Portfolio。
