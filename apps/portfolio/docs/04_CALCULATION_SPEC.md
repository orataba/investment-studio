# PMS 正式版计算口径规格

更新时间：`2026-05-03`
关联文档：

- [`01_PMS_REFERENCE_BASELINE.md`](./01_PMS_REFERENCE_BASELINE.md)
- [`02_PRODUCT_PRD.md`](./02_PRODUCT_PRD.md)
- [`03_DOMAIN_MODEL.md`](./03_DOMAIN_MODEL.md)

## 1. 文档目标

这份文档用于锁定正式版 PMS 的核心计算口径。

目标是确保：

- 同一个指标在 CLI / API / UI / export 中口径一致；
- 账本、绩效、风控、风险预算之间的分母和权重语义不冲突；
- 后续实现不会因为“看起来差不多”的公式导致结果漂移。

除非某个接口明确声明不同口径，否则本文档中的定义视为默认 canonical 口径。

### 1.1 参考继承原则

这份规格中的默认继承关系如下：

- **PP 负责**：
  - TTWROR
  - IRR / MWROR
  - Statement of Assets 风格的 purchase value / cost basis
  - open / closed trades 的 lot matching 逻辑
- **本项目扩展负责**：
  - benchmark-relative analytics
  - risk system
  - target risk budget gap
  - scenario P&L

换句话说：

- 涉及 `持仓、成本、交易匹配、收益率` 的 canonical 口径，尽量先向 PP 靠拢；
- 涉及 `风控、风险预算、buy-side review` 的部分，再由本项目扩展。

### 1.2 事实底座与派生原则

正式版计算层按三类输入理解：

#### 事实输入

- `Transactions`
- `Quotes`
- `FX`
- `Benchmark series`
- `Corporate Actions / Events`

#### 必要配置输入

- `Portfolio / Account / Instrument metadata`
- `Benchmark definition / portfolio benchmark assignment`
- `Portfolio taxonomies / target sets / alert-rule config`

#### 派生结果

- `Positions`
- `Lots`
- `Trades`
- `Snapshots`
- `Performance`
- `Risk`
- `Review`

规则：

- `Transactions` 是原始账本；
- `Trades` 是 lot matching 后的分析视图；
- `Quotes`、`FX`、`benchmark series` 是共享市场数据，不属于单个组合私有；
- `Snapshots` 是由 Analytics 从事实层生成的派生产物；
- 页面展示不得绕过这些输入直接手填结果字段。

## 2. 总体约定

### 2.1 估值时点

正式版默认使用 **end-of-day valuation**：

- 当日价格、FX、benchmark level 都视为当日收盘或该日最终可用估值；
- 当日 `MVB` 等于上一估值日的 `MVE`；
- 当日 `MVE` 为当日收盘后的组合总市值与现金合计。

### 2.1.1 全球 EOD 规则

全球多资产组合的 daily snapshot 采用以下 canonical 规则：

- 每个 `Portfolio` 必须定义 `valuation_timezone` 和 `valuation_cutoff_policy`；
- 组合日度结果按 `as_of_date` 归档，`as_of_date` 表示**市场日**，不是实际计算发生的墙上时间；
- 单个资产优先使用其本地市场在该 `as_of_date` 的最新官方收盘价或该日最终可用估值；
- 组合绝对口径快照只有在该 `as_of_date` 所需市场和 FX 数据满足覆盖率阈值后，才能标记为 `complete`；
- benchmark 相关区块的 `complete / partial / unavailable` 由 benchmark coverage 单独决定，不反向阻塞绝对口径 snapshot；
- 前端默认展示 `latest complete as_of_date`，而不是当前本地时钟下尚未收齐数据的“今天”。

示例：

- 上海团队在 `2026-04-15` 早晨拿到美国市场 `2026-04-14` 的收盘数据后，该结果归属于 `as_of_date = 2026-04-14`，而不是 `2026-04-15`。

### 2.1.2 FX 与 benchmark 对齐规则

在同一个 `as_of_date` 的快照中，若需要计算 benchmark-relative 结果：

- FX 必须使用同一 provider / cut 的 EOD 数据；
- benchmark 必须对齐到同一个 `as_of_date`；
- 系统不得把 `T` 日股票收盘与 `T+1` 日 FX 或 benchmark 静默混用。

### 2.1.3 Valuation Basis vs Total-Return Basis

共享层允许同一资产同时维护多种 quote basis，例如：

- `fund`: `official_nav` 与 `total_return_nav`
- `equity`: `close` 与 `adjusted_close`

这里必须区分两种用途：

- `valuation` role：服务组合 statement、持仓市值、NAV、ledger-driven performance
- `total_return` / `chart` role：服务 research target solve、资产风险序列与图表

规范如下：

- 组合账面估值不得静默切到 total-return basis；若分红或派息已作为交易/现金流入账，再用复权价会造成双算；
- fund 的 research/risk 序列应优先使用 `total_return_nav`，只有缺失时才回退到 `official_nav`；
- equity 的 research/risk 序列应优先使用 `adjusted_close`，只有缺失时才回退到 `close`；
- chart / sparkline 默认也应遵循 total-return-first 的顺序，避免把除权除息导致的机械跳空误当成真实损失。

