# Portfolio Daily Exact Methodology

- 状态：Phase 3B 首个 producer 的实现契约
- methodology version：`portfolio-daily.exact.v1`
- input schema version：`portfolio-daily-input.v1`
- output schema version：`portfolio-daily-output.v1`

## 1. 定位与边界

Portfolio Daily 是 Workbench 的组合账本、日终估值、管理口径绩效与贡献主数据源。它不是基金会计总账、
托管估值表、税务申报引擎或 GIPS composite presentation；对外正式披露仍需独立复核。这个边界不降低
内部计算要求：每个发布数字必须由 sealed manifest 在固定方法版本下精确复算，不能依赖 current fact、
binary float、请求时临时计算或人工不可见的 rounding plug。

首版支持的经济事实由当前 revisioned transaction schema 明确定义。某类资产需要但输入中不存在的
乘数、应计、现金替代、税务或公司行动条款时，该资产/日期返回结构化 `Unavailable`；不得使用行业
经验值、前值、零值或 `1.0` 猜测。

## 2. 时间、日界与事件顺序

- 每个 portfolio 固定一个 IANA valuation timezone、EOD valuation cut-off、valuation calendar 与 freshness
  policy。run 分别保存 requested/effective as-of、当地估值截点规则，以及本次 `REPEATABLE READ` 输入快照的
  UTC knowledge cut-off；三者不能混用。后来才发布或更正、但经济日期属于 effective as-of 的官方基金 NAV，
  可以进入后来发起的新 run，并由新 manifest 明确记录其 revision/ingested time；不能因把 EOD 当作知识截点而
  永久排除，也不能反向改写已经 sealed 的旧 run。
- requested/effective as-of 可以是历史市场日，但不能晚于数据库 `transaction_timestamp()` 按该组合
  valuation timezone 换算出的本地日期。未来日 carry-forward 不构成估值事实，而且一旦成为 current
  publication，会被防时间倒退规则保护，反而阻塞随后正确的较早日期发布。
- manifest capture 使用单一 `REPEATABLE READ` snapshot。计算器只读 manifest 中的 revision/snapshot。
- 同一天的确定性事件顺序为：BOD confirmed corporate action、entitlement、其他 recognition/accrual、按
  `(trade_at, recorded_at, transaction_id, revision_id)` 排序的 trade event、internal transfer、EOD settlement、
  EOD valuation。dividend、coupon、return of capital 和 dividend reinvestment 属于 entitlement；interest、
  maturity 与独立 fee/tax 属于 recognition；FX conversion 属于 trade event，不能因内部实现也使用
  recognition posting 而提前到交易之前。同一事件的 settlement 始终在其 recognition/trade 之后。
- position 与 book cost 使用 trade-date accounting；cash 使用 settlement-date accounting。交易日至结算日之间，
  应收/应付结算款作为独立资产或负债进入 NAV，防止未结算买卖制造虚假收益。
- 没有可信日内现金流时点时，外部流入固定为期初、外部流出固定为期末；不宣称 true intraday TWR，
  也不按数据是否方便而切换 Modified Dietz。

## 3. Decimal 与事实验证

- 账本金额、数量、现金流和 monetary bridge 的有限十进制加、减、乘使用 coefficient/exponent 精确算术，
  不读取进程全局 Decimal context。收益率、贡献率、wealth、drawdown、统计量、XIRR 等 method domain 的
  加、减、乘、除全部在 precision 50、`ROUND_HALF_EVEN` 且 trap
  `FloatOperation / InvalidOperation / DivisionByZero / Overflow` 的 local Decimal context 中逐步执行；
  每个递归步骤都按同一方法域落位，不能积累无界字符串后在 API 边界一次性截断。分摊除法的最后一行
  显式承接方法学余数，使 exact monetary total 与 method50 ratio total 同时闭合。
