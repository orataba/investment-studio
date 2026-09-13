# 收益序列与区间边界契约

## 1. 图表展示什么

Watchlist 主图把所选区间锚点归一为 `100` 的增长指数，不展示复权净值的绝对点位：

`growth_index(t) = level(t) / level(anchor) * 100`

图例、末值标签和 tooltip 继续展示累计收益百分比：

`cumulative_return_pct(t) = growth_index(t) - 100`

Canonical chart 的原始 level 与最新估值保留源数据经数值解析后的精度，不能提前按固定小数位四舍五入。区间交互及矩阵仍用这些 level 计算收益；小额现货价格尤其不能被舍入为零。小数位格式化仅用于界面标签，不写回计算序列。

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

### 1.1 用户界面命名与内部 basis

`quote` 只作为跨资产的内部总称，不作为基金页面的具体指标名。Watchlist 按资产类型显示：

| 资产类型 | 最新估值字段 | 收益/图表字段 |
| --- | --- | --- |
| 公募、私募基金 | `Unit NAV / 单位净值`（`official_nav`） | `Cumulative NAV / 复权累计净值`（`total_return_nav`，含分红再投资） |
| 股票、ETF | `Latest Price / 最新价格`（valuation role 的 `close` 或 `last`） | `Adjusted Close / 复权收盘价`；只在来源口径确认时称 Total Return |
| 指数 | `Index Level / 指数点位`（valuation role 的 `close` 或 `last`） | 按 Registry 声明显示 `Price Index` 或 `Total Return Index` |
| 原生加密资产 | `Spot Price / 现货价格`（`close` 或 `last`） | `Cumulative Price Return`，不代表基金净值或包含质押、借贷等收益 |
| 混合资产 Watchlist | `Latest Value / 最新值` | 每行继续保留自身明确的 return semantics |

基金 Overview 的主数字固定为单位净值；其下方小字显示同一产品最新复权累计净值。切换图表
basis 不能改变主数字的估值口径。普通现金累计净值不进入这两个用户字段。

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

指数和原生加密资产详情页的 Metrics Matrix 只使用本页所选的 canonical series，各列沿用本节的区间锚点。区间收益取首尾点之比；年化收益使用上述实际日历周年分数；年化波动率、Sharpe 和 Sortino 使用区间内相邻观测收益及按实际观测密度推导的年化频率。Sharpe 的无风险利率和 Sortino 的最低可接受收益均为 0，Sortino 的 downside deviation 以全部期间为分母、非负期间记 0。Calmar 为年化收益除以最大回撤绝对值，因此不足一年或没有回撤时不发布。Recovery Days 从最大回撤谷底计至首次恢复该轮前高；尚未恢复时明确显示 `Unrecovered`。这两种详情页的图表与主报价也只取 canonical series；缺少该序列时不回退到未采用的原始 OHLC 行情。

详情页 Overview 主图不提供固定窗口快捷按钮，只保留双端拖动范围条。拖动手柄绑定真实观测点，图例同时展示实际锚点和实际终点。标量 performance 字段覆盖 `1W / 1M / 3M / 6M / MTD / YTD / 1Y`；Sparkline 仍只物化 `1D / 1W / 1M / 1Y` 的有界展示路径。两类输出都使用本节边界规则，但字段集合不要求相同。

## 3. Watchlist 的逐 instrument as-of