### 2.2 组合基准货币

每个 `Portfolio` 必须有 `base_currency`。

所有组合级指标默认以 `base_currency` 表达，包括：

- NAV
- market value
- P&L
- benchmark-relative return
- scenario P&L

### 2.3 正负号约定

默认采用“从组合视角”记账：

- 买入证券：现金减少，证券头寸增加；
- 卖出证券：现金增加，证券头寸减少；
- `deposit`：外部资金流入组合；
- `withdrawal`：外部资金流出组合；
- 收费 / 税费：组合价值下降；
- `market_value`、`NAV`、`cash balance` 默认正值展示；
- 损失类结果如 `drawdown`、`scenario_return` 可以为负。

对 `Transaction` 事实层的输入约定：

- `quantity`、`gross_amount`、`fees`、`taxes` 在存储层默认使用非负 magnitude；
- 方向由 `transaction_type` 与派生 `LedgerPosting` 的 posting role 决定，而不是在输入 payload 上混用正负号表达买卖或流入流出；
- 导入器、手工录入与 API 都必须遵守同一套 magnitude contract。

### 2.4 外部现金流与内部现金流

绩效口径中必须区分：

- **External cash flow**
- **Internal portfolio flow**

默认分类如下：

#### External cash flows

- deposit
- withdrawal
- 组合对投资人真实分配的现金

#### Internal portfolio flows

- buy / sell
- account-to-account transfer（同一 portfolio 内，使用 `transfer_in / transfer_out + transfer_group_id`）
- dividend / coupon 由持仓证券支付到组合现金账户
- maturity_redemption / principal return 由发行人支付到组合现金账户
- fee / tax 作为组合内部费用损益

说明：

- 证券分红、票息对组合来说是收益，不是 external flow。
- 同一 portfolio 内部不同账户之间的现金划转，不改变组合级绩效。
- `deposit` / `withdrawal` 在首版保留给组合边界现金流，不再与内部转账混用。
- `transfer_in` / `transfer_out` 在首版只表示 `internal_portfolio` scope 的成组内部迁移，必须通过 `transfer_group_id` 关联，并显式携带 `transfer_object_type = cash | position`。
- 对证券类交易，`account_id` 表示 `securities_account`；现金腿优先取 `settlement_cash_account_id`，若为空则回退到账户的 `default_settlement_cash_account_id`。
- `deposit` / `withdrawal` 必须记入 `deposit_account`；`buy` / `sell` / `dividend` / `coupon` / `maturity_redemption` 默认记入 `securities_account`。
- 若 `transfer_object_type = cash`，只更新 `deposit_account` 现金账本；若 `transfer_object_type = position`，只更新 `securities_account` 持仓账本。
- account-level cash / position ledger 必须通过 `Transaction -> LedgerPosting` 的确定性展开生成；`deposit_account` 账本是派生视图，不要求用户为同一结算再录入第二条现金交易。
- 证券现金腿的 settled cash 进入账户账本的业务日期是 `LedgerPosting.effective_date = settlement_date`；证券头寸 posting 的 `effective_date = trade_date`。
- `opening_balance` 是 bootstrap event，不属于正常运行期的 external / internal recurring flow。

### 2.4.1 opening_balance 处理

- `opening_balance` 只允许出现在组合导入起点或 inception 边界；
- 在 TTWROR 与 `Delta` 口径中，`opening_balance` 用于建立期初 `MVB` / 起始持仓，不作为区间内 external flow 重复计入；
- 在 IRR / MWROR 中，若测量窗口从导入起点开始，`opening_balance` 视为期初初始投入；若测量窗口开始于其后日期，则它落在窗口外，不再重复记为现金流。
- `opening_balance + position` 必须进一步 materialize 成 opening lots；`gross_amount` 解释为 imported remaining cost basis，而不是 bootstrap 日市值。
- `opening_balance` 的 `trade_date` / `settlement_date` 表示导入边界；历史 lot 的 acquisition date 必须落在 opening lots 上，而不是伪造到 bootstrap transaction 的成交日期里。
- 若导入源提供历史 lots，则必须逐 lot 保留 `acquisition_date` 与 remaining cost basis；若只能提供聚合头寸，则只能生成单个 `synthetic_opening` lot，并明确其后的 FIFO / realized P&L 解释从该 synthetic lot 起算。

### 2.5 权重口径

正式版默认只保留一套显式权重：

- `portfolio_weight = market_value / total_nav`

其中：

- `total_nav` 包含现金及其他组合资产净额；
- 排除现金后的归一化若需要使用，必须在具体分析指标处单独说明，不能默认复用为另一套通用 `weight` 字段。
- 若前端要展示排除现金或 invested-only 视角，必须显式命名为 `invested_only_weight` / `ex_cash_weight` 一类分析字段，而不是继续叫 `weight`。

### 2.6 年化约定

默认采用以下年化约定：

- Return / IRR：`ACT/365.25`
- Daily volatility / tracking error：优先使用实际有效收益观察密度推断 `periods_per_year`
- Weekly volatility：`52`
- Monthly volatility：`12`