- canonical FX 的 direct leg 原样保留 adopted quote；inverse leg 仅对 `1 / quoted_rate` 执行上述
  precision-50 HALF_EVEN 除法，并显式保存单腿 inversion residual。direct/inverse/cross path 在得到各腿
  effective rate 后使用 coefficient/exponent 精确乘积，最终 path rate 不再二次截到 50 位；因此 resolved
  path 的 composition residual 必须严格为 0，任何非零值均视为证据损坏并 fail closed。sealed input 不能只因
  `effective_rate * quoted_rate - 1` 与所报 residual 自洽就接受 inverse leg；应用边界与数据库约束都必须从
  adopted quote 重放版本化倒数。数据库重放 inverse FX、TWR、drawdown 或其他 method50 除法时，必须调用
  `calculation_registry.divide_significant_half_even`；该函数使用十进制移位、整数商和余数完成 HALF_EVEN 判定，
  禁止依赖 PostgreSQL `/` 的隐式除法精度后再做 significant rounding。
- quantity/price 接收边界为 12 位、amount/fee/tax 为 8 位、FX rate 为 18 位；禁止 float、NaN、Infinity
  与负零。内部 lot、现金、成本、估值和损益不做隐式 quantize；收益率、贡献和递归财富链
  只能执行方法版本明示规定的 method50 舍入。
- quantity、price、gross amount 同时存在时，使用 manifest 中明确的 quote unit、contract multiplier 与 price
  factor 验证：`quantize(quantity * price * contract_multiplier * price_factor, 8) == gross_amount`。v1 对
  fund/ETF/equity 固定并记录 methodology-owned `per_unit / multiplier=1 / price_factor=1`；bond 或其他合约型
  资产必须由 snapshot 提供 face/quote unit/multiplier/factor，缺失时 fail closed，不能把百分比报价当每单位价格。
- currency 必须与 account、instrument snapshot 及 quote series 一致。任何转换都必须列出精确 FX path；
  不允许把 currency mismatch 当成 1:1。
- published NUMERIC 与 canonical decimal string 只在 output boundary 量化；字段 scale 属于 output schema，
  不是 UI 格式。

## 4. 账本状态与符号

每个日期至少维护以下互相独立的 Decimal 状态：

- account/currency settled cash；
- transaction/currency pending settlement receivable/payable；
- account/instrument position lots、quantity、local-currency book cost 与按 acquisition/trade-date canonical FX
  固定的 historical base-currency book cost；
- income/fee/tax accrual 与已结算金额；
- external flow、internal transfer 与 performance-neutral movement；
- realized P&L、unrealized P&L、income、fee、tax、local-price effect 与 FX effect。

资产与收入为正，负债、费用和税费为负；API 可另提供正数展示字段，但 canonical ledger 不改变符号。
外部流入/流出分别以非负 magnitude 保存，禁止用一个带符号字段混合两者。

## 5. 交易方法

### 5.1 Opening balance 与外部现金

- opening cash 增加 settled cash；opening position 创建带 acquisition date 的 lot，gross amount 是其 book cost。
- opening balance 只允许建立首个 performance anchor；已有可信前日 NAV 后再次出现 opening balance 是数据错误，
  不能伪装成收益或普通 deposit。
- 首个可计量日期、或前一日 closing NAV 不可计量后的恢复日期，没有可信 opening boundary；该日
  `opening_nav = null`，closing NAV 仍可完整计量并建立 TWR anchor。禁止用 0 伪造期初 NAV，也不计算该日
  economic/book P&L。
- deposit 在 effective settlement date 增加 cash 并记录 external flow in；withdrawal 减少 cash 并记录
  external flow out。外部实物申赎在事实模型明确支持前不可借 internal transfer 表达。

### 5.2 Buy、sell 与 maturity

- buy 在 trade date 增加 quantity；lot cost 为 `gross + fee + tax`。同额 settlement payable 进入 NAV，
  settlement date 再由 cash 清偿，不产生第二次 P&L。
- sell 在 trade date 按 account 的明确 cost method 释放 quantity 与 book cost；net receivable 为
  `gross - fee - tax`，realized P&L 为 net proceeds 减 released cost。settlement 只搬移 receivable 与 cash。
- FIFO 按 `(acquisition_date, source_trade_at, lot_id)`；average cost 在每次 acquisition 后用未量化总成本除以
  总数量。partial disposal 不量化 unit cost，最后一笔关闭 lot 时释放精确剩余成本，避免累积尾差。
- 多币种 lot 同时维护 local cost 与 historical base cost；处置必须由同一次 FIFO/average-cost 匹配同时释放
  两者，禁止估值层再复制一套 lot matching，也禁止用处置日或报告日 FX 重译历史成本。事件日 FX 缺失时，
  local ledger 继续闭合，但 base-cost、base realized/unrealized P&L 明确 unavailable。
