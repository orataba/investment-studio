# Holdings 字段计算与分组标准

> 文档状态：当前实现合同。本文逐字段定义 Portfolio Holdings 的展示、计算、分组和不可用规则。

> 权威边界：[`01_CALCULATION_SPEC.md`](./01_CALCULATION_SPEC.md) 定义 Portfolio 的领域计算合同；本文是该合同在 Holdings 表格字段上的完整映射。README 和用户手册只做摘要，发生冲突时不得用摘要覆盖这两份规格。

## 1. Holdings 回答什么问题

Holdings 是指定 `as_of_date` 的**当前资产负债快照**。一条普通非现金 `position` row 会合并该资产在当前组合各证券账户里的开放头寸；written option 使用独立 `option_obligation` row，现金则按 settled cash ledger 逐币种生成 `cash:{currency}` 行。

它主要回答两类问题：

- 当前还持有什么或承担什么义务：数量、市值或 carrying amount、权重、剩余账面成本、未实现盈亏、short-option contract quantity 与 premium liability；
- 以当前这些资产和当前权重回看，标的自身及当前持仓篮子的历史市场表现和风险如何。

它不回答真实历史组合在某个区间内赚了多少。组合 TWR、期间贡献、已实现资本利得、Income、费用、税费和已经卖出的仓位属于 Overview / Performance。Holdings 的 group、subtotal 和 `Portfolio Total` 也不是 Performance group。

Portfolio 和 Watchlist 各自实现自己的读路径，不导入对方服务或通过运行时调用复用计算。对于同一 instrument、同一请求 `as_of_date`、同一已确认 total-return basis 和同一窗口边界，Holdings row 的 `1M Return` 等标的指标应与 Watchlist 对应字段数值一致；这是一致性合同，不是应用耦合。

## 2. 公共计算约定

### 2.1 日期、行情与币种

- 未显式指定日期时，Holdings 使用 latest fresh complete snapshot；不能把部分资产已经更新的更晚日期当作组合 `as_of_date`。
- 行级 `Position Value`、`Cost Basis`、`Day Change` 和 `Unrealized P&L` 使用标的本币展示；对应 `* Base` 字段及所有 group / subtotal / total 金额使用组合 base currency。对 event-valued asset 和 option obligation，`market_value(_base)` 只是 signed operational NAV amount，不代表 fair value。
- 普通非现金市值为 `quantity × selected valuation quote × price_scale`；再用 `as_of_date` FX 转为 base currency。FCN/长期权在没有可靠 fair value 时使用 `valuation_basis=carried_cost`，written option 使用 `valuation_basis=premium_liability` 和负的 NAV amount；两者的 `fair_value` 及 quote identity 均为空。现金市值为 settled cash amount。
- 行级 `Weight = market_value_base / portfolio NAV`，其中 NAV 包含 pending settlement，option obligation 因而使用负权重。行级 Return / Risk 只允许 `risk_eligible=true` 的 market-valued positions 参与；pending settlement、event-valued asset 和 written liability 都不能被伪造成有自身收益序列的持仓。组合聚合风险另以 total NAV 为分母，将 base-currency monetary rows 与衍生品资本按 0 return 处理。
- 缺少唯一且合法的 valuation quote、价格单位/scale 或 FX 时，依赖它的字段为不可用，不用旧价格、图表点或 0 补齐。

### 2.2 成本、盈亏与收益分类

- `Cost Basis` 是当前开放头寸的 remaining book cost。FIFO 为开放 lots 剩余成本之和；moving average 为滚动平均成本 bucket 的剩余成本。
- `Avg Cost = remaining cost basis / remaining quantity`。它不是某笔成交价，也不把显示舍入后的价格反写到账本。
- `Unrealized P&L = current market value - remaining open-position cost basis`；现金不适用。
- Event-valued position 的 carrying basis 不等于 fair value；option obligation 也没有 long-position Cost Basis / Avg Cost / Unrealized P&L。相关字段留空，liability 使用 obligation operational strip 的 remaining premium basis 与 carrying liability 展示。
- 任一 group 或 `Portfolio Total` 含非零 event-valued asset / option obligation 时，`Unrealized P&L` 和 `Unrealized Return` 整体显示 `N/A`。不得删除该行后只汇总剩余 market-valued positions，也不得用 carrying amount 减 book basis 制造零未实现盈亏。
- 普通 `dividend / coupon` 在 entitlement date 进入已实现 `Income`，不冲减开放成本，也不进入 `Unrealized P&L`。
- `dividend_reinvestment` 先确认已实现 `Income`，再以 reinvested gross amount 建立新 lot；不得建立零成本份额或重复记现金。
- 只有明确的 `return_of_capital` 才冲减开放成本；超过剩余成本时失败关闭，不能假装成负成本。