若观察频率是稳定交易日频，`periods_per_year` 通常接近 `252`；若存在节假日、缺价或非交易日 carry-forward，系统必须记录并使用实际有效收益观察密度，避免把无市场观察的 0 return 当作风险样本。

### 2.7 缺失数据与覆盖率

如果某一估值日存在缺失数据：

- 价格缺失时，可使用最近可用价格前向填充，但必须记录 `stale_price_flag`；
- benchmark 缺失时，benchmark-relative 指标只在重叠日期上计算，但不影响绝对口径 snapshot 的 `complete` 状态；
- 若重叠覆盖率低于配置阈值，结果标记为 `partial` 或 `unavailable`；
- 风控和 review 页面必须显示 coverage ratio，前端不得把缺失数据伪装成正常结果。

## 3. 估值与 NAV

### 3.1 单个头寸市值

对于任意 instrument `i`：

$$
MV_{i,t}^{local} = Q_{i,t} \times P_{i,t} \times Multiplier_i
$$

其中：

- `Q` 为数量；
- `P` 为估值价格；
- `Multiplier` 默认为 `1`，若未来支持合约乘数则显式给出。

换算到组合基准货币：

$$
MV_{i,t}^{base} = MV_{i,t}^{local} \times FX_{i,t}^{local \to base}
$$

### 3.2 组合总 NAV

组合总净值：

$$
NAV_t = SettledCash_t^{base} + PendingSettlementNet_t^{base} + \sum_i MV_{i,t}^{base} + OtherAssets_t - Liabilities_t
$$

MVP 中：

- `SettledCash_t^{base}` 表示截至 `t` 已经按 `effective_date` 生效的现金 posting；
- `PendingSettlementNet_t^{base}` 表示 trade date 已确认、但 cash leg 尚未到 `effective_date` 的证券结算应收 / 应付款；该值在 settlement 前继续留在 NAV 中，settlement 当日转入 `SettledCash_t^{base}`；
- `Accounts` workspace、account-axis contribution、Research current context / actual rows 在任意 `as_of_date = t` 都必须复用同一条 settled-vs-pending 口径；不能出现 settled cash 已按 `effective_date` 截断，但 ending value / actual rows 又漏掉 pending settlement 的情况；
- `OtherAssets_t` 可先默认为 `0`，除非显式支持应收项；
- `Liabilities_t` 可先包含费用、税费、应付款等可识别项目；
- 若未显式支持某类应计项目，则必须在结果说明中标明未纳入。

### 3.3 权重

对任意头寸 `i`：

$$
portfolio\_weight_{i,t} = \frac{MV_{i,t}^{base}}{NAV_t}
$$

规则：

- 任何涉及 target、drift、risk diagnostics 的结果必须带 `weight_basis`；
- 首版 `TargetSet.target_weight` 的 canonical basis 固定为 `portfolio_nav`；
- API/UI 不允许只返回一个无语义的 `weight` 字段。

### 3.5 Cost Basis / Purchase Value

正式版首版默认 canonical cost-basis method 采用 **FIFO**，优先贴近 PP 的 Statement of Assets / Trades 逻辑。

对导入边界上的 opening positions：

- `opening_balance + position` 是 bootstrap event，但成本法上必须继续落实到 lots；
- one opening lot in import payload = one cost-basis unit in FIFO world；
- 若是 multi-lot imported position，后续卖出必须先按这些 imported opening lots 的 FIFO 顺序匹配，而不是先聚合再卖出。

### 3.5.1 Bond valuation boundary

首版债券估值采用 **valuation-first** 口径：

- 首版 `bond` 默认指 `plain-vanilla cash bond`；
- 组合 NAV、持仓市值、绩效计算统一以 **dirty market value** 为 canonical basis；
- 若数据源提供的是 clean price，则必须同时提供 accrued interest，再合成为 canonical dirty value；
- coupon 作为现金收益进入 ledger；到期本金回收通过 `maturity_redemption` 或等价显式事件入账；
- duration、convexity、yield、spread 等字段可作为解释性外部输入展示，但不是首版 canonical 自研计算结果。

#### Open position purchase value

对任意头寸，未平仓剩余份额的 purchase value 定义为：

- 基于 FIFO lot matching 后，当前仍未卖出的 buy lots 成本之和；
- 包含应计到 lot 的交易费用；
- 不包含已经 realized 的部分。

#### Unrealized P&L

$$
UnrealizedPnL_i = MV_i^{base} - PurchaseValue_i^{open}
$$

#### Realized P&L

每次卖出时，按 FIFO 依次匹配最早未平的买入 lot：

$$
RealizedPnL = NetSaleProceeds - MatchedCostBasis
$$

说明：

- 首版以 FIFO 作为默认统一口径，避免持仓页、trades 页、review 页各用不同成本法；
- 若未来需要支持 average cost，也应作为显式切换口径，不得静默替换 canonical 默认值。

### 3.5.2 Internal position transfer

对 `transfer_object_type = position` 的内部转仓，首版 canonical 规则如下：

