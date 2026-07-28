# Holdings 字段计算与分组标准

> 文档状态：当前实现合同。本文逐字段定义 Portfolio Holdings 的展示、计算、分组和不可用规则。

> 权威边界：[`01_CALCULATION_SPEC.md`](./01_CALCULATION_SPEC.md) 定义 Portfolio 的领域计算合同；本文是该合同在 Holdings 表格字段上的完整映射。README 和用户手册只做摘要，发生冲突时不得用摘要覆盖这两份规格。

## 1. Holdings 回答什么问题

Holdings 是指定 `as_of_date` 的**当前开放持仓快照**。一条非现金 instrument row 会合并该资产在当前组合各证券账户里的开放头寸；现金则按 settled cash ledger 逐币种生成 `cash:{currency}` 行。

它主要回答两类问题：

- 当前还持有什么：数量、市值、权重、剩余账面成本、未实现盈亏；
- 以当前这些资产和当前权重回看，标的自身及当前持仓篮子的历史市场表现和风险如何。

它不回答真实历史组合在某个区间内赚了多少。组合 TWR、期间贡献、已实现资本利得、Income、费用、税费和已经卖出的仓位属于 Overview / Performance。Holdings 的 group、subtotal 和 `Portfolio Total` 也不是 Performance group。

Portfolio 和 Watchlist 各自实现自己的读路径，不导入对方服务或通过运行时调用复用计算。对于同一 instrument、同一请求 `as_of_date`、同一已确认 total-return basis 和同一窗口边界，Holdings row 的 `1M Return` 等标的指标应与 Watchlist 对应字段数值一致；这是一致性合同，不是应用耦合。

## 2. 公共计算约定

### 2.1 日期、行情与币种

- 未显式指定日期时，Holdings 使用 latest fresh complete snapshot；不能把部分资产已经更新的更晚日期当作组合 `as_of_date`。
- 行级 `Market Value`、`Cost Basis`、`Day Change` 和 `Unrealized P&L` 使用标的本币展示；对应 `* Base` 字段及所有 group / subtotal / total 金额使用组合 base currency。
- 非现金市值为 `quantity × selected valuation quote × price_scale`；再用 `as_of_date` FX 转为 base currency。现金市值为 settled cash amount；pending settlement 不生成 Holdings 行。
- 行级 `Weight = market_value_base / portfolio NAV`，其中 NAV 包含 pending settlement；因此 Holdings rows 的权重合计等于 `Holdings market value / NAV`，结算未完成时不保证正好为 100%。Return / Risk 的当前篮子只在参与成员内部按当前 market value 归一，pending settlement 不被伪造成有收益序列的持仓。
- 缺少唯一且合法的 valuation quote、价格单位/scale 或 FX 时，依赖它的字段为不可用，不用旧价格、图表点或 0 补齐。

### 2.2 成本、盈亏与收益分类

- `Cost Basis` 是当前开放头寸的 remaining book cost。FIFO 为开放 lots 剩余成本之和；moving average 为滚动平均成本 bucket 的剩余成本。
- `Avg Cost = remaining cost basis / remaining quantity`。它不是某笔成交价，也不把显示舍入后的价格反写到账本。
- `Unrealized P&L = current market value - remaining open-position cost basis`；现金不适用。
- 普通 `dividend / coupon` 在 entitlement date 进入已实现 `Income`，不冲减开放成本，也不进入 `Unrealized P&L`。
- `dividend_reinvestment` 先确认已实现 `Income`，再以 reinvested gross amount 建立新 lot；不得建立零成本份额或重复记现金。
- 只有明确的 `return_of_capital` 才冲减开放成本；超过剩余成本时失败关闭，不能假装成负成本。

### 2.3 标的收益、风险与窗口