- maturity redemption 与 sell 相同地释放 lot，但保留独立 event type；其 instrument-lifecycle recognition
  先于同日自由交易，数量、proceeds 或条款不足时不可假定 par。
- short、margin、borrow fee 与负持仓在显式扩展方法前不支持；超卖立即 fail closed。

### 5.3 Income、return of capital、fee 与 tax

- dividend/coupon 在 entitlement date 确认 gross income 与 receivable；若输入明确没有 entitlement date，
  使用 recorded trade date，同时写入 `recognition_date_recorded` reliability reason，不伪造应计日期。
- interest 按 recorded effective date 确认。dividend reinvestment 同时确认 income 与等额 acquisition，
  不制造外部 flow。
- return of capital entitlement 先按 cost method 降低当日交易前全部 open cost；随后同日部分处置释放降低后的
  成本，同日全额处置也不能先删除 position 再丢失 entitlement。超过剩余成本的部分计入 realized gain，
  book cost 不得为负。
- trade 附带 fee/tax 按 acquisition/disposal 规则处理；独立 fee/tax 是当日 expense，不倒灌到无关 lot。
- income、fee、tax 的 recognition 与 settlement 分开；现金未到账不影响已经确认的 NAV/P&L。

### 5.4 Internal transfer 与 FX conversion

- internal cash/position transfer 必须以同一 transfer group 成对出现，source/destination 互为对手方，且日期、
  currency、instrument、quantity/amount 完全一致；不完整或冲突的 pair 整组失败。
- position transfer 原样携带 lot id、acquisition date、quantity 与 cost，不实现出售/重买，也不产生 P&L。
- FX conversion 同时减少 source-currency cash、增加 target-currency cash；`counter_amount / gross_amount`
  必须与事实中的 direction-aware FX rate 契约一致。它是内部 movement，不是 external flow；base-currency
  economic gain/loss 由 conversion terms 与当日 canonical FX 的差异显式计算。

## 6. Corporate action

- 只应用 manifest 中 status=`confirmed` 且在 effective date 生效的事件；`detected` 只产生数据质量状态。
- split/reverse split 在 BOD 按 exact `new_units / old_units` 调整每个 lot quantity，总 book cost 不变。
- `quantity_rounding=exact` 可直接处理。任何需要 fractional rounding/cash-in-lieu 的事件，必须有明确 rounding
  precision、fractional quantity、cash price/proceeds 与 source evidence；当前事实不足时整项 fail closed，
  不能静默丢弃零股或用当日收盘价代替 cash-in-lieu 条款。
- dividend adjustment 不能替代 position ledger 中的 share event；valuation 使用 raw eligible quote，
  total-return comparison 才使用明确的 total-return series。

## 7. Valuation 与 FX

- EOD NAV = settled cash + pending settlements + income accruals - expense accruals + 可计量 position market value。
- position market value 使用 exact quantity、raw valuation quote、instrument multiplier/accrual contract；
  adjusted close 不用于持仓市值。
- base currency identity rate 恒为 Decimal 1。非 base exposure 使用 manifest 固定的 direct/inverse/cross legs；
  每条 leg 必须为正、currency continuity 完整且 revision/status/observed time 符合 policy。
- quote/FX carry-forward 可以生成带明确 lineage 的管理估值，但该日期不是新的 reliable valuation endpoint：
  daily TWR 为 null，不能伪装成 0。若 gap 中间日期没有 external flow，保留上一个 fresh anchor，下一次完整
  fresh endpoint 计算一个明确标注起止日期的多日子期收益；中间日期任何 external flow 或 fresh endpoint 的 BOD
  inflow 都因缺少流量前可靠边界而 broken。fresh endpoint 的 EOD outflow 发生在该可靠估值之后，可在
  adjusted ending 中精确中性化，不打断窗口。stale 值不能伪装成 current，也不能跨真正的 broken boundary 链接
  TWR。
- 对无法计量的资产不补零。NAV coverage 为 `partial` 或 `unavailable`，并列出结构化 reason code、instrument
  与缺失 dependency。