### 2.3 标的收益、风险与窗口

- `1W / 1M / 3M / 6M / MTD / YTD / 1Y Return`、drawdown 和 volatility 只使用 Registry 已明确确认的单一 total-return series。基金使用 `total_return_nav`；其他 basis 只有在 Registry 明确声明 total-return semantics 时才可用。
- 滚动窗口从**请求的** `as_of_date` 回看；终点是 `as_of_date` 或之前最新点，锚点仍按请求日期计算，不能因为终点 stale 就整体向前平移窗口。1M/3M/6M/1Y 按自然月/年回看，1W 按 7 个日历日回看。
- Volatility 和用于分组 Drawdown 的 return path 还必须通过日频尾部新鲜度检查，最多落后 `5` 个自然日。整组成员即使共同停在同一个旧日期，也不能因为“彼此对齐”就发布已经陈旧的风险值。
- MTD / YTD 使用严格早于月初 / 年初的最近有效点作为锚点；期间内第一点不能冒充完整自然期间。
- `Chart *` 是有界采样的展示路径，可以是明确标注的 price-return 或 total-return path；它不是 Return、Volatility 或 Drawdown 的替代输入。
- 缺少完整覆盖、唯一 series identity、合法 return semantics、必要锚点、共同 return currency 或最小样本时显示 `—`，不把缺失解释为 0。只有 base-currency cash 的经济收益和风险天然为 0。
- `carried_cost` 和 `premium_liability` 行的 Day Change、Day Return、instrument return、chart、drawdown、Vol 与 Forward RC 均为 `N/A`；即使 payload 错误携带数值零也不得当作真实市场收益或风险展示。只有 base-currency cash / pending monetary row 可以按明确的 monetary 口径显示风险 `0`。

### 2.4 分组的六种类型

| 代码 | 含义 |
| --- | --- |
| `none` | 只在 instrument row 展示；group、subtotal 和 total 留空。`Instrument` 列仍用作组名或 `Portfolio Total` 标签，不代表该字段被聚合。 |
| `sum` | 对当前 rows 的绝对量求和；金额先统一为 base currency。 |
| `recomputed_ratio` | 用组级分子和分母重新计算，禁止平均成员百分比。 |
| `current_weight_return` | 用 `as_of_date` base-market-value 权重合成当前成员自身 total return。 |
| `current_basket_path` | 在共同 period 上先生成当前权重篮子 return path，再计算 volatility / drawdown。 |
| `portfolio_risk_contribution` | 成员都相对于同一个完整全组合 forward-risk variance；分组只加总这些贡献。 |

当前权重 instrument return 要求有当前市值的 market-valued 成员 100% 覆盖，不能把衍生品未知收益伪造成普通持仓收益。Volatility 与 risk drawdown 使用 total-NAV 权重：普通证券提供共同 period return series，衍生品、base-currency cash 和 base-currency pending monetary row 作为 0-return capital；non-base monetary exposure 仍需要兑 base currency 的 FX return。没有任何 modeled market asset 时结果 unavailable。成员 return currency 不一致且没有统一的 base-currency return series 时，结果留空，不能直接拼接各自本币收益。

### 2.5 跨页面事件估值一致性

- Overview 的 Top Holdings 对 event-valued row 显示 operational amount 和权重，但 sparkline、Unrealized P&L、1W、MTD 与 YTD 均为 `N/A`。
- Security detail 的 event-valued hero 使用 `Carrying value`，Quote、Quote Date 和 Unrealized P&L 为 `N/A`；页面不请求或展示 instrument price chart，account slice 与 open lot 也不得从 carrying basis 生成零 unrealized P&L。
- Accounts position API 必须显式返回 `carrying_value`、`fair_value`、`fair_value_coverage_status`、`valuation_basis` 和 `coverage_status`。Accounts UI 按这些字段判断事件估值，相关 Unrealized P/L 为 `N/A`，不得按 instrument type 猜测或按 `market_value - cost_basis` 得出零。

### 2.6 单表与持仓类别 contract

Holdings 只有一张可排序、可选列的表。系统视图 `Default` 与 `Return & Risk` 都保留；表内始终按后端 `holding_category` 展示以下三个固定、互斥的一级区段，即使某一区段当前为空也不消失：