- `1W / 1M / 3M / 6M / MTD / YTD / 1Y Return`、drawdown 和 volatility 只使用 Registry 已明确确认的单一 total-return series。基金使用 `total_return_nav`；其他 basis 只有在 Registry 明确声明 total-return semantics 时才可用。
- 滚动窗口从**请求的** `as_of_date` 回看；终点是 `as_of_date` 或之前最新点，锚点仍按请求日期计算，不能因为终点 stale 就整体向前平移窗口。1M/3M/6M/1Y 按自然月/年回看，1W 按 7 个日历日回看。
- Volatility 和用于分组 Drawdown 的 return path 还必须通过尾部新鲜度检查：日频最多落后 `5` 个自然日、周频 `14` 日、月频 `62` 日。整组成员即使共同停在同一个旧日期，也不能因为“彼此对齐”就发布已经陈旧的风险值。
- MTD / YTD 使用严格早于月初 / 年初的最近有效点作为锚点；期间内第一点不能冒充完整自然期间。
- `Chart *` 是有界采样的展示路径，可以是明确标注的 price-return 或 total-return path；它不是 Return、Volatility 或 Drawdown 的替代输入。
- 缺少完整覆盖、唯一 series identity、合法 return semantics、必要锚点、共同 return currency 或最小样本时显示 `—`，不把缺失解释为 0。只有 base-currency cash 的经济收益和风险天然为 0。

### 2.4 分组的六种类型

| 代码 | 含义 |
| --- | --- |
| `none` | 只在 instrument row 展示；group、non-cash subtotal 和 total 留空。`Instrument` 列仍用作组名或 `Portfolio Total` 标签，不代表该字段被聚合。 |
| `sum` | 对当前 rows 的绝对量求和；金额先统一为 base currency。 |
| `recomputed_ratio` | 用组级分子和分母重新计算，禁止平均成员百分比。 |
| `current_weight_return` | 用 `as_of_date` base-market-value 权重合成当前成员自身 total return。 |
| `current_basket_path` | 在共同 period 上先生成当前权重篮子 return path，再计算 volatility / drawdown。 |
| `portfolio_risk_contribution` | 成员都相对于同一个完整全组合 forward-risk variance；分组只加总这些贡献。 |

当前权重 return/risk 要求有当前市值的成员 100% 覆盖。base-currency cash 可作为 0-return 成员；non-base cash 使用兑 base currency 的 FX return。成员 return currency 不一致且没有统一的 base-currency return series 时，结果留空，不能直接拼接各自本币收益。

## 3. 完整字段字典

下表必须与前端 `HOLDINGS_GROUP_AGGREGATION_KIND` 一一对应。表内的机器可读标记由测试使用，增删字段时必须同时更新实现、本文和 contract tests。