- 它不是卖出再买入，不得形成 realized P&L；
- source account 的 open lots 必须 in-kind 搬迁到 destination account；
- 被迁移的 lot slice 必须保留原 acquisition date、remaining cost basis、unit cost 与已分摊 fees；
- 若未显式提供 lot-level selection，部分转仓默认按 source account 内的 FIFO open-lot 顺序切分；
- `TradeView` 不把内部 position transfer 解释为新的 open trade 或 closed trade，只更新所属 account 归属。

### 3.6 Trades 视图计算

`Trades` 子视图不是原始交易流水，而是 lot matching 后的派生结果。

首版默认定义：

- **Open trade**：尚未被完全平仓的买入 lot 或 lot slice
- **Closed trade**：已被卖出完全匹配的 lot slice

每条 `TradeView` 至少应包含：

- open date
- close date（open trades 可为空）
- matched quantity
- purchase value
- proceeds
- realized / unrealized P&L
- holding period

规则：

- `Transactions` 页面显示原始账本流水；
- `Trades` 子视图显示 lot-matched 分析结果；
- 二者不能混为同一对象。

### 3.7 Events 视图语义

`Events` 子视图服务于单资产 detail pane，不是组合级总账页面。

默认事件来源包括：

- corporate actions
- dividend / coupon records
- data quality warnings
- manual event notes

规则：

- `Events` 是解释层时间轴，用于说明某个资产发生过什么；
- `Events` 可引用交易或公司行为，但不等于原始账本流水；
- 改变持仓或成本基础的事件，必须能追溯到底层 `Transaction` 或 `CorporateAction`。
- `dividend / coupon` 进入 `Events` 时，若已经入账，则必须引用对应 `Transaction`；不得在事件层再次形成独立 ledger posting。

## 4. FX 口径

### 4.1 基础换算

所有组合级价值对象先换算到 `base_currency` 再聚合。

### 4.2 基准货币收益

base currency 下的单期收益应优先直接从 base value 计算：

$$
r_{i,t}^{base} = \frac{MV_{i,t}^{base} - MV_{i,t-1}^{base} - CF_{i,t}^{internal}}{MV_{i,t-1}^{base}}
$$

若需要拆分本地收益与 FX 收益，则使用：

$$
1 + r_{i,t}^{base} = (1 + r_{i,t}^{local}) \times (1 + r_{i,t}^{fx})
$$

因此：

$$
r_{i,t}^{base} = r_{i,t}^{local} + r_{i,t}^{fx} + r_{i,t}^{local} \times r_{i,t}^{fx}
$$

规则：

- 正式结果以 `base` 结果为准；
- 本地收益与 FX 收益分解仅作为解释层附加输出。

## 5. 绩效口径

### 5.1 默认返回语义

除非明确写成 `absolute_change`、`delta` 或 `pnl`，否则系统中的“return”默认指：

- 组合级：`TTWROR`
- 资金使用效率：`IRR / MWROR`
- 基准对比：`benchmark-relative return`

### 5.2 Daily TTWROR

正式版组合级 TTWROR 采用 Portfolio Performance 的日级 true time-weighted 逻辑：

$$
r_t = \frac{MVE_t + CF_{out,t}}{MVB_t + CF_{in,t}} - 1
$$

其中：

- `MVB_t`：当日开始市值，等于上一估值日 `MVE`；
- `MVE_t`：当日结束市值；
- `CF_in,t`：当日外部流入；
- `CF_out,t`：当日外部流出。

解释：

- 外部流入放在分母，视作在当日开始投入；
- 外部流出加回分子，视作在当日结束取出；
- 这样可以把 external flows 从业绩中中性化。

### 5.3 Cumulative TTWROR

给定区间内共有 `n` 个子期间：

$$
R_{cum} = \prod_{t=1}^{n}(1+r_t)-1
$$

这是组合页面、绩效页和 review 中默认的区间收益口径。

组合的 `TWR Index` 是把 `R_cum` 归一到 100 后得到的组合表现曲线。它在语义上类似基金的 total-return NAV / cumulative NAV，但不是组合会计单位净值；它只用于投资表现、回撤、波动和 benchmark comparison，不用于资产规模或账面 NAV 展示。

### 5.4 Annualized TTWROR

若区间长度为 `Y` 年（按 `ACT/365.25` 计算）：

$$
R_{ann} = (1 + R_{cum})^{1/Y} - 1
$$

若 `Y` 接近 0，则年化结果记为 `unavailable`。

### 5.5 IRR / MWROR

IRR 使用 XIRR 语义，按实际日期折现：

$$
\sum_{k=0}^{m}\frac{CF_k}{(1+IRR)^{\tau_k}}=0
$$

其中：

- `\tau_k` 为相对起始日的 `ACT/365.25` 年分数；
- `CF_k` 采用“从投资人视角”的符号约定。

默认现金流构造：

- 期初组合市值：负数（视为已投入资本）
- 期间 external contributions：负数
- 期间 withdrawals / distributions：正数
- 期末组合市值：正数

说明：

- IRR 反映资本使用效率；
- TTWROR 反映经理在中性化 external flows 后的投资表现；
- UI 不允许用 IRR 替代 TTWROR 展示“组合收益”。