- 外部流量的本币金额已知、但 base FX 缺失时，base `external_flow_in/out` 保持 null，并单独发布 flow
  coverage/reason；不得补 0，也不得因此丢弃整份 run。该日 TWR 与 contribution fail closed，local ledger 和
  其他仍可计量的 unavailable/partial 证据继续发布。
- `measurement coverage` 与 `valuation freshness` 是正交维度：所有组件均能以允许的 carry 值计量时，NAV
  measurement coverage 仍可为 complete，但 endpoint status 必须是 carry/stale；缺少任一资产价格、合约因子或
  FX 时，canonical closing NAV 为 null，不能把已知组件之和命名为 NAV。后者可另行展示为 known value。

## 8. P&L、贡献与闭合

Portfolio 当日独立经济恒等式为：

```text
economic_pnl_t = MVE_t + CF_out,t - MVB_t - CF_in,t
```

book realized/unrealized、income、fee、tax、local-price 与 FX 是对该 economic P&L 的解释，不得改变总额。
日频 book bridge 使用 daily realized P&L、ending unrealized P&L 相对 beginning unrealized P&L 的变化、gross
income、expensed fee/tax 以及 cash/pending/accrual FX effect；字段必须明确标注 `daily`、`ending` 或 `change`，
不得以含义不明的 `unrealized_pnl` 混用累计余额与当日变化。capitalized acquisition costs 进入 lot cost，
disposal costs 进入 realized，二者只作为解释列示一次，不能再次从 economic P&L 扣除。

多币种 book bridge 固定采用 historical-base-cost 口径：

```text
ending_unrealized_base = ending_market_value_base - ending_historical_base_cost
unrealized_change_base = ending_unrealized_base - beginning_unrealized_base
economic_pnl_base
  = realized_pnl_daily_base
  + unrealized_change_base
  + gross_income_daily_base
  - expensed_fee_daily_base
  - expensed_tax_daily_base
  + monetary_balance_fx_effect_base
  + fx_conversion_effect_base
```

`monetary_balance_fx_effect` 覆盖 settled cash、trade pending 与 income/expense accrual 的期初净货币敞口；在
当前 BOD-inflow/EOD-outflow 约定下，非基础币流入从期初起承担当日 FX，流出在期末前仍承担当日 FX。日内
交易、income/expense recognition 与 settlement 使用当日 manifest canonical FX；FX conversion 的两币金额按
同一时点 canonical FX 产生的差额单列。若所需事件日或端点 FX 缺失，base bridge unavailable，不能以 current
FX 翻译历史成本或用 residual plug 补齐。
每个 account/instrument/currency/taxonomy group 使用与 portfolio TWR 完全相同的
`(return_period_start_date, return_period_end_date]`、beginning/ending value 与 external/internal movement 符号契约；
internal movements 在 source/out 与 destination/in 抵消，所以所有 group P&L 精确汇总到 portfolio。

group boundary 与 movement 采用以下固定归属，不按报表请求临时改变：

- `account` 按 component 所属账户；`currency` 按 component 原币；`instrument` 将 position 归入 instrument，
  settled cash、pending 与 accrual 归入显式 `__cash__` 系统组；
- `taxonomy` 只使用 sealed config 中的 default taxonomy snapshot。instrument/account/cash_bucket primary scope
  分别按对应 subject 归属；instrument scope 的货币 component 进入 `__cash__`，cash_bucket scope 的非现金
  position 进入 `__unclassified__`，其他缺少有效 assignment 的 component 也进入 `__unclassified__`。系统组
  是真实完整切片的一部分，不是 unavailable 或零值占位；
- opening group value 是 `return_period_start_date` reliable anchor 的 independently-valued closing component
  同口径聚合，closing group value 是 active endpoint 的 independently-valued component 聚合；不能在多日 gap 中
  偷换为前一自然日 carry boundary。窗口内每个日期的 movement 全部累积：buy 将 settlement cash group 按 exact
  local consideration × 事件日 canonical FX 流出、position group 等额流入；sell/maturity 反向处理 net proceeds。
  income/return of capital 从 instrument 流出到 cash，instrument-tagged fee/tax 从 cash 流出到 instrument，因此
  货币 component 本身不冒充投资收益；