| `holding_category` | 固定区段 | 内容 |
| --- | --- | --- |
| `securities` | Securities | 股票、基金、ETF 等 Registry instrument |
| `derivatives` | Derivatives | FCN、long/short Call、long/short Put；方向和生命周期属于行自身，不拆成策略区 |
| `cash_and_settlement` | Cash & Settlement | settled cash、receivable、payable 与 pending monetary rows |

`holding_category` 是系统 read-model 字段，不是用户可选的 Group By 维度，也不写入交易事实。`Group By` 由底层限定为只在 Securities 区段内部按 taxonomy、instrument type、currency 等字段建立二级分组；Derivatives 与 Cash & Settlement 不参与该分组。Taxonomy / Taxonomy Leaf 只解析普通证券，衍生品和现金固定显示 `N/A`。covered call 等策略继续由独立股票行和 short Call 行表达；行内 notes/交易 notes 承担人工关联说明。

Short-option row 不读取或分配股票持仓。`required_underlying_quantity = open_contract_quantity × contract_multiplier`，只表示合约对应的标的数量；`strike_notional = strike × required underlying`，base amount 只有在 as-of FX 可用时才发布。covered call 等策略由用户通过独立股票和期权交易表达，不属于交易或持仓类型。

Pending row 的 identity 固定为：

```text
pending:{kind}:{cash_account}:{economic_instrument}:{currency}:{settlement_date}:{pending_until_date}
```

它防止相同账户/资产/币种但不同结算边界的余额发生主键冲突。`pending_status` 目前包括 `awaiting_settlement`、`settled_awaiting_position` 和 `overdue`；本币 signed amount 与 base amount 必须同时保留，base FX 不可用时本币金额仍可展示但 base aggregation 为 unavailable。

Workspace `operational_summary` 展示 open short-option contract count、expiry buckets、option obligation strike notional 和 settlement receivable/payable/net；`operational_alerts` 返回 severity、code、message 和真实 `related_line_ids`。到期已到和逾期结算是 critical，七日内到期与 settlement FX unavailable 是 warning。API 不发布股票覆盖分类或结算方式；动态 workspace、materialized snapshot 和 instrument detail projection 对当前字段必须保持 parity。

CSV/XLSX 只导出当前视图的可见列，并始终以 `Category` 作为第一列。启用 `Group By` 时再增加 `Group` 列；该列只对 Securities 的二级分组有值，Derivatives 与 Cash & Settlement 写 `N/A`。三个固定区段都附带 subtotal，最后附带 `Portfolio Total`。价格路径、收益、未实现盈亏和回撤的不适用值导出为 `N/A`；衍生品的 Vol / Forward RC 同样导出 `N/A`，只有明确 modeled-zero 的 monetary rows 导出风险数值 `0`。

## 3. 完整字段字典

下表必须与前端 `HOLDINGS_GROUP_AGGREGATION_KIND` 一一对应。表内的机器可读标记由测试使用，增删字段时必须同时更新实现、本文和 contract tests。