- Watchlist 没有一个强制所有行共用的业务 `as_of_date`。每个 instrument 都以自己所选 calculation series 的最新有效观测日为终点，再独立回看 1W / 1M / 3M / 6M / MTD / YTD / 1Y。
- Screener 每行始终返回 `metric_as_of_date`。页级 `snapshot_metadata.as_of_date` 定义为当前结果中的最晚行终点，仅用于摘要，不得当成所有行的共同终点；同时返回 `as_of_date_min / as_of_date_max / has_mixed_as_of_dates / as_of_date_missing_count`。
- 分组中的收益、波动率、回撤、Sharpe 和 peer 指标只有在所有有值行的 `metric_as_of_date` 相同时才展示等权横截面平均；终点混合或缺失时必须显示不可用。
- taxonomy peer 排名只纳入与目标 instrument **同一 snapshot as-of** 的候选；不同终点的候选被排除并记录数量，不能拿 24 日结果与 27 日结果直接排名。
- benchmark 比较只使用双方日期完全相同的共同观测收盘点。基金自身独立指标仍用自己的 as-of；一旦展示 benchmark，矩阵中的基金值要重算到最晚共同观测终点，SI 从最早共同观测起点开始，所有相对风险统计也只能链接连续的共同 `(start_date, end_date)`。不得把基金目标日前的周五收盘与 benchmark 的周四收盘当作同一期相减。

## 4. 频率、缺点与风险

- 计算频率固定为 daily；行情点统一按日频计算，不做降采样。
- daily 数据若配置 `market_calendar`，内部缺点按该交易所真实 session 检测，春节、周末等休市不算缺失；未配置或日历不可解析时才使用保守的日历日阈值。
- 尾部新鲜度按该 instrument 自己的 market calendar 和 `release_lag_days` 判断，不拿 Watchlist 中其他 instrument 的最新日期做基准。有可用日历时，`release_lag_days` 按该日历的交易日数量延后；24/7 日历的交易日即 UTC 自然日。未显式声明滞后的邮箱私募采用一个交易日，显式配置优先。当日盘中不会强制要求“计划于当日发布”的观测已经到达；无可用日历时按自然日宽限保守判断，不声称已核实具体发布日。
- 标量端点收益在起点和终点可信时仍可计算；最大回撤、当前回撤、波动率、downside deviation、Sharpe、Sortino 等路径指标在存在预期观测缺失时 fail closed。downside deviation 以全部期间为分母，非负期间的 downside 为 0；Sharpe/Sortino 当前风险自由利率为 0。

### 4.1 原生加密资产与 UTC 日线

- `crypto` 是原生资产类型，与股票、ETF 和指数分开登记。当前维护的 BTC/USD 序列以美元/枚 BTC 报价，`return_semantics=price_return`；不把 BTC/USD 与 BTC/USDT 混接，也不把现货包装为 ETF 或指数。
- `market_calendar=24/7`，每个 UTC 自然日均应有观察值，包括周末。采集投影只接受早于当前 UTC 日期的已完成日线；当日未完成价格不进入这条收盘研究序列。新鲜度以 UTC 前一日为预期最新日，不能套用纽约股票交易日。
- 连续日频风险的年化观察数为 `365.25`；通用风险计算仍按 `观察间隔数 / 实际跨度天数 × 365.25` 推导频率。缺失任何预期 UTC 日线即标记缺口，不把周末当休市，也不以降低年化观察数掩盖缺失。收益年化仍使用第 2 节的完整日历周年规则。
- Metrics Matrix 按各自窗口逐日验证完整性。历史早期缺口继续使 SI 的路径风险不可用，但不使缺口之后完整的 1W/1M 等窗口失效；没有补点或插值。后端全历史风险仍保留原来的覆盖不足状态。
- BTC/USD 当前点由共享 `market_series_daily` 投影，canonical provider 标识来源序列，日期标识观察日；原始版本、采集批次和按获知时间查询的历史版本保留在 numeric 存储。canonical 当前点会随来源修订更新，并未逐点绑定不可变的原始批次；不能单靠当前点重建历史 PIT。可复核的研究须使用当时冻结的研究输入及原始来源版本。
- Watchlist 的登记、行情与研究支持不授予 Portfolio 交易能力；当前 Portfolio 的交易选项及录入校验排除 `crypto`。

## 5. 私募基金断段规则

- `window_normalized_anchor` 只表示观测窗口起点归一为因子 `1`；它不声称累计现金差额为零。
- 窗口内疑似现金分红但尚未确认时，复权收益序列在事件前停止，并标记 `partial`。
- 管理员确认分红及再投资净值后，投影、Watchlist read model 和 Portfolio 相关任务按统一队列重算。
- 不得用“单位净值加历史现金分红”的现金累计值冒充再投资复权累计净值。