### 5.6 Absolute Change 与 Delta

### Absolute Change

$$
AbsoluteChange = MVE - MVB
$$

### Delta

$$
Delta = (MVE - MVB) - NetExternalInflow
$$

其中：

$$
NetExternalInflow = \sum CF_{in} - \sum CF_{out}
$$

`Delta` 表示扣除 external cash flows 后的绝对收益金额。

### 5.7 Drawdown

基于累计增长指数：

$$
G_t = \prod_{s=1}^{t}(1+r_s)
$$

运行峰值：

$$
Peak_t = \max_{s \le t} G_s
$$

回撤：

$$
DD_t = \frac{G_t}{Peak_t} - 1
$$

关键指标：

- `current_drawdown = DD_t`
- `max_drawdown = min(DD_t)`
- `max_drawdown_days = peak -> trough`
- `drawdown_duration_days = peak -> recovery`

约束：

- 组合级 drawdown 必须基于 `TTWROR` 复合后的 `G_t`，不得基于资产规模 `NAV_t` 直接计算；
- 任何带 `start_date / end_date` 的区间 summary 都必须用区间内 `daily_ttwror` 重新复合并重算 drawdown，不能直接复用 inception-to-date 的 `cumulative_ttwror` 或 snapshot-level drawdown；
- 若主图显示 `Portfolio Value`，下方 drawdown 仍然使用 `TWR Index`，因为外部出入金不应制造或稀释投资回撤。

### 5.8 Volatility / Sharpe / Sortino / Tracking Error

### Portfolio volatility

默认使用组合 `daily_ttwror` 的 simple returns 构造风险统计；当需要对齐 PP 风格展示时，可额外输出 log-return 版本，但 canonical risk API 默认仍以 simple returns 为主。资产规模 `NAV_t` 的变化不得作为组合级 volatility / Sharpe / Sortino 的输入。

$$
\sigma_{ann} = std(r_t) \times \sqrt{periods\_per\_year}
$$

### Sharpe ratio

$$
Sharpe = \frac{\mu_{ann} - r_f}{\sigma_{ann}}
$$

MVP 默认：

- `r_f = 0`，除非显式提供 risk-free series；
- `\mu_{ann}` 由日均收益年化得到。

### Sortino ratio

$$
Sortino = \frac{\mu_{ann} - r_f}{\sigma_{downside}}
$$

### Tracking error

给定 benchmark 日收益 `r_{b,t}`：

$$
a_t = r_{p,t} - r_{b,t}
$$

$$
TE_{ann} = std(a_t) \times \sqrt{252}
$$

### Information ratio

$$
IR = \frac{mean(a_t) \times 252}{TE_{ann}}
$$

## 6. Benchmark-relative 口径

### 6.1 基准序列

benchmark 必须先转化到组合基准货币，并对齐到组合估值日期。

若 benchmark 提供的是 NAV / index level：

$$
r_{b,t} = \frac{B_t}{B_{t-1}} - 1
$$

### 6.2 Primary benchmark

一个组合可以有多个 benchmark definition，但 canonical 计算必须先从 `PortfolioBenchmarkAssignment` 解析出当前生效的 `primary benchmark`。

以下指标必须始终基于 resolved `primary benchmark`：

- benchmark return
- excess return
- tracking error
- information ratio
- active contribution

规则：

- current-state 页按 `as_of_date` 解析 active primary benchmark assignment；
- period 页按 `[period_start, period_end]` 解析 primary benchmark assignment；若区间内发生切换，review 必须显式标记 mixed benchmark context 或按分段汇总；
- canonical benchmark resolution 不得读取 `Portfolio` 上的固定 benchmark pointer；唯一真相来源是 `PortfolioBenchmarkAssignment` 的生效区间解析；
- 若不存在可解析的 primary benchmark assignment，则 benchmark-relative 结果进入 `comparator missing / unavailable` 状态。

### 6.3 默认比较对象选择

系统不采用 `benchmark / target set / alert rule` 之间的静默互相替代。

canonical 规则如下：

- market-relative return / active contribution / snapshot relative columns：使用 `primary benchmark`
- benchmark_active_weight / benchmark-relative exposure / benchmark-active bets：使用 `primary benchmark`，且仅在 benchmark composition 可用时启用
- target_weight_gap / construction drift / rebalance diagnostics：使用 selected taxonomy 下 resolved target 的 `weight` 维度
- limit checks / alerts：使用 configured `AlertRule`
- risk budget gap：使用 selected taxonomy 下 resolved target 的 `risk_budget` 维度
- review target weight drift / target risk budget summary：使用 selected planning taxonomy 在 `[period_start, period_end]` 上的 resolved target timeline，并按已启用维度分别解释

其中：

- 被用于 drift / risk budget gap 的 selected taxonomy 必须是 `planning_enabled = true`；
- resolved target 必须按维度解析，而不是整套 `TargetSet` 整体替换：
  - `weight` 维度：若 active `TAA` 启用了 `weight`，则取 active `TAA`；否则回退到 active `SAA`
  - `risk_budget` 维度：若 active `TAA` 启用了 `risk_budget`，则取 active `TAA`；否则回退到 active `SAA`