<!-- holdings-column-contract:start -->
| 字段 key | UI 标签 | instrument row 口径 | group / subtotal / total | 类型 |
| --- | --- | --- | --- | --- |
| `instrument` | Instrument | canonical instrument name；现金为对应币种现金名称 | 仅作为组名、subtotal 或 total 标签 | `none` |
| `ticker` | Ticker | Registry primary identifier | 留空 | `none` |
| `instrument_type` | Instrument Type | canonical instrument type | 留空；可作为 Group By 维度 | `none` |
| `taxonomy_top` | Taxonomy | Securities 使用当前默认 planning taxonomy 的顶层节点，无 assignment 为 Unassigned；其他固定区段为 N/A | 留空；可作为 Securities 的 Group By 维度 | `none` |
| `taxonomy_leaf` | Taxonomy Leaf | Securities 使用当前默认 planning taxonomy 的叶节点，无 assignment 为 Unassigned；其他固定区段为 N/A | 留空；可作为 Securities 的 Group By 维度 | `none` |
| `currency` | Currency | instrument currency；现金为该现金币种 | 留空；可作为 Group By 维度 | `none` |
| `holding_date` | Holding Since | 当前开放头寸里最早的 holding start date；不包含已经平掉的旧头寸 | 留空，不拼接不同成员起点 | `none` |
| `quantity` | Quantity | 当前开放 quantity；short-option obligation 为负的 open contract count；现金行是 settled cash amount | 留空，不跨不同份额单位求和 | `none` |
| `cost_method` | Cost Method | 账户成本法；跨账户不一致时为 Mixed | 留空 | `none` |
| `avg_cost_book` | Avg Cost | market/event position 的 `remaining cost basis / remaining quantity`；option obligation 和 cash 不适用 | 留空，不平均成员成本 | `none` |
| `last_price` | Quote | `as_of_date` 选中的 valuation quote；event-valued asset、option obligation 和 cash 不适用 | 留空 | `none` |
| `quote_date` | Quote Date | 上述 valuation quote 的实际日期；event-valued row 必须为空 | 留空 | `none` |
| `quote_basis` | Quote Basis | 实际 market quote basis，例如 `official_nav` 或 `close`；event-valued row 无 quote，因此为空 | 留空 | `none` |
| `quote_provider` | Provider | 选中 valuation series 的 provider | 留空 | `none` |
| `quote_status` | Quote Status | valuation selection / freshness 状态 | 留空 | `none` |
| `market_value` | Position Value | market-valued position 为本币 fair value；event asset 为 carrying amount；option obligation 为负 liability；现金为 settled amount | 按 base currency 加总 | `sum` |
| `market_value_base` | Position Value Base | 上述 signed NAV amount 按 `as_of_date` FX 转为组合 base currency | 直接加总 | `sum` |
| `cost_basis` | Cost Basis | position 的本币 remaining book cost；option obligation 和 cash 不适用 | position 成本按 base currency 加总 | `sum` |
| `cost_basis_base` | Cost Basis Base | position 开放成本按 `as_of_date` FX 转为 base currency | 直接加总 | `sum` |
| `weight` | Weight | `market_value_base / portfolio NAV`；written liability 为负权重 | 加总当前 row 权重 | `sum` |
| `accounts` | Accounts | 当前持有该 instrument 的 distinct account 数 | 留空，账户集合可能重叠 | `none` |
| `open_lots` | Open Lots | 当前开放 lot 数；moving average synthetic lot 按一个开放 lot 展示 | 加总开放 lot 数 | `sum` |
| `day_change_value` | Day Change | market-valued position 使用完整 return/valuation pair；non-base cash 使用 FX；`carried_cost` / `premium_liability` 为 N/A | 完整覆盖时加总 base-currency day change；含 event row 时 N/A | `sum` |
| `day_change_pct` | Day Return | 与 Day Change 使用同一完整点对；base cash 为 0，non-base cash 为 FX return，event row 为 N/A | `sum(day change base) / sum(prior market value base)` 重算；含 event row 时 N/A | `recomputed_ratio` |
| `unrealized_value` | Unrealized P&L | market-valued position 为 `current market value - remaining open cost`；event asset/obligation 不把 carrying basis 冒充 fair-value unrealized P&L | 无非零 event exposure 时加总 base-currency unrealized P&L；否则 N/A | `sum` |
| `unrealized_pct` | Unrealized Return | market-valued position 为 `unrealized P&L / remaining open cost`；event asset/obligation 不适用 | 无非零 event exposure 时按 `sum(unrealized P&L base) / sum(non-cash open cost base)` 重算；否则 N/A | `recomputed_ratio` |
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
| `instrument_volatility_1m` | 1M Vol | 普通证券为 confirmed total-return series 的 trailing sample volatility；衍生品为 N/A，modeled-zero monetary row 为 0 | 从风险 scope 排除衍生品后，用 1M 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `instrument_volatility_3m` | 3M Vol | 同上，3M 窗口 | 从风险 scope 排除衍生品后，用 3M 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `instrument_volatility_6m` | 6M Vol | 同上，6M 窗口 | 从风险 scope 排除衍生品后，用 6M 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `instrument_volatility_1y` | 1Y Vol | 同上，1Y 窗口 | 从风险 scope 排除衍生品后，用 1Y 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `forward_risk_share` | Forward RC | eligible 普通证券相对于同一 total-portfolio variance 的 contribution share；衍生品为 N/A，modeled-zero monetary row 为 0；其他 policy-excluded rows 为 N/A | eligible 风险模型完整时加总，衍生品不进入合计；纯 modeled-zero monetary group 的行级贡献为 0；excluded exposure 仍保留在 scope disclosure | `portfolio_risk_contribution` |
| `price_chart_1m` | Chart 1M | 1M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_3m` | Chart 3M | 3M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_6m` | Chart 6M | 6M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_1y` | Chart 1Y | 1Y 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `coverage` | Coverage | valuation、NAV、FX、trend series 等覆盖诊断 | 留空；可作为 Group By 维度 | `none` |
<!-- holdings-column-contract:end -->

表内所有“可作为 Group By 维度”的说明都只适用于 Securities 区段；Derivatives 与 Cash & Settlement 始终保留为固定一级区段。

## 4. 分组计算细则

### 4.1 当前权重收益

对 group 中有当前市值的成员：

`group_return = Σ(current_market_value_base_i / group_market_value_base × instrument_total_return_i)`

覆盖不足时整个 group 留空，不能剔除缺失成员后重新归一。`Portfolio Total` 遵守同一规则。该结果表示“如果当前篮子在该历史窗口一直保持当前权重”，不是实际组合 TWR。

当前 group scope 中只要存在非零 event-valued asset 或 option obligation，Day Change、Day Return、Unrealized P&L、Unrealized Return 和 instrument return 均失败关闭为 `N/A`。Operational amount 仍参与 Position Value、Weight 和 NAV 对账。Vol 与 risk drawdown 把衍生品和本币现金作为 0-return capital；Forward RC 不把它们放入 covariance，但使用 total-NAV 权重。衍生品自身字段仍为 `N/A`。

### 4.2 当前篮子风险

普通证券先构造共同的日频 mark-to-last 路径，再用当前权重得到 group return series，不能加权平均成员风险值。衍生品与本币现金保留市值、收益固定为 0；非本币 monetary exposure 必须提供可比较的 FX return。group 与 subtotal 使用各自篮子的净市值分母，`Portfolio Total` 使用 total NAV。只有至少一个可建模市场成员并且共同路径完整时才发布 Volatility、Current DD 与 Max DD；纯本币现金/衍生品 scope 显示 unavailable，不冒充实际 0。

成员可以在共同起点之前拥有不同长度的早期历史；共同起点取各成员首个合法 period start 中最晚者。从该点开始，每个成员必须具有完全相同、内部连续且顺序一致的 `(period_start, period_end)` identity：后一段的 start 必须等于前一段的 end。任何成员在共同区间中间或尾部缺一段，或所有成员共同缺失同一段，整个 group 指标都应显示不可用，不能静默取交集后把缺口隐藏。

### 4.3 Forward RC

所有 eligible 市场成员必须来自同一个完整 Production Risk Model 和同一 leaf covariance matrix，权重为 signed base exposure / total NAV。`risk_eligible` 来自 as-of effective analytics taxonomy selection、configuration revision 与 scope policy：exact node 优先，其次最近祖先，再到 taxonomy root；`__unassigned__` 只使用自身 policy。窗口从 holdings as-of date 按自然月回看；eligible member return 的 start/end period identity 必须完全一致，strict 与 complete-case policy 都校验尾部新鲜度。group / subtotal / total 只能加总成员相对于同一 total-portfolio variance 的 risk share；不能先合成 group return 再运行 shrinkage，也不能在每个 group 内另建局部分母。`abs` mode 使用精确绝对贡献，零贡献保持为零。

Event-valued asset 和 derivative liability 不进入 covariance matrix，行级 `forward_risk_share`、contribution 与 modeled volatility 留空，状态为 `excluded`。Forward-risk summary 仍必须返回 policy/configuration versions、total NAV、`modeled_net_exposure`、`modeled_gross_exposure`、`excluded_carrying_value`、`excluded_liability`、`cash_unallocated_exposure`、coverage ratio 和 `excluded_rows`。没有 eligible risky holding、total NAV 无效、存在无法建模的非本币 monetary 或 policy-excluded 市场敞口，或者 eligible member 的 return/FX/period identity/weight/variance 不完整时，整个 forward risk 失败关闭。modeled-zero monetary row 的行级贡献可以明确为 0，但它本身不能使纯现金组合得到可观测组合风险。

## 5. 不可用与排查顺序

`—` 表示缺数据、口径不适用或严格条件未满足，不等于 0。衍生品的风险字段固定为 `N/A`；明确显示的风险 `0` 只适用于 modeled-zero monetary rows。排查顺序：

1. 数量或 Holding Since：检查 Transactions 的 trade date、账户、instrument、quantity 和历史卖出/转仓；
2. 成本或未实现盈亏：检查账户 FIFO / moving average、gross amount、费用税费、分红/资本返还分类；
3. 市值或权重：检查 valuation quote、quote basis、price scale、FX 和 snapshot freshness；
4. Return / Vol / Drawdown：检查 Registry total-return semantics、窗口锚点、历史覆盖和 risk frequency；
5. group 指标：检查每个当前成员是否 100% 覆盖、return currency 是否一致；
6. Forward RC：先检查 effective analytics taxonomy/policy、total NAV 与 excluded-row disclosure，再检查 eligible members、Production Risk Model、完整对齐收益矩阵、base-currency return、total-portfolio variance 和 workspace status。