## 6. Watchlist 内单一实现来源与跨 app 边界

- 后端区间策略：`watchlist_app.services.return_windows`。
- 前端交互图策略：`src/lib/returnWindows.ts`，与后端使用同一组黄金边界测试。
- performance、risk、return-window 和 row materialization 的当前版本标识由
  [`canonical_recalc.py`](../backend/watchlist_app/services/canonical_recalc.py)、
  [`return_windows.py`](../backend/watchlist_app/services/return_windows.py) 与
  [`materialization_policy.py`](../backend/watchlist_app/services/materialization_policy.py) 中的常量唯一维护，文档不复制易漂移的版本号。方法或 payload 改变时必须提升对应版本并重建旧结果。
- `return_3m / return_6m` 与其他标量 performance 字段一样，从 canonical performance snapshot 投影到 watchlist row read model；迁移、repository、serializer、field registry 和导出不得缺少其中任一层。
- Sparkline 字段为 `return_chart_1d / 1w / 1m / 1y`。
- Portfolio 与 Watchlist 独立计算行情收益和组合绩效，不共享这些业务 helper 或 read model。两边以本文边界、Registry series identity 和黄金用例保持一致：同一 instrument、请求日期、已确认 total-return basis 和窗口下，Watchlist 标量收益与 Portfolio Holdings instrument row 必须相同；Portfolio group / total 的当前权重篮子计算仍只属于 Portfolio。标的风险跟进和只读研究上下文的 API 连接不改变这一计算边界。

## 7. 研究比较与跌幅复核线

研究对话的比较工具采用请求区间内的共同实际观察日，不前向填充。首尾收益、路径回撤和相邻区间相关性均以该共同样本为准，不冒充完整日频路径或年化指标；样本日期、观察数、币种、实际收益口径和剔除原因随结果返回。不同币种不直接混算；收益口径混合或缺失观察值时明确披露可比性限制。没有共同基准时不生成超额收益，观察列表也不代表全市场排名。

标的风险的近 1 日、1 周、1 月、1 季分别使用最近 1、5、21、63 个日频观察间隔；`24/7` 加密资产使用 1、7、30、90 个 UTC 日频间隔。这些是跌幅复核窗口，与第 2 节按自然月计算的 1M/3M 业绩窗口分别定义。只有已就绪、无未确认断点且无缺口的日频序列才参与；不足窗口则显示不可用。复核线首次使用最多 252 个对数收益、至少 63 个收益的样本标准差 `sigma` 校准；`24/7` 序列改为最多 365、至少 90 个收益。按 `100 × (1 − exp(−k × sigma × sqrt(n)))` 得到正的跌幅容忍值。四个窗口的 `k` 为 3、2.5、2、1.5，最低容忍值为 0.5%、1%、2%、3%，向上取整到 0.5 个百分点。这些是初始复核规则，不是经验分位数或预测损失概率。

研究中的 EWMA 价格波动另按资产日历定义：股票、ETF、指数使用 21 个交易观察的半衰期，最多 252、至少 63 个连续实际收益并按 252 年化；原生加密资产使用 30 个 UTC 日的半衰期，最多 365、至少 90 个连续实际收益并按 365.25 年化。两者只采用最后一个缺口之后的连续样本，指数权重归一并扣除加权均值；不得将基金净值套入该价格波动定义。

规则和校准样本保存后不随日常波动自动抬升，可逐窗口人工修改或关闭。自动观察由现有 recalc 流程更新；GET 只读，不因打开页面生成事项。资料不足不能证明风险恢复；触发解除与人工跟进状态分别保留。实现和验证见 [price_risk.py](../backend/watchlist_app/services/price_risk.py) 与 [test_price_risk.py](../backend/tests/test_price_risk.py)。