- effective-date cash/position transfer 位于 EOD valuation 前，逐日使用该 effective date 前一 valuation boundary
  的 exact base value 成对转移，使转入组承担当日后续价格/FX 变化；多日 return window 不得把所有 transfer
  压到 anchor 或 endpoint 估值。settlement 只在同一 cash/account/currency group 内搬移，不再生成第二笔
  internal movement；
- FX conversion 用 source currency 当日 canonical base value作为 source/out 与 target/in 的相同 magnitude；
  target currency 的 closing value 与该等额 movement 的差异就是 conversion terms 及当日货币重估共同形成的
  经济损益。任何一条 required FX、position transfer opening value 或配对 leg 缺失时，只把受影响的
  date/axis 标为 typed unavailable，不能估计 movement；
- 每组在完整 return window 独立满足
  `MVE_g + CF_out_g + IF_out_g = MVB_g + CF_in_g + IF_in_g + economic_pnl_g`，exact residual 必须为 0；
  同一 axis 的 opening、closing、external flow、economic P&L 分别精确汇总到 portfolio，且
  `sum(IF_in) = sum(IF_out)`。这是 group P&L 的定义与闭合证明，不允许用 portfolio residual 回填任一组。

subperiod 贡献只在 TWR denominator 有效且该 group 的完整窗口 P&L 可计量时发布：

```text
contribution_g,t = economic_pnl_g,(anchor,t] / (MVB_anchor + CF_in,(anchor,t])
sum_g contribution_g,t = subperiod_twr_t
```

每组先独立执行 precision-50 HALF_EVEN 除法。`contribution_method50` 是有界方法值；
`contribution_effective = contribution_method50 + contribution_division_adjustment_exact` 才是日频归因消费值。
多个已舍入商之和与 portfolio 商存在最后位差异时，选择
`max(abs(economic_pnl))`、并在并列时取升序最小 `group_key` 的唯一组承接差额，同时保存
`contribution_division_adjustment_exact`；这只是可审计的除法闭合分配，不改变 group economic P&L。exact
monetary evidence、method50 contribution、division adjustment、18 位 published contribution 与逐行
balanced-rounding adjustment 均持久化，
所以贡献闭合不存在隐藏 residual。

不同 axis 是同一 portfolio result 的独立完整切片，不能把 axis 之间相加。taxonomy 未分配值进入显式
`unclassified` group；ETF rotation operating profile 可以不做 allocation policy/taxonomy drift，但账本、估值、
performance 与基础 instrument/account/currency 切片不例外。

## 9. TWR、broken boundary 与 re-anchor

```text
return_numerator_t = (MVE_t + CF_out,t) - (MVB_t + CF_in,t)
r_t = return_numerator_t / (MVB_t + CF_in,t)
```

该形式与 ratio-minus-one 在实数域代数等价，但 v1 把唯一一次 precision-50 HALF_EVEN 除法固定在
`return_numerator / denominator`；portfolio TWR 与所有 group contribution 因此共享同一分子闭合和同一
denominator，不允许两个代数变形各自舍入后再用 residual 调平。

- denominator `<= 0`、现金流边界使用 stale/carry-forward/incomplete valuation、输入闭合失败时，
  当日及其链接边界为 `broken`，不返回近似收益。
- 没有 external flow 的非 fresh 日期只标记 `no_new_valuation`：保留 last reliable anchor/wealth/peak，
  daily TWR 与 `return_period_*` 均为 null；下一 fresh endpoint 才以该 anchor NAV 为 beginning NAV 固化完整
  多日子期的起止日期与日数。月度基金 NAV、周末与
  停牌因此不会被伪造成每日 0 收益，也不会因为正常发布频率而永远 re-anchor。
- BOD inflow / EOD outflow 是严格现金流时点契约，不得跨多日 gap 倒推。gap 中间非 fresh 日期发生任何
  external flow，或相隔多日的 fresh endpoint 发生 BOD inflow，系统都缺少流量发生前的 reliable boundary，
  必须 `broken + gap_external_flow`。fresh endpoint 的 EOD outflow 位于可靠 endpoint valuation 之后，允许精确
  进入 `E_t = MVE_t + CF_out,t`；不能把其他流量放到整个 gap 的期初/期末，也不能暗中改用 Modified Dietz。
  周末或停牌本身不构成例外。