- resolved target 结果必须显式携带 `resolved_weight_target_set_id/type` 与 `resolved_risk_budget_target_set_id/type`；
- 若两个维度来源不同，`target_resolution_mode` 必须标记为 `mixed_dimensions`；若区间内来源随时间变化，则标记为 `mixed_timeline`
- period analytics 必须把上述结果 materialize 为正式 `ResolvedTargetTimeline` / `ResolvedTargetSegment`，而不是匿名 timeline blob；
- `TargetSet(type = taa)` 在存储层必须已物化为对已启用维度完整的目标集，运行时不做稀疏 overlay 解析；
- review period 内若 target 发生切换，系统必须按生效区间分段汇总，而不是拿单一期初或期末 target 解释整个区间；
- 若用户切到纯分析 taxonomy，系统应明确退回 `absolute only` 或标记 comparator missing。

若某项分析缺少其 canonical comparator：

- 结果状态记为 `unavailable` 或退回 `absolute only`
- UI 必须明确标识 comparator missing
- 结果对象必须显式携带 `taxonomy_id` 与按维度解析后的 target source 信息
- 不允许无提示地改用另一类比较对象

### 6.4 Excess return

区间层面默认采用：

$$
ExcessReturn = R_{p,cum} - R_{b,cum}
$$

日频层面采用：

$$
a_t = r_{p,t} - r_{b,t}
$$

说明：

- `ExcessReturn` 是 arithmetic relative result；
- 若未来需要 `relative wealth ratio`，必须另起字段，不得复用 `excess_return` 名称。

### 6.5 Benchmark coverage

benchmark 能力应按可用数据分层理解：

- `return_only`：仅有 benchmark return series；可计算 excess return、tracking error、information ratio
- `group_weight`：额外有 benchmark group weights；可计算 group-level `benchmark_active_weight`、relative exposure 与 group-level benchmark attribution
- `constituent_weight`：额外有 benchmark constituent weights；可计算 security-level `benchmark_active_weight` 与更细的 benchmark-relative attribution

benchmark-relative 指标必须携带：

- overlap start / end
- overlapping observations
- coverage ratio
- status: `complete` / `partial` / `unavailable`

若 benchmark 只有 `return_only` 能力：

- 不允许计算 `benchmark_active_weight`
- 不允许计算 benchmark-relative exposure
- 不允许启用 Brinson 类 attribution
- UI 必须明确标识 `benchmark composition unavailable`
- `Review` 只能输出 `benchmark-relative performance summary`，不输出 `benchmark-active bets summary`

## 7. 归因口径

### 7.1 首版归因目标

首版优先支持**日频算术归因**，而不是一开始就做最复杂的多层 Brinson 变体。

说明：

- benchmark group-weight coverage 不足时，仅输出 contribution / excess return 口径，不强行输出 Brinson 风格分解；
- 任何 Brinson 或 benchmark allocation/selection 归因都依赖可用的 benchmark composition 数据。

### 7.2 组合日贡献

对任意 instrument 或 group `g`：

$$
Contribution_{g,t} = w_{g,t-1} \times r_{g,t}
$$

区间贡献：

$$
Contribution_{g,period} = \sum_t Contribution_{g,t}
$$

### 说明

- `w_{g,t-1}` 使用上一估值日权重，避免当日结果前视；
- 组级 return 由组内 base-currency market value 变化得到；
- 归因和总收益之间允许存在小 residual，必须显式展示。

### 7.3 Active contribution

若 benchmark 同样可按 group 聚合：

$$
ActiveContribution_g = Contribution_{p,g} - Contribution_{b,g}
$$

首版优先支持：

- instrument contribution
- taxonomy contribution
- planning taxonomy contribution
- benchmark-relative group contribution

## 8. 暴露与集中度口径

### 8.1 Exposure aggregation

按任意 grouping axis（taxonomy node、selected planning taxonomy node、region、sector）聚合时：

$$
Exposure_{group} = \sum_{i \in group} weight_i
$$

其中 `weight_i` 必须来自显式 `weight_basis`。

### 8.2 Concentration

### HHI

$$
HHI = \sum_i w_i^2
$$

### Top-k concentration

$$
TopK = \sum_{i \in largest\ k} w_i
$$

默认：

- concentration 诊断默认先排除现金，再对剩余头寸归一化；
- 若改用 `portfolio_nav`，必须在结果对象中显式标记。

## 9. Weight Drift 口径

### 9.1 单项 weight drift

对任意 instrument 或 group：

$$
Drift_i = CurrentWeight_i - TargetWeight_i
$$

### relative drift

若 `TargetWeight_i \neq 0`：

$$
RelativeDrift_i = \frac{CurrentWeight_i - TargetWeight_i}{TargetWeight_i}
$$

其中：