<!-- holdings-column-contract:start -->
| 字段 key | UI 标签 | instrument row 口径 | group / subtotal / total | 类型 |
| --- | --- | --- | --- | --- |
| `instrument` | Instrument | canonical instrument name；现金为对应币种现金名称 | 仅作为组名、subtotal 或 total 标签 | `none` |
| `ticker` | Ticker | Registry primary identifier | 留空 | `none` |
| `instrument_type` | Instrument Type | canonical instrument type | 留空；可作为 Group By 维度 | `none` |
| `taxonomy_top` | Taxonomy | 当前默认 planning taxonomy 的顶层节点；无 assignment 为 Unassigned | 留空；可作为 Group By 维度 | `none` |
| `taxonomy_leaf` | Taxonomy Leaf | 当前默认 planning taxonomy 的叶节点；无 assignment 为 Unassigned | 留空；可作为 Group By 维度 | `none` |
| `currency` | Currency | instrument currency；现金为该现金币种 | 留空；可作为 Group By 维度 | `none` |
| `holding_date` | Holding Since | 当前开放头寸里最早的 holding start date；不包含已经平掉的旧头寸 | 留空，不拼接不同成员起点 | `none` |
| `quantity` | Quantity | 当前开放 quantity；现金行是 settled cash amount | 留空，不跨不同份额单位求和 | `none` |
| `cost_method` | Cost Method | 账户成本法；跨账户不一致时为 Mixed | 留空 | `none` |
| `avg_cost_book` | Avg Cost | 非现金 `remaining cost basis / remaining quantity`；现金不适用 | 留空，不平均成员成本 | `none` |
| `last_price` | Quote | `as_of_date` 选中的 valuation quote；现金不适用 | 留空 | `none` |
| `quote_date` | Quote Date | 上述 valuation quote 的实际日期 | 留空 | `none` |
| `quote_basis` | Quote Basis | 实际 valuation basis，例如 `official_nav` 或 `close` | 留空 | `none` |
| `quote_provider` | Provider | 选中 valuation series 的 provider | 留空 | `none` |
| `quote_status` | Quote Status | valuation selection / freshness 状态 | 留空 | `none` |
| `market_value` | Market Value | 标的本币市值；现金为 settled amount | 按 base currency 加总 | `sum` |
| `market_value_base` | Market Value Base | `Market Value` 按 `as_of_date` FX 转为组合 base currency | 直接加总 | `sum` |
| `cost_basis` | Cost Basis | 非现金开放头寸本币 remaining book cost；现金不适用 | 非现金成本按 base currency 加总 | `sum` |
| `cost_basis_base` | Cost Basis Base | 非现金开放成本按 `as_of_date` FX 转为 base currency | 直接加总 | `sum` |
| `weight` | Weight | `market_value_base / portfolio NAV`；NAV 包含 pending settlement | 加总当前 row 权重 | `sum` |
| `accounts` | Accounts | 当前持有该 instrument 的 distinct account 数 | 留空，账户集合可能重叠 | `none` |
| `open_lots` | Open Lots | 当前开放 lot 数；moving average synthetic lot 按一个开放 lot 展示 | 加总开放 lot 数 | `sum` |
| `day_change_value` | Day Change | 当前持仓规模上的单日经济变动；非现金为 `current market value - current market value / (1 + day return)`，优先使用完整 total-return pair，否则使用完整 valuation pair；non-base cash 为 `amount × (current FX - prior FX)` | base-currency day change 加总 | `sum` |
| `day_change_pct` | Day Return | 上述同一完整点对的收益；base cash 为 0，non-base cash 为 FX return | `sum(day change base) / sum(prior market value base)` 重算 | `recomputed_ratio` |
| `unrealized_value` | Unrealized P&L | 非现金 `current market value - remaining open cost`；不含已实现 Income | base-currency unrealized P&L 加总 | `sum` |
| `unrealized_pct` | Unrealized Return | 非现金 `unrealized P&L / remaining open cost`；零或无成本时不可用 | `sum(unrealized P&L base) / sum(non-cash open cost base)` 重算 | `recomputed_ratio` |
| `instrument_return_1w` | 1W Return | instrument 自身 confirmed total return，锚点为请求 `as_of_date - 7` 日或此前最近点 | 当前 base-market-value 权重合成，要求完整覆盖 | `current_weight_return` |
| `instrument_return_1m` | 1M Return | instrument 自身 confirmed total return，按请求 `as_of_date` 回看 1 个自然月 | 当前 base-market-value 权重合成，要求完整覆盖 | `current_weight_return` |
| `instrument_return_3m` | 3M Return | instrument 自身 confirmed total return，按请求 `as_of_date` 回看 3 个自然月 | 当前 base-market-value 权重合成，要求完整覆盖 | `current_weight_return` |
| `instrument_return_6m` | 6M Return | instrument 自身 confirmed total return，按请求 `as_of_date` 回看 6 个自然月 | 当前 base-market-value 权重合成，要求完整覆盖 | `current_weight_return` |
| `instrument_return_mtd` | MTD | instrument 自身 total return；锚点严格早于月初 | 当前 base-market-value 权重合成，要求完整覆盖 | `current_weight_return` |
| `instrument_return_ytd` | YTD | instrument 自身 total return；锚点严格早于年初 | 当前 base-market-value 权重合成，要求完整覆盖 | `current_weight_return` |
| `instrument_return_1y` | 1Y | instrument 自身 confirmed total return，按请求 `as_of_date` 回看 12 个自然月 | 当前 base-market-value 权重合成，要求完整覆盖 | `current_weight_return` |
| `instrument_current_drawdown` | Current DD | confirmed total-return series 的最新点相对历史峰值 | 用共同 period 和当前权重篮子 path 重算 | `current_basket_path` |
| `instrument_max_drawdown` | Max DD | confirmed total-return series 可用历史上的最大回撤 | 用共同 period 和当前权重篮子 path 重算 | `current_basket_path` |
| `instrument_holding_max_drawdown` | Held Max DD | 从当前最早开放持仓日起的最大回撤；不含已经平掉的旧持有期 | 留空，不为不同持有起点创造伪共同区间 | `none` |
| `instrument_volatility_1m` | 1M Vol | confirmed total-return series 的 trailing sample volatility，按 resolved frequency 和实际 elapsed days 年化 | 用 1M 共同 period 当前篮子 path 重算 | `current_basket_path` |
| `instrument_volatility_3m` | 3M Vol | 同上，3M 窗口 | 用 3M 共同 period 当前篮子 path 重算 | `current_basket_path` |
| `instrument_volatility_6m` | 6M Vol | 同上，6M 窗口 | 用 6M 共同 period 当前篮子 path 重算 | `current_basket_path` |
| `instrument_volatility_1y` | 1Y Vol | 同上，1Y 窗口 | 用 1Y 共同 period 当前篮子 path 重算 | `current_basket_path` |
| `forward_risk_share` | Forward RC | 当前非现金权重在 Production Risk Model 下相对全组合 variance 的 risk contribution share；现金为 0 | workspace 风险模型完整时加总成员贡献，否则留空 | `portfolio_risk_contribution` |
| `price_chart_1m` | Chart 1M | 1M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_3m` | Chart 3M | 3M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_6m` | Chart 6M | 6M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_1y` | Chart 1Y | 1Y 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `coverage` | Coverage | valuation、NAV、FX、trend series 等覆盖诊断 | 留空；可作为 Group By 维度 | `none` |
<!-- holdings-column-contract:end -->

## 4. 分组计算细则

### 4.1 当前权重收益

对 group 中有当前市值的成员：

`group_return = Σ(current_market_value_base_i / group_market_value_base × instrument_total_return_i)`

覆盖不足时整个 group 留空，不能剔除缺失成员后重新归一。`Non-cash Portfolio` 和 `Portfolio Total` 遵守同一规则。该结果表示“如果当前篮子在该历史窗口一直保持当前权重”，不是实际组合 TWR。

### 4.2 当前篮子风险

先在共同 period identity 和共同 observation dates 上构造成员收益，再用当前权重得到 group return series；volatility、current drawdown 和 max drawdown 都从这条 group path 计算，不能加权平均成员风险值。混合数据频率使用 workspace 已解析的共同 daily / weekly / monthly basis，不跨 period forward-fill。

成员可以在共同起点之前拥有不同长度的早期历史；共同起点取各成员首个合法 period start 中最晚者。从该点开始，每个成员必须具有完全相同、内部连续且顺序一致的 `(period_start, period_end)` identity：后一段的 start 必须等于前一段的 end。任何成员在共同区间中间或尾部缺一段，或所有成员共同缺失同一段，整个 group 指标都应显示不可用，不能静默取交集后把缺口隐藏。

### 4.3 Forward RC

所有非现金成员必须来自同一个完整 Production Risk Model、同一 leaf covariance matrix 和同一组合 variance。窗口从 holdings as-of date 按自然月回看；成员 return 的 start/end period identity 必须完全一致，strict 与 complete-case policy 都校验尾部新鲜度。group / subtotal / total 只能加总成员相对于全组合分母的 risk share；不能先合成 group return 再运行 shrinkage，也不能在每个 group 内另建局部分母。`abs` mode 使用精确绝对贡献，零贡献保持为零。任一 active risk holding、FX 或 period identity 缺失时，整个 forward-risk 结果失败关闭。

## 5. 不可用与排查顺序

`—` 表示缺数据、口径不适用或严格条件未满足，不等于 0。排查顺序：

1. 数量或 Holding Since：检查 Transactions 的 trade date、账户、instrument、quantity 和历史卖出/转仓；
2. 成本或未实现盈亏：检查账户 FIFO / moving average、gross amount、费用税费、分红/资本返还分类；
3. 市值或权重：检查 valuation quote、quote basis、price scale、FX 和 snapshot freshness；
4. Return / Vol / Drawdown：检查 Registry total-return semantics、窗口锚点、历史覆盖和 risk frequency；
5. group 指标：检查每个当前成员是否 100% 覆盖、return currency 是否一致；
6. Forward RC：检查 Production Risk Model、完整对齐收益矩阵、组合 variance 和 workspace status。