- broken 后第一个完整日期只建立 fresh anchor：daily TWR 为 null，cumulative TWR/drawdown 从 0 重新开始；
  再下一个完整日期才恢复计算。
- 每个可计算 endpoint 严格按以下 method50 顺序执行：`B_t = exact(MVB_t + CF_in,t)`、
  `E_t = exact(MVE_t + CF_out,t)`、`N_t = exact(E_t - B_t)`、
  `r_t = divide50_HALF_EVEN(N_t, B_t)`、`F_t = add50_HALF_EVEN(1, r_t)`、
  `W_t = multiply50_HALF_EVEN(W_prev, F_t)`、`C_t = subtract50_HALF_EVEN(W_t, 1)`、
  `P_t = max(P_prev, W_t)`、
  `DD_t = divide50_HALF_EVEN(subtract50_HALF_EVEN(W_t, P_t), P_t)`。共同分子形式防止
  ratio-minus-one 在极小收益/回撤下发生 cancellation，并让 portfolio return 与
  contribution 共用同一个分母口径。所有 method 加、减、乘、除都在独立的 precision-50
  HALF_EVEN context 中完成，不依赖进程 ambient Decimal context。
- cumulative wealth 是 method50 subperiod factor 的递归乘积，不是每日 18 位 published 收益率的乘积；
  每步保留 `wealth_chain_rounding_adjustment_exact = W_t - exact(W_prev × F_t)`。链不跨 broken window，
  也不使用 `1e-9` 或其他 binary-float tolerance。`cumulative = method50(wealth - 1)` 与 drawdown
  必须逐步闭合；方法舍入是明示契约，不是根据历史长度临时 rebase。每个 endpoint 只持久化当期
  method state 与当步 adjustment，存储随日期线性增长，不保留随链长扩张的十进制前缀。
- `subperiod_twr_method50`、`cumulative_twr_method50`、`wealth_index_method50`、
  `peak_wealth_index_method50` 与 `drawdown_method50` 使用有界 PostgreSQL `NUMERIC` 方法域，并限制为最多
  50 个有效数字；逻辑域为 `NUMERIC(132,100)`，即最多 32 个整数位与 100 个小数位；方法舍入/闭合
  证据逻辑域为 `NUMERIC(232,200)`，即同样保留 32 个整数位并容纳两个 method operand 相减形成的最多
  200 个小数位。物理列使用无 typmod 的 `NUMERIC`，由 CHECK 实现上述逻辑域，防止 PostgreSQL 在 CHECK
  前按 typmod 静默舍入第 101/201 位。scale 100 定义的是显式工程指数域：它可在首位低至 `1E-50`
  时仍保留 50 个方法有效数字，但不声称 `1E-50` 是天然具有金融意义的阈值，也不是“小数位越长越好”。
  超出整数位、小数位或 50 个有效数字任一边界都 fail closed；这是显式方法域，不宣称覆盖
  任意数学实数。多余尾随零不算新增数值精度，并在 canonical decimal 序列化时移除。禁止以随历史增长的无界
  plain-decimal `TEXT` 前缀代替方法精度，也禁止 scientific notation、NaN、Infinity 和 `-0`。
  18 位 published return/drawdown 必须从 method50 使用 HALF_EVEN 导出，并在 builder 发布前校验
  method/published/rounding evidence 三者闭合。

### 9.1 Published multi-period reporting

Performance 页面只能消费同一个 fenced `CurrentPortfolioDailyPublication`；权威 consolidated report 在一次
read 中同时读取 snapshot 与 contribution output，不能由前端并行拼接多个可能跨 current pointer 的响应。
Performance router 只保留 `GET /{portfolio_id}/performance/report` 与
`GET /{portfolio_id}/snapshots/daily`；旧 performance/calendar/contribution 分拆端点不再提供。consolidated
builder 只构建一次 return calendar，并把同一组 bucket 复用于 statistics 与 attribution calendar。

- selected range 只链接 `active` subperiod，跳过 `no_new_valuation`；任何 broken、非连续 return period 或
  range 内多条 re-anchor chain 都 typed unavailable。返回 requested rows 与 `effective_return_start/end`，不会
  把 publication inception chain 冒充选定区间。