- `TargetWeight_i` 来自按维度解析后的 resolved `TargetSetLine.target_weight`
- canonical target 只能在 selected taxonomy 的 `budgeting_level` 上直接录入；父层节点目标必须派生汇总
- 首版 canonical target weight 固定为 `portfolio_nav` basis
- drift 计算前必须先校验 selected `TargetSet` 启用了 `weight` 维度，且在 budgeting level 上形成完整节点集；若存在 gross leverage / overlay，`target_weight` 总和可以大于 `100%`
- 若 active `TAA` 未启用 `weight` 维度，则必须回退到 active `SAA` 的 `weight` 维度；只有当 `SAA` 也未提供该维度时，target drift 才记为 `unavailable` / `comparator missing`

### 9.2 Total drift

$$
TotalDrift = \sum_i |Drift_i|
$$

### Rebalance turnover lower bound

若需要更接近最小调仓量的量纲，则另算：

$$
TurnoverFloor = \frac{1}{2}\sum_i |Drift_i|
$$

规则：

- `total_drift` 与 `turnover_floor` 必须分开命名；
- 不允许把二者混成一个“总偏离”指标。

### 9.3 Drift weight basis

首版 target drift 的 canonical 比较口径固定为 `portfolio_nav`：

- `TargetSet.target_weight` 一律按 `portfolio_nav` 定义；
- `CurrentWeight_i` 在 canonical drift 中也必须使用 `portfolio_nav`；
- 若某层 target 来自 sleeve-local capital split 或 optimizer recipe，则它不是 canonical drift 的直接输入；只有解析成 `portfolio_nav` basis 的 resolved implementation weight 后，才能进入 drift compare；
- 因而不能默认用 `ParentWeight × ChildLocalWeight` 机械展开所有层级权重；只有当 child weight 明确也是 capital share 且不会被内部求解器 / 杠杆 / 对冲改写时，该乘法才成立；
- resolved `target_weight` 必须在 budgeting level 上形成完整节点集，但总和不要求固定为 `100%`；若组合存在 gross leverage / overlay，target gross exposure 可以大于 `100%`；
- 排除现金后的分析口径可用于暴露、集中度或纯分析展示，但不能替代 canonical target drift 口径。

### 9.4 MVP weight drift source decomposition

首版 canonical weight drift source decomposition 只锁定到三层对象：

- `resolved_target`
- `intended_target`（optional）
- `actual`

定义：

- `resolved_target`：由 selected planning taxonomy 下的 active `SAA` / `TAA` 解析出的正式 target；
- `intended_target`：把 active tilts 物化到 budgeting level 后形成的意图目标视图；若未提供该物化视图，则视为 unavailable；
- `actual`：当前组合在同一 budgeting level 上的实际权重。

若 `intended_target` 可用：

$$
IntentionalGap_i = IntendedWeight_i - ResolvedTargetWeight_i
$$

$$
UnintendedGap_i = ActualWeight_i - IntendedWeight_i
$$

且：

$$
Drift_i = IntentionalGap_i + UnintendedGap_i
$$

规则：

- `intentional / unintended` 分解只在 `intended_target` 已被物化且与 current target 使用同一 budgeting level 时启用；
- 单条 `TiltDecision` 本身不是 drift source decomposition 的直接计算输入；
- 首版不把 `market drift` 与 `execution bias` 进一步拆成独立 canonical 字段，避免伪精确解释。

## 10. 目标风险份额口径

### 10.1 目标

目标风险份额模块要回答的不是“仓位偏了多少”，而是：

- 风险贡献偏了多少；
- 偏离来自哪里；
- 是否已经背离当前 taxonomy target design。

### 10.2 Covariance-based portfolio risk

给定权重向量 `w` 和协方差矩阵 `\Sigma`：

$$
\sigma_p = \sqrt{w^\top \Sigma w}
$$

### 10.3 Marginal / Component Risk Contribution

### Marginal contribution to risk

$$
MCTR_i = \frac{(\Sigma w)_i}{\sigma_p}
$$

### Signed component contribution

$$
CTR_i^{signed} = w_i \times MCTR_i
$$

### Absolute component contribution

$$
CTR_i^{abs} = |CTR_i^{signed}|
$$

### 10.4 Realized risk share

正式版默认把 **目标风险份额对比口径** 定义为 `absolute risk share`：

$$
RiskShare_i = \frac{CTR_i^{abs}}{\sum_j CTR_j^{abs}}
$$

原因：

- 对冲或负相关位置可能产生负的 signed contribution；
- 用 absolute share 更适合做 PM 视角的 target risk share comparison；
- advanced analytics 仍可单独暴露 `signed contribution`。

### 10.5 Risk budget gap

若 `TargetRiskShare_i` 为目标风险预算占比：

$$
RiskBudgetGap_i = RiskShare_i - TargetRiskShare_i
$$

总预算偏离：

$$
TotalRiskBudgetGap = \sum_i |RiskBudgetGap_i|
$$

其中：

- `TargetRiskShare_i` 来自按维度解析后的 resolved `TargetSetLine.target_risk_share`
- canonical target 只能在 selected taxonomy 的 `budgeting_level` 上直接录入；父层节点目标必须派生汇总
- `RiskShare_i` 与 `TargetRiskShare_i` 必须使用同一风险分母；首版 canonical top-level compare 的分母是 selected taxonomy 下的 portfolio-level absolute risk share
- 层级 sleeve 内部的 `25%` 这类 local risk budget 表示“占父 sleeve 内部风险的 25%”，不是全组合风险的 `25% × 父层预算`
- 因而禁止通过祖先 `target_risk_share` 乘法把 local sleeve risk budget 铺平成全局 risk-budget target；若需要全局 leaf comparator，必须先由 solver / resolved implementation target 在全组合协方差下显式解出
- risk budget gap 计算前必须先校验 resolved risk-budget target 可用，且其非现金节点的 `target_risk_share` 加总为 `100% ± epsilon`，现金节点 `target_risk_share = 0`
- 若 active `TAA` 未启用 `risk_budget` 维度，则必须回退到 active `SAA` 的 `risk_budget` 维度；只有当 `SAA` 也未提供该维度时，risk budget gap 才记为 `unavailable` / `comparator missing`

说明：

- `RiskBudgetGap` 就是首版里唯一的风险预算偏离指标；
- 其计算输入仍然是 `realized risk share` 与 `target_risk_share`；
- canonical 计算对象是 selected taxonomy 下按维度解析后的 risk-budget target，而不是简单等同于整套 active target set 或独立风险预算对象。
- 若用户 drill into 某个 sleeve 做 local compare，则结果对象与 UI 必须显式标记 denominator = `parent_local_risk`，不得静默沿用 portfolio-level 含义。

### 10.6 Weight basis for target risk share comparison

默认规则：

- `risk share` 基于参与风险计算的头寸权重；
- 首版默认在参与风险计算的头寸集合内归一化权重进入协方差风险计算；
- 现金默认不贡献市场风险，除非显式建模为风险因子。

如果 target-set 设计需要把 cash 作为 selected planning taxonomy 下的独立节点展示：

- 它可以在 SAA 或 TAA `target_weight` 中存在；
- 但在 covariance-based realized risk share 中通常为 `0`；
- UI 必须解释“capital share != risk share”。

## 11. Scenario P&L 口径

### 11.1 情景冲击对象

情景可以定义在以下层级：

- instrument
- taxonomy group
- selected planning taxonomy node
- factor bucket

若同一情景不是 instrument-level，则需通过映射规则下沉到 instrument。

### 11.2 单头寸 scenario P&L

对头寸 `i` 和情景 `s`：

$$
ScenarioPnL_{i,s} = MV_{i}^{base} \times ShockedReturn_{i,s}
$$

### 11.3 组合 scenario P&L

$$
ScenarioPnL_{p,s} = \sum_i ScenarioPnL_{i,s}
$$

默认情景收益率使用 **总 NAV** 作为分母：

$$
ScenarioReturn_{p,s} = \frac{ScenarioPnL_{p,s}}{NAV}
$$

这条规则必须固定，不能随 `weight_basis` 自动切换。

说明：

- 该归一化规则只适用于 `realized_risk_share / target_risk_share` 比较；
- canonical target drift 不属于该规则，仍固定按 `portfolio_nav`；
- 但 scenario loss 反映的是整个组合会亏多少，所以分母固定为 `total_nav`。

### 11.4 Group contribution in scenarios

group 级情景损益：

$$
ScenarioPnL_{group,s} = \sum_{i \in group} ScenarioPnL_{i,s}
$$

group 级贡献比例：

$$
ScenarioContributionShare_{group,s} = \frac{ScenarioPnL_{group,s}}{ScenarioPnL_{p,s}}
$$

若组合总情景损益接近 0，则 contribution share 记为 `unavailable`。

## 12. 输出状态与质量标记

所有关键分析结果应支持以下状态：

- `complete`
- `partial`
- `unavailable`

并附带至少以下质量字段：

- `as_of_date`
- `valuation_currency`
- `weight_basis`（若适用）
- `coverage_ratio`
- `stale_price_count`
- `benchmark_overlap_ratio`（若适用）
- `calculation_version`

## 13. 首版明确不锁死的高级口径

以下能力首版可以保留扩展位，但不在当前 canonical 规格中锁死：

- multi-period Brinson linking 的完整变体
- factor model ex-ante risk
- option Greeks
- CVaR / Expected Shortfall 的复杂参数化定义
- fixed income full accrual engine
- liquidity-adjusted scenario engine

## 14. 实现优先级

按当前 PRD，计算实现建议优先级如下：

1. `P0`
   NAV、weights、FX、TTWROR、IRR、drawdown、benchmark-relative、drift、basic exposures、scenario P&L
2. `P0`
   covariance-based realized risk、risk share、target risk budget gap、tracking error、information ratio
3. `P1`
   attribution refinement、coverage diagnostics 深化、signed vs abs risk decomposition
4. `P2`
   factor risk、liquidity / capacity、pre-trade what-if

## 15. 下一步

在这份计算规格之后，下一份文档应为：

- `05_INFORMATION_ARCHITECTURE.md`

目标是把这里的对象和指标映射到：

- Snapshot
- Holdings
- Performance
- Risk
- Transactions / Accounts
- Review
- Research

并明确每个页面展示哪套分母、哪套权重和哪套 drill-down。