- range wealth 从 1 重建；`W_t = multiply50_HALF_EVEN(W_prev, add50_HALF_EVEN(1, r_t))`，
  peak 与 drawdown 使用上述共同分子公式重算，禁止复用
  inception drawdown。Calendar 把完整 subperiod 归入其 fresh endpoint 所在月/周，桶的 effective start 使用首个
  endpoint 的上一 reliable anchor；跨周末、跨月的 sparse valuation subperiod 不按天估分、也不因此拒绝。每桶显式
  标记 `complete / partial / unavailable`：requested range 覆盖完整 calendar bounds 只是必要条件；ready return 的
  `effective_return_start` 还必须不晚于 bucket 首日前一日 EOD；末个 fresh return endpoint 必须到达 bucket 末日，
  或由保持同一可靠 anchor/wealth chain 的 `no_new_valuation` calendar carry 延续到末日，才可标记 complete。
  首次 anchor 或 re-anchor 落在 bucket 中间时，该桶为 partial 并从 risk statistics 排除。按
  endpoint 归属的稀疏 subperiod 可以早于所需 opening boundary 开始；只要覆盖 opening/closing 两端，就不因跨周、
  跨月而降级。首尾不完整但 return ready 为 partial；return 不可计算为 unavailable。
- elapsed days `< 365` 不年化。达到 365 天时按 Actual/365：
  `annualized_TWR = subtract50(power50(W_range, divide50(365, elapsed_days)), 1)`，其中 `W_range` 直接取
  selected-range method50 replay 的最终 wealth；禁止从已舍入的 cumulative TWR 反加 1 重建 wealth，否则
  极小正 wealth 可能被 cancellation 错判为 0。保留 method50 Decimal、18 位 published value与显式
  rounding adjustment。
- risk statistics 使用调用方明确选择的 monthly/weekly calendar returns，仅纳入 `coverage_state=complete` 且
  return ready 的完整桶，partial/unavailable 桶计数并排除；至少 2 个完整观测。monthly periods/year 固定 12，
  weekly 固定 52；均值使用 Decimal division50；volatility 用 sample variance
  `Σ(r-mean)^2/(N-1)`，target-zero downside deviation 使用 `Σ(min(r,0)^2)/N`。年化方差再乘 periods/year，
  其中 method-domain 的求和、差、平方和年化乘法均逐步使用 method50，再只做一次 Decimal precision-50
  HALF_EVEN square root，禁止把两个分别近似的平方根相乘制造虚假精度。方法版本分别固定 calendar
  frequency、periods/year 与 denominator policy；每项发布 method50/published/rounding
  evidence，任何金融统计都不得转 binary float。
- group multi-period contribution 使用 Frongello forward linking：
  `L_g,t = add50(multiply50(L_g,t-1, 1+r_t), contribution_effective_g,t)`。该有界 forward recurrence
  产生 `linked_contribution_method50`；所有 group raw linked value 的 exact sum 与 range TWR method50 的差额
  单独保存为 `linking_adjustment_exact`，并唯一分配给 `max(abs(linked_contribution_method50))`、并列时
  升序最小 `group_key`。`linked_contribution_effective = linked_contribution_method50 + linking_adjustment_exact`，
  summary 同时发布 adjustment total，effective total 必须逐位等于 range TWR method50。每个 date/axis 的
  `Σ_g contribution_effective_g,t = r_t` 也必须逐位相等；每个 group 相邻 active period 的 prior closing NAV 与 next opening NAV
  也必须逐位相等（缺行视为 0），防止多个 boundary break 相互抵消后伪装成期间闭合。先验证完整 axis，再应用
  group filter，不使用 epsilon。Performance 的期间 opening/closing/flow/P&L 只来自同一 `axis=portfolio` exact
  contribution bridge；不得从 active snapshot 的已发布金额重复拼接。
- XIRR 采用 investor sign：opening NAV 与 external inflow 为负现金流，external outflow 与 ending NAV 为正；
  日期为 Actual/365。令 `q=(1+r)^(-1/365)`，则 XNPV 是整数日指数多项式 `Σ CF_d q^d`。v1 仅在非零现金流
  符号变化恰为 1、从而可证明唯一正根时运行固定上限 256 次 Decimal bisection（仅 exact root 或 Decimal lattice
  不再可分时提前终止）；固定 bracket expansion、无 epsilon，
  并发布 rate method50/published/rounding 与 method-q 上精确重算的 XNPV residual。无根、无法 bracket 或多重符号变化均
  typed unavailable，禁止回退旧 float solver。
  `<365` 天仍可返回数学上可计算的 rate 与 residual，但 `annualized_headline_eligible=false` 并带稳定 reason code，
  只能作为 supplemental diagnostic；达到 365 天后才可作为年化 headline。

## 10. Publication rounding 与 deterministic hash

- exact rollup 的 component total、parent residual 以及 publication rollup 的已量化 component total 都使用
  coefficient/exponent 精确求和与相减；即使中间值超过 50 个有效位也不得先截断再判断 closure。
- 每个 aggregate 先从 exact values 独立计算并 HALF_EVEN 量化。逐行初始值也独立 HALF_EVEN。
- 多行 rounding residual 使用 deterministic balanced rounding：按 exact discarded remainder、再按 stable natural key
  排序，逐 quantum 分配；普通 child row 最多调整一个 quantum，并显式保存 `rounding_adjustment`。不得把残差
  隐藏在任意一行或使用遍历顺序。无法在该约束下平衡时 run 失败。
- 多组件会计等式的 bridge adjustment 与 child-row balancing 是两类证据。前者等于各字段 HALF_EVEN 后的
  确定性舍入差，不改变任何 exact 金额，按等式项数设置严格上限：NAV 4 quantum、NAV/P&L bridge 3 quantum、
  book-P&L component bridge 5 quantum、portfolio contribution bridge 4 quantum；包含 7 个分别 balanced 的
  monetary terms 的 group contribution bridge 上限为 11 quantum。group 每个 exact monetary term、published
  value 与自身不超过 1 quantum 的 balancing adjustment 分列保存。bridge 可以超过一个 quantum，必须单独
  保存，不能被误称为 exact residual；相应 exact residual 仍必须严格等于 0。
- lot disposition 的 published proceeds 与 published allocated cost 先按日期整体 balanced rounding；published
  realized P&L 固定为二者之差，并保存其相对 `HALF_EVEN(exact realized P&L)` 的显式 adjustment（上限 3
  quantum）。组合日级 realized P&L 消费该 child roll-up，禁止独立舍入后与 disposition 明细不相等。
- 平衡后 holding/group/taxonomy/cash/P&L/contribution 的发布行必须与 aggregate 逐位相等；exact residual 与
  publication residual 都记录在 closure evidence 中。
- canonical output hash 只覆盖按 schema 固定顺序排列的财务字段、coverage/reason 与显式 rounding adjustment；
  不包含 run id、worker id、fencing token、created timestamp 等运行元数据。
- 每个 attempt 的 output 主键包含 `(run_id, output_fencing_token, natural key)`；publication 绑定最终 token，
  reader 必须同时过滤 run 与 token。

## 11. Fail-closed reason taxonomy

至少固定以下机器可读类别，具体 code 只能在版本化枚举中增加：

- `input_contract_*`：事实 scale/shape/currency/multiplier/配对错误；
- `ledger_*`：超卖、负成本、lot/transfer/cash 不闭合；
- `market_data_*`：quote 缺失、stale、carry-forward、revision/status 不合格；
- `fx_*`：path 缺失、currency continuity、rate 或 freshness 错误；
- `corporate_action_*`：未确认、条款不足、fractional treatment 不明确；
- `performance_*`：非正 denominator、broken boundary、anchor 不完整；
- `closure_*`：NAV、P&L、group、contribution 或 publication rounding 不闭合；
- `numeric_*`：非 Decimal、非有限值、precision/NUMERIC overflow。

错误不得只保存自由文本。run/job 保存稳定 reason code 和受限 context；诊断文本不参与结果 hash。

## 12. 方法变更纪律

修改事件时点、成本法、corporate-action treatment、quote/FX policy、coverage、rounding、输出字段或 hash ordering
都必须提升 methodology/input/output version 中相应版本，并创建新 run/publication；不得原地重写已发布结果。
性能优化只能在证明 manifest dependency 完整、golden result/hash 相同后采用。首版默认 full rebuild；suffix
计算只有在精确引用 prior publication、覆盖新增 observation window 与通过同结果证明后才能启用。
