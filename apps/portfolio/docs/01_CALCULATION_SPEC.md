# Portfolio 计算口径与目标规格

> 文档状态：本文定义 Portfolio 的 canonical 计算合同和已确认的上线口径。标记为 future design 的对象不得被 UI、API 或其他文档描述成已上线能力；新的实现偏差应在对应 issue/执行记录中明确登记，不在本文内静默兼容。

> 适用范围：仅适用于 `apps/portfolio` 的组合级 Overview / Holdings / Performance / Risk / Research。本文不定义 `apps/watchlist` 的 fund/instrument detail，也不能用 Watchlist 的同名 Performance / Risk tab 作为实现或验收依据。

关联文档：

- [`02_GIPS_ALIGNMENT.md`](./02_GIPS_ALIGNMENT.md)
- [`03_HOLDINGS_FIELD_REFERENCE.md`](./03_HOLDINGS_FIELD_REFERENCE.md)：Holdings 每个 UI / export 字段的完整映射和分组合同。

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
  - TWR
  - IRR / MWROR
  - Statement of Assets 风格的 purchase value / cost basis
  - open / closed trades 的 lot matching 逻辑
- **本项目扩展负责**：
  - benchmark-relative analytics
  - risk system
  - target risk budget gap
  - scenario P&L
- **GIPS-informed 方法治理负责**：
  - TWR 优先于 MWR 作为默认绩效呈现口径
  - external cash flow policy 的稳定定义
  - 外部现金流日期估值、子期间收益几何链接和方法一致性
  - coverage / stale / unavailable 边界披露

换句话说：

- 涉及 `持仓、成本、交易匹配、收益率` 的 canonical 口径，尽量先向 PP 靠拢；
- 涉及 `风控、风险预算、buy-side period analysis` 的部分，再由本项目扩展。
- 涉及绩效呈现政策、外部现金流治理和方法一致性时，采用 GIPS-informed 原则，但不声称本项目或任意组合 GIPS compliant。

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
- `Benchmark definition`；`portfolio benchmark assignment` 是尚未实现的 future design
- `Portfolio taxonomies / target sets / alert-rule config`

#### 派生结果

- `Positions`
- `Lots`
- `Trades`
- `Snapshots`
- `Performance`
- `Risk`
- `Research`

规则：

- `Transactions` 是原始账本；
- `Trades` 是 lot matching 后的分析视图；
- `Quotes`、`FX`、`benchmark series` 是共享市场数据，不属于单个组合私有；
- `Snapshots` 是由 Analytics 从事实层生成的派生产物；
- 页面展示不得绕过这些输入直接手填结果字段。

### 1.3 正确性与失败策略

计算层不保留为了“让结果看起来可用”的兼容分支。任何 canonical 指标必须满足其输入、配置、覆盖率和数学条件；条件不满足时，结果应显式进入 `partial`、`unavailable`、`comparator missing`、`insufficient-history` 或失败状态。

严格规则：

- 不用目标权重、等权、历史旧算法或另一维度 target 替代 risk-budget 求解结果；
- 不用 stale price、跨 period forward fill 或不同长度持有期收益补齐 covariance、correlation、Sharpe、target-volatility overlay 或 risk contribution；
- 不在 `SAA` / `TAA`、`weight` / `risk_budget`、benchmark / target / alert rule 之间静默互相替代；
- 单成员非现金 scope 允许输出数学上唯一确定的 100% 权重；`risk_budget` target 只包含承担风险的非现金成员，现金不得建立 `target_risk_share = 0` 的占位行。纯现金 scope 没有可求解的风险预算；除此之外，未配置目标不是默认等权目标；
- UI、API、export 必须展示结果状态与 coverage / solver 诊断，不能把失败条件包装成正常结果。

## 2. 总体约定

### 2.1 估值时点

正式版默认使用 **end-of-day valuation**：

- 当日价格、FX、benchmark level 都视为当日收盘或该日最终可用估值；
- 当日 `MVB` 等于上一估值日的 `MVE`；
- 当日 `MVE` 为当日收盘后的组合总市值与现金合计。
- 物化 daily snapshot 必须同时保存 `beginning_nav` 与 `ending_nav`。组合已经存在时，显式查询中的 `start_date`、`end_date` 都是 **EOD valuation boundary**：`initial value` 使用 `start_date` 的 `ending_nav`，`final value` 使用 `end_date` 的 `ending_nav`，收益子期间为 `(start_date, end_date]`。
- 因此 `2026-01-01` 到 `2026-01-07` 表示 `2026-01-01` 收盘到 `2026-01-07` 收盘，几何链接 `2026-01-02` 至 `2026-01-07` 的 daily returns。起始日交易、现金流、收益和成本状态已经包含在期初 EOD 状态中，不得再次列入区间 flow / P&L。
- 对已有组合，`start_date = end_date` 是零长度 close-to-close 区间：起止 NAV 相同，区间收益、资金流和 P&L 为 0，不生成一日收益观察。
- MTD、QTD、YTD 的目标锚点分别是上月末、上季末和上年 `12-31`；rolling `1Y` 使用一年前同一日，闰日按目标月份最后一日截断。组合优先使用目标日的完整 EOD valuation state（休市日可以是完整的 carry state）；标的与 benchmark 使用目标日或之前最近一个可用且语义合格的收盘点。
- 日期边界例外是 **funded-segment start**：若请求起点正好是组合首次入金成立日，或组合 NAV 真正归零后的再次入金日，此前没有可用的有资本 EOD 基数，因此必须从 0 BOD capital 加当日流入的分母开始，保留该日 BOD-to-EOD 子期间。导入式 `opening_balance` 本身是已有资产的 EOD anchor，不产生导入首日收益。
- 留存现金即使无收益、无市场风险暴露，仍属于组合 NAV 和 TWR 分母，不构成零资本或 segment restart。后续新增 instrument 也不会重置组合 inception。若组合 1 日成立、3 日盘中新增资产，选择 3 日到 7 日仍表示组合 3 日 EOD 到 7 日 EOD，不含该资产成交到 3 日收盘的收益；要包含 3 日子期间，应选择 2 日作为起点。
- 若组合 NAV 归零并连续若干日没有资本，零资本日没有可定义的收益观察，不得伪造为 0%。跨越该 inactive gap 的连续 TWR 必须 fail closed；归零前后分别展示。再次入金日作为新 funded segment 可从当日 BOD contribution 分母重新计算。
- 从未入资的组合即使存在零 NAV 快照，也没有合法收益分母；Performance 必须返回 `unavailable` / 空收益，不能发布 0%。
- 所有显式区间都必须满足 `start_date <= end_date`；逆序区间由 API 以校验错误拒绝，UI 不发起该请求，不能通过排序或空结果掩盖调用错误。
- Performance、Calculation、Contribution 与 Groups 的主摘要必须保留 `requested_start_date` / `requested_end_date`，并单独返回 `effective_start_date` / `effective_end_date` 与 clamp reason。请求晚于最后可靠估值日时只收缩 effective end，不能改写用户原始请求。
- 主摘要还必须声明 `start_boundary_kind` 与 `include_start_date_return`：普通期初收盘为 `close_eod / false`，首次或重启入资为 `funded_bod / true`，导入式期初余额为 `imported_opening_eod / false`。下游不得再根据日期或首条收益自行猜测边界语义。

### 2.1.1 全球 EOD 规则

全球多资产组合的 daily snapshot 采用以下 canonical 规则：

- 每个 `Portfolio` 必须定义 `valuation_timezone` 和 `valuation_cutoff_policy`；
- 组合日度结果按 `as_of_date` 归档，`as_of_date` 表示**市场日**，不是实际计算发生的墙上时间；
- 单个资产优先使用其本地市场在该 `as_of_date` 的最新官方收盘价或该日最终可用估值；
- 行情源必须携带可审计的 daily/event-driven 更新口径、market calendar / schedule 与 `release_lag`；缺点判断以该 source schedule 为准，不能把尚未到发布时间的数据误报为缺失，也不能把超过 release lag 的缺口当成正常休市；
- 组合绝对口径快照只有在该 `as_of_date` 所需市场和 FX 数据满足覆盖率阈值后，才能标记为 `complete`；
- benchmark 相关区块的 `complete / partial / unavailable` 由 benchmark coverage 单独决定，不反向阻塞绝对口径 snapshot；
- 组合 summary、Overview 和未显式指定日期的 Holdings 默认展示 latest fresh complete `as_of_date`，而不是当前本地时钟下尚未收齐数据的“今天”。fresh complete 表示 `valuation_coverage_state = complete`、`nav` 存在，且 `stale_price_flag = false`、`stale_fx_flag = false`。为保持日度 NAV 链连续，休市日可以保存沿用上一有效点计算的 tagged snapshot，但它不能成为默认展示或正式区间终点；book P&L / attribution 的 coverage 也不能把当前边界的 stale FX 当成 fresh observation。

示例：

- 上海团队在 `2026-04-15` 早晨拿到美国市场 `2026-04-14` 的收盘数据后，该结果归属于 `as_of_date = 2026-04-14`，而不是 `2026-04-15`。

### 2.1.2 FX 与 benchmark 对齐规则

在同一个 `as_of_date` 的快照中，若需要计算 benchmark-relative 结果：

- FX 必须使用同一 provider / cut 的 EOD 数据；
- benchmark 必须对齐到同一个 `as_of_date`；
- 系统不得把 `T` 日股票收盘与 `T+1` 日 FX 或 benchmark 静默混用。

### 2.1.3 Valuation Basis vs Total-Return Basis

共享层允许同一资产同时维护多种 quote basis，例如：

- `public_fund / private_fund`: `official_nav` 与 `total_return_nav`
- `equity`: `close` 与 `adjusted_close`

这里必须区分两种用途：

- `valuation` role：服务组合 statement、持仓市值、NAV、ledger-driven performance
- `total_return` / `chart` role：服务 research target solve、资产风险序列与图表

规范如下：

- 组合账面估值不得静默切到 total-return basis；若分红或派息已作为交易/现金流入账，再用复权价会造成双算；
- 公募和私募的 research/risk 序列只使用 `total_return_nav`；缺失时结果为 unavailable/NA。`official_nav` 只用于估值和明确标注的单位净值视图，不能作为 total-return 序列的兼容回退；
- equity 的 research/risk 序列使用 `adjusted_close`；若改用 `close`，必须在结果中标记 quote basis，且不得把除权除息导致的机械跳空当成真实损失；
- chart / sparkline 必须展示实际采用的 quote basis。basis 缺失时，图表可以降级为 `partial / unavailable`，不能静默换基准。

#### Portfolio 中的基金分红处理

基金是否存在分红，必须由共享 Registry 的通用、可修订事件账本决定，Portfolio 不维护按基金名称、邮箱规则或产品 ID 写死的分红配置。

- 尚无已确认分红事件时，Portfolio 明确采用“无未记录分红”的会计假设，继续用 `official_nav` 估值并计算 ledger-driven TWR；不能因为缺少 `total_return_nav` 而阻断组合收益。
- 这个假设只适用于组合会计。它不能生成或替代基金的 `total_return_nav`；research / risk 缺少可证明的分红再投资链时仍然必须为 unavailable / NA。
- Registry 出现已确认 `cash_distribution` 后，Portfolio 按 `portfolio + securities account + stable action id` 生成待核查事项，计算权益日持仓和预期税前分红；事件本身不得自动生成现金、份额或交易。
- 操作人核对托管/管理人事实后，录入 `dividend`、`dividend_reinvestment`，或 `dividend + buy` 交易，并把交易关联到当前事件修订。关联前必须校验组合、账户、标的、权益日及 `entitled quantity × cash per unit`。
- Registry 对同一 stable action 追加 correction / cancellation 时，不得修改或删除已经存在的 Portfolio 交易；原关联自动进入重新核查。人工重新确认、修订交易或解除关联后，均保留不可变审核历史。
- 交易新增、修改、删除继续沿用统一的 snapshot invalidation 与耐久重算队列；不得为分红另建同步重算旁路，也不得在 API 请求中执行长耗时全量重算。

每条可用于估值、收益或执行价格校验的行情还必须满足显式身份合同：

- series identity 至少由 `metric_family + quote_basis + currency` 构成；同一角色出现多条匹配 series 时视为 ambiguous 并失败关闭，不能按日期把候选序列拼接；
- `price_unit` 与 `price_scale` 必须成对存在且与资产类型一致。Registry 普通价格与 FX rate 的 scale 均为 `1`；期权交易由 Portfolio 本地合约 multiplier 决定；
- 估值、上一行情、图表和交易执行价格检查必须沿用同一 resolved identity。币种、basis、unit 或 scale 不一致时返回明确 unavailable reason，不做隐式换算；
- quote role 只决定允许搜索的 basis 顺序，不得越过 metric family、currency 或 price contract 的唯一性校验。

### 2.2 组合基准货币

每个 `Portfolio` 必须有 `base_currency`。

`base_currency` 是组合报告币种，不会改写账户、交易、lot、现金余额或行情的原始币种事实。用户可以在 Portfolio Settings 中选择系统支持的报告币种；变更后必须清除该组合已有派生快照并从 inception 全量重算，不能只换 UI 符号或对旧 base-currency 结果二次换算。

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
- 证券现金腿的 settled cash 进入账户账本的业务日期是 `LedgerPosting.effective_date = settlement_date`；证券头寸 posting 的 `effective_date = position_effective_date`。未显式填写 `position_effective_date` 的历史记录与普通交易默认回退到 `trade_date`。
- 绩效核算遵循 GIPS 2.A.9 的 trade-date accounting 原则，不采用“到交收日才确认整笔交易”的 settlement-date accounting。这里的 accounting trade date 是资产/负债实际被确认的日期；对 pooled fund 申购，order date 与所有权/确定份额转移日可以不同，因此原始下单/定价日保留在 `trade_date`，确定份额归属的日末边界记录为 `position_effective_date`，二者都不得被 `settlement_date` 覆盖。
- 开盘前或盘中完成、应进入当天 EOD 持仓的交易使用 `position_effective_date = trade_date`。以当日收盘净值定价、下一个交易日确认份额的基金申购/赎回，保留真实 `trade_date`，并把确认日写入 `position_effective_date`，不得通过伪造下一日成交来隐藏当天持仓。
- 当前账本不隐式猜测 T+N，也不从成交时钟推断确认规则；确认日必须来自成交回单、注册登记或托管事实。`settlement_date < position_effective_date` 时，现金先按结算日出账，同时在对应结算现金账户的 settlement subledger 确认等额 `position_recognition_bridge`；买入场景对外呈现为 `pending_subscription`（申购结算应收/待份额确认），不得伪造成证券账户中的基金份额。确认日桥接余额消失并转为真实份额，NAV 不得在等待确认期间凭空下降。
- 交易可用份额按真实 `trade_date + trade_at` 检查，持仓与成本按 `position_effective_date` 排序确认；因此尚未确认的申购份额不能被提前卖出，而延后确认的赎回在确认日前仍保留于 EOD 持仓。数据库同时约束 `trade_date <= position_effective_date` 及可用交易类型，绕过 API 的非法事实也必须失败。
- `opening_balance` 是 bootstrap event，不属于正常运行期的 external / internal recurring flow。

### 2.4.1 opening_balance 处理

- `opening_balance` 只允许出现在显式 Portfolio inception boundary，且 trade date 与 settlement date 都必须等于该日期；
- 在 TWR 与 `Delta` 口径中，`opening_balance` 用于建立期初 `MVB` / 起始持仓，不作为区间内 external flow 重复计入；
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

- TWR annualization：calendar-anniversary Actual/Actual；相同月日的一周年（29 February 按目标年 2 月最后一日截断）精确记为 1 年
- XIRR 现金流折现：沿用独立的 daily year-fraction policy
- Daily volatility / tracking error：使用实际有效收益观察密度推断 `periods_per_year`

累计测量期短于一年时：

- 可以展示 period TWR / period MWR；
- 不得发布 annualized TWR、annualized MWR / XIRR、Calmar 等依赖年化收益的指标；
- API 必须返回 `null / unavailable` 及原因，不能只依赖前端隐藏；
- 满一年门槛按 calendar anniversary 判定，并在 API 与 UI 共用同一 eligibility；不得因平年只有 365 天而把同日到次年同日误判为不足一年。

若观察频率是稳定交易日频，`periods_per_year` 通常接近 `252`；若存在节假日、缺价或非交易日 carry-forward，系统必须记录并使用实际有效收益观察密度，避免把无市场观察的 0 return 当作风险样本。

协方差 / 相关性计算必须先构造 active return matrix。每个 return observation 必须同时携带 `start_date` 与作为 period end 的 `date`；同一矩阵行不只要 end date 相同，还必须代表完全相同的起止期间。默认 `strict` missing-return policy 要求矩阵中的每个收益日期对所有 active series 都完整；任何单成员缺失或 period identity 不一致都进入 coverage / missing 诊断，不得被补成 0 return，也不得用 pairwise dates 拼出一个每个 entry 样本不同的 covariance matrix。`sample_covariance` 使用完整对齐样本的样本协方差估计量，分母为 `n - 1`，且至少需要两个完整 return observations；不得用总体协方差 `n` 分母作为普通样本风险估计。年化必须使用完整样本 index 的实际有效收益观察密度。Research 的 `ewma_vol_shrinkage_corr_covariance` 也必须在 missing-return policy 处理后的完整窗口上估计 EWMA volatility 与 shrinkage correlation。

### 2.6.1 计算频率与节假日

Holdings / Risk / Research 中所有 forward-looking covariance / risk contribution 计算共享组合级 `Production Risk Model`。该模型存储在 `portfolio_record.risk_policy_json`，包括 covariance model、lookback days、calculation frequency、missing-return policy 和 contribution mode。Production risk window 只允许 `1M / 3M / 6M / 12M / 24M`，API 枚举值分别为 `30 / 90 / 180 / 366 / 730`，但窗口边界必须从请求的 as-of date 回看 `1 / 3 / 6 / 12 / 24` 个自然月，不能按固定日数或交易日数量相减，也不能把窗口锚到最新一条较早的收益观察。起止日期都是 EOD boundary，收益行按 `(window_start EOD, as_of EOD]` 选取：结束于 window start 当天的收益属于前一区间，不得计入；若 window start 是非交易日，首条合法收益可以从该边界之前最近一个有效交易日收盘开始，并结束于边界后的首个有效交易日。它是 forward RC、风险预算偏离、risk-budget solve 和 target-volatility overlay 的唯一生产风险模型来源；Research settings 中保留的 lookback/frequency 字段只作为 run setup 表单输入，不得成为第二套风险模型来源。Performance 中基于实际历史组合路径的 realized attribution 仍然独立保留，不和 forward RC 混用。

Risk 与 Research 的 covariance / correlation / risk contribution 固定使用日频组合路径：

- 组合计算频率只有 `daily`，不是用户设置项。Registry `source_settings.expected_frequency` 只保留 `daily` 与 `event_driven`，不能把组合降级成周频或月频。
- 对齐规则：在所有成员有效观测日期的并集上，未发布新值的成员沿用最近有效 mark；来源更新日一次性确认自上次发布以来的变化。所有来源均无新观测的日期不进入风险样本。
- 节假日规则：共同非交易日不生成样本；预期应更新却缺失的数据必须由 `risk_basis` 标记 incomplete，不能用 carry-forward 掩盖数据缺口。
- Missing-return policy：默认 `strict`。共同起点之前的缺失、无法取得初值或预期更新缺口均使风险/研究样本不可解。`strict` 与 `complete_case_drop` 都必须校验 latest complete row 的日频尾部新鲜度 `5` 天；结果必须暴露 rows before / after、missing rows、latest complete date 与 trailing staleness。
- 日频 instrument 配置 `market_calendar` 时，以该日历校验 holiday vs missing：共同非交易日不生成样本；任何日历交易日缺价都进入 coverage / missing 诊断。未配置或日历无法解析时使用保守的日历日 gap threshold，不隐式填值。
- Portfolio Risk 页的 `risk_basis` 来自 Holdings workspace，是当前 `risk_eligible=true` 非现金 modeled sleeve 的来源覆盖元数据，不是一个风险指标。它必须能覆盖全部 eligible members，并且 `resolved_frequency` 恒为 `daily`；缺失、非法或来源更新不完整时，Risk 页面和 Holdings Forward RC 都进入 basis unavailable / incomplete。
- Holdings `Forward RC` 和 Risk 页 Current Drift 使用 total-NAV 当前权重：eligible exposure 按 `signed market_value_base / total NAV` 形成权重，衍生品和 base-currency cash 不进入 covariance，但作为 0-return capital 留在分母中。Forward covariance 必须先在全部 eligible leaf instruments 上估计一次；taxonomy / sleeve 的 current risk share 只能把这些 leaf contribution 按同一个 total-portfolio variance 分母相加，禁止先合成 sleeve return、再对 sleeve 重新做 shrinkage covariance。输出同时披露 modeled/excluded exposure、cash 与 coverage。真实成立以来/真实持仓期间的 realized attribution 留在 Performance `Calculation`。Risk 页不再提供单独的 point-in-time Risk Contribution 表；风险预算偏离只在 Current Drift 中展示。
- Risk 页 Rolling Risk 是窗口内 sample volatility / sample Sharpe 展示层，只受 rolling lookback、calculation frequency 和 coverage 影响；不提供 EWMA / shrinkage 等 covariance model 选择。当前权重篮子同样要求所有 active members 在共同 inception 之后具有完全一致的 dates 与 period identity；不能先取交集静默丢掉缺失日再计算。凡是用于 risk-budget drift、rebalance trigger、Research solve 或 Holdings Forward RC 的 RC 相关指标，必须使用组合级 `Production Risk Model`，不能在不同页面各自硬编码 decay、shrinkage、lookback 或 contribution mode。
- Risk 页 Correlation Matrix 是窗口内 sample correlation 展示层，只受 lookback、calculation frequency、coverage 和 scope 影响；不提供 EWMA、vol shrinkage 或 correlation shrinkage 方法选择。EWMA / shrinkage 是 forward covariance 估计模型，应保留在 Production Risk Model 驱动的 Current Drift risk gap、Research solve 和 Holdings Forward RC 中。
- Risk 页 instrument-scope planning taxonomy 的 Current Drift 用 Securities、Derivatives 与 Accounts workspace 的 `derived_cash_balance_base + pending_settlement_base` 合成当前 NAV；deposit 与 securities account 的现金、待交收都必须计入，证券账户的 position market value 只通过 Holdings 进入一次。Securities 按 taxonomy 顶层节点汇总；Derivatives 固定汇总到 `derivative_bucket:__derivatives__`，现金与待交收固定汇总到 `cash_bucket:__cash__`，两者都不读取 taxonomy assignment。Holdings 中为对账而展示的 settled-cash 与 pending-monetary rows 不得再次计入。
- Base-currency cash 和事件记账型衍生品资本在组合风险聚合中按 0 return 处理；衍生品行自身没有可观测波动率或 Forward RC，仍显示 `N/A / excluded`。Non-base cash 需要 FX total-return series；非本币资产的本地币种 total return 也不能直接与 base-currency returns 拼接。在显式产出 base-currency total-return series 之前，Forward RC、Rolling Risk 与 Correlation 都必须 fail closed。若组合没有任何可建模市场资产，组合风险为 unavailable，不把纯现金/衍生品组合报告成实际波动率 0。
- Risk benchmark 只有在 return semantics 已确认为 `total_return` 或 `price_return` 且 currency 与 portfolio base currency 一致时才参与 rolling comparison。`price_return` 可以作为探索性比较，但必须提示分红口径差异；unknown semantics、raw-currency 或 unavailable coverage 不输出相对风险曲线。
- Risk Health 的基础监控包括 Production Forward Volatility 及 coverage、SAA/TAA risk-budget absolute gap、非现金 gross-normalized Top-3/HHI、以及 Accounts cash + pending settlement。任何组成字段缺失时对应指标显示 unavailable，不得把缺失金额或权重当成 0。
- Taxonomy 页 instrument-scope planning taxonomy 的当前覆盖视图使用同一口径：只有 Securities 接受 taxonomy assignment；Derivatives 与 Cash 是不可移动、不可分类的固定系统桶。三类资产共同进入根行 `Actual Weight` 分母，根行必须约等于 100%；`Without Classification` 只表示尚未分类的 Securities，不包含衍生品或现金。
- Risk 页 rolling metrics 与 correlation matrix 必须进一步校验 lookback window 覆盖率：按 resolved frequency 使用最小收益样本数、窗口起点最大偏离和至少 80% elapsed-day 覆盖。覆盖不足时结果为 insufficient-history / unavailable，不用更短窗口、pairwise dates、0 return 或前向填充替代。

### 2.7 缺失数据与覆盖率

如果某一估值日存在缺失数据：

- 价格缺失时，状态型 NAV / holdings 可以使用最近可用价格维持账面连续性，但必须记录 `stale_price_flag`，且该日不得作为正常市场收益观察进入 return/risk 样本；
- benchmark 缺失时，benchmark-relative 指标只在重叠日期上计算，但不影响绝对口径 snapshot 的 `complete` 状态；
- 若重叠覆盖率低于配置阈值，结果标记为 `partial` 或 `unavailable`；
- 风控和 period analytics 页面必须显示 coverage ratio，前端不得把缺失数据伪装成正常结果。

Coverage 必须按职责拆分，至少返回：

- `valuation_coverage_state`：fair-value NAV 所需的价格、FX 与现金是否完整；
- `return_coverage_state`：可靠估值边界与可链接 daily return 是否完整；
- `book_pnl_coverage_state`：cost basis、lots 与账面 P&L 是否完整；
- `attribution_coverage_state`：贡献拆分和分组解释是否完整。

这四个状态不得互相替代。成本或归因不完整不能阻断已经完整的 fair-value NAV/TWR；反之，完整账面成本也不能修补缺失估值或收益链。

Return chain 只链接连续且 `return_coverage_state = complete` 的可靠观察。显式 close-to-close 区间把起始 EOD 仅作为 growth index 的 0% anchor，第一条可链接收益必须晚于起始边界。若不可靠估值 gap 内发生 external flow，gap 后第一个可靠点必须作为新 anchor，其当日不发布跨 gap 的 bridging return；后续从该 anchor 继续链接。月度、MTD、QTD、YTD 等闭合区间只有在可靠起点、可靠终点和中间连续覆盖同时满足时才可发布完整收益；inception 落在期间内部只能是 partial / unavailable，不能伪装成完整自然期间收益。Performance 明细页的滚动或手动区间若请求起点早于组合 inception，则必须保留 requested start，同时把 effective start 明确收敛到 inception，并按该有效区间发布结果；不得把成立前尚不存在的日期误报为 return coverage gap。此规则不改变前述自然月/季度/年度闭合区间的完整性要求。

用户请求的终点晚于 latest reliable endpoint 时，后端应 clamp 到该 endpoint，并同时返回 requested/effective as-of 与 `as_of_clamp_reason`。summary、chart、drawdown、calendar 和 Calculation 必须使用同一 effective window。

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
- `PendingSettlementNet_t^{base}` 包括两类 monetary exposure：头寸已确认而 cash leg 尚未到 `settlement_date` 的证券结算应收 / 应付款，以及 cash 已结算而头寸尚未到 `position_effective_date` 的 `position_recognition_bridge`。前者在 settlement 当日转入 `SettledCash_t^{base}`；后者在确认日转为真实份额/头寸。两类都必须在有效区间内继续留在 NAV 中；
- 非基准货币的 `PendingSettlementNet` 是独立 monetary exposure。结算前的汇率重估必须记为 `PendingSettlementCurrencyGain`，与 settled cash FX、instrument FX 和资产 capital gain 分列；不得把待结算应收 / 应付的 FX 变动吸收到资产的 realized / unrealized capital gain；
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

### 3.4 Transaction facts、日期与审计

Transaction 是可修改的业务事实，但修改必须保留可追溯性，并与 ledger 派生结果分层：

- `transaction_economic_date` 对 position-changing 事实取 `position_effective_date`（缺省回退 `trade_date`）；dividend / coupon 有合法 `entitlement_date` 时在 entitlement date 确认经济收益；
- deposit / withdrawal 的 `external_flow_date` 是响应层派生字段，固定取 `settlement_date`，缺失时回退 `trade_date`。TWR 必须在该实际收付日中性化 external flow；
- 证券头寸 posting 在 position effective date 生效，结算现金 posting 在 settlement date 生效；当 `position_effective_date <= settlement_date` 时，两者之间的 pending settlement 作为独立 monetary exposure 留在 NAV；
- `trade_date` 是成交/定价事实，`position_effective_date` 是 EOD 持仓确认事实，`settlement_date` 是现金结算事实，`entitlement_date` 是收入权利事实；四者不得因界面或计算方便互相覆盖；
- `trade_time` 是可选事实。已知开盘前、盘中或收盘后成交时必须录入真实时间；未知时客户端提交 `null`，服务端用配置的日内默认时点形成确定性排序，并显式保存 `trade_time_is_estimated = true`。客户端不得把默认时点预填成看似精确的用户输入，修改既有 estimated 记录时也不得静默改成精确时间；
- `fee_category` 必须显式保存；无法分类的历史或导入事实使用 `unknown`，不得猜测 management / custody / transaction cost；
- create / update / delete 必须写入 additive change log，记录 before / after、row version、时间与可用的 idempotency key；这不是 event-sourcing ledger replacement；
- 同一 portfolio 的 idempotency key 只可重放同一 operation 与同一 request hash；同 key 不同 payload 必须冲突失败；
- 交互式 create / internal transfer 必须生成 request-scoped idempotency key，并在请求完成前锁定重复提交；update / delete 的 `row_version` 是必填 optimistic-concurrency 前置条件，缺失或不匹配时都必须拒绝覆盖较新的事实；
- 源 `quantity / price / amount / fx / fees / taxes` 使用 practical NUMERIC 精度保存，同时保留 float64 projection 供现有计算与统计使用。不得用显示舍入值反写源事实。
- 对 buy / sell 等证券成交，`quantity` 与 `gross_amount` 是份额和成交金额事实，lot 的隐含成交价按 `gross_amount / quantity / price_scale` 计算；输入 `price` 可以是该隐含价格四位小数的展示值。校验可以接受精确乘积或与隐含价格四位小数一致的展示价，但账本不得反过来用舍入后的 `price × quantity` 改写源金额。
- 对 pooled fund 申购 / 赎回，成交确认单的 `quantity` 与 `gross_amount` 是 authoritative facts，录入界面必须由二者按 canonical `price_scale` 反算 source `price`；Registry 的当日 NAV 只作参考，不得自动覆盖确认金额、确认份额或反算价格。ETF 与股票仍以实际 execution price + quantity 为正常录入锚点。

### 3.5 Cost Basis / Purchase Value

成本法是账面成本、realized capital gain 和税务/会计解释口径，不是组合绩效收益口径。
Portfolio 级 TWR、IRR、drawdown 和 contribution 必须基于 fair value、cash flow 与 P&L 事件计算，不得因为账户从 FIFO 改成 moving average 而改变组合级 return。

当前实现支持账户级成本法：

- 新建 `securities_account` 未显式选择时默认 `FIFO`；
- `FIFO` 保留真实 open lots，并按交易时间顺序释放成本；
- `moving_average` 对每个 `account + instrument` 保留一个滚动平均成本 bucket；API 仍输出一个 synthetic position lot 以支持 UI、转仓链路和审计引用；
- 修改账户成本法会从交易事实重新推导 holdings、lots、ledger postings 与 snapshots，不保留“历史旧算法”的兼容分支。

对导入边界上的 opening positions：

- `opening_balance + position` 是 bootstrap event，但成本法上必须 materialize 为成本单元；
- `FIFO` 下，一个 opening lot 表示一个后续可被 FIFO 消耗的成本单元；
- `moving_average` 下，opening position 进入该账户该资产的 rolling average bucket；
- `gross_amount` 表示 imported remaining cost basis，可以为 0；0 成本持仓仍然是合法持仓，后续卖出或内部转仓不得被误判为缺失成本。

### 3.5.1 Direct bond boundary

直接债券当前不进入 Instrument Registry、Watchlist 或 Portfolio 交易模型，因此没有债券行情、percent-of-par 价格、票息、到期兑付、持仓估值或收益计算路径。债券公募/私募与债券 ETF 仍分别按 `public_fund / private_fund / etf` 普通证券处理。未来若需要直接债券，应另行设计 Portfolio-local 事件记账合同，不能把已删除的 Registry 债券模型恢复为兼容分支。

#### Open position book cost

对任意当前头寸，`Cost Basis` / `Purchase Value` 定义为 open-position book cost：

- 当前仍未 disposed / transferred out 的 remaining cost basis；
- 包含已资本化到 buy/opening/reinvestment 成本的费用和税费；
- 不包含已经 realized 或 transferred out 的部分；
- 在 `FIFO` 下等于 open FIFO lots 的 remaining cost basis 之和；
- 在 `moving_average` 下等于 rolling average bucket 的 remaining cost basis。

`Book Avg Cost` 定义为：

$$
AvgCost^{book}_i = \frac{RemainingCostBasis_i}{RemainingQuantity_i}
$$

它不是某一笔交易的 purchase price。真实交易价格在 lot 层用 `entry_price = entry_gross_amount / entry_quantity` 表示，且不包含资本化费用和税费；`entry_cost_per_unit` 才包含资本化费用和税费。`moving_average` 的 synthetic lot 没有真实 tax-lot purchase price，展示时应优先使用当前 `Book Avg Cost`。

Holdings 是 as-of balance sheet view，展示当前仍然 open 的正式 position、written option obligation、settled cash 及用于把 NAV 对平的 pending monetary balance。只有正式 position 才有证券 quantity、open-position cost basis 与 unrealized P&L；option obligation 是负债 read model，pending monetary row 也不是基金/证券持仓。资产级 TWR、period contribution、realized gain、dividend / coupon income、fees / taxes impact 和 closed positions 属于 `Performance` / security detail 的区间绩效视图，不进入 Holdings 默认列，也不作为 Holdings 的 canonical 语义。

事件型衍生品使用以下独立估值边界，不能伪装成普通 market quote：

- FCN 和长期权在没有可靠公允价值时按 remaining transaction basis carried，使用 `holding_kind=position`、`valuation_basis=carried_cost`、`fair_value=null`、空 quote identity 和 `quote_status=event-cost`；
- event-valued purchase 的 fees / taxes 在交易日确认为 expense，不进入 carrying basis；
- Call / Put 的 sell-to-open 同时增加 settlement cash 和等额 premium-basis liability；short-option row 使用 `holding_kind=option_obligation`、`valuation_basis=premium_liability`、负的 NAV amount，并披露 open contract count、`contract count × multiplier` 的标的数量、remaining premium basis 和 carrying liability。风险读模型可汇总同一组合的现有标的股票或同币种 settled cash，显示 portfolio-level backing ratio/shortfall；该指标不分配具体担保物，也不声明券商保证金或质押状态；
- partial/full buy-to-close、writer expiry、writer cash settlement 和 assignment 按关闭的合约数量比例释放 premium basis。释放 basis 减去 close cost 与 charges 形成 realized option P&L；
- FCN close 独立记录正常到期、敲入或敲出结果，按关闭金额释放 carrying basis。若敲入后接收股票，人工另录一笔普通股票 `buy`；FCN 和股票记录不绑定，也不自动转移成本；
- option contract 不预设结算方式。long/writer 到期作废使用零现金 expiry；有现金结果使用 cash settlement。经人工确认的实物行权或指派由专用命令原子生成两条事实：期权腿以零现金关闭并释放 remaining premium/cost basis，股票腿严格按合约 strike、multiplier 和 Call/Put 方向买入或卖出；费用和税费只记在股票腿。`option_delivery_link` 保存严格一对一关系，任一腿不能单独修改或删除。期权 premium P&L 与股票自身 cost/realized P&L 保持分开，详情页再按 underlying 汇总展示；不同币种的合约与 underlying 不推断实物交割，只能录入经核对的现金结算。FCN 交付仍不建立关联。

只要期间内存在 material event-valued asset、writer liability 或 derivative lifecycle activity，账本仍完整发布用于 NAV reconciliation 的 flow-neutral operational/carrying-basis daily return，并明确标记为 `Total Portfolio Operational Return`；它不是完整 fair-value 或 GIPS-informed return。所有波动率、downside volatility、Sharpe、Sortino、Calmar、风险 drawdown、benchmark risk compare 和 realized risk attribution 统一消费独立的 `Market Risk Return`，不得消费 operational return。FCN、长期权和 writer obligation 是 system-level derivative tracking scope：Taxonomy assignment 不能把它们重新纳入 Research universe、target solve、covariance、Risk Budget 或 point-in-time backtest；它们未分配 planning taxonomy 时也不得阻断普通证券 Research。

`Market Risk Return` 与 operational return 共用总 NAV 分母，但使用独立风险 P&L 分子。令 `ExcludedPnl_t` 为截至 t 的累计非市场风险 P&L，则：

$$
MarketRiskPnl_t = Delta_t - (ExcludedPnl_t - ExcludedPnl_{t-1})
$$

$$
MarketRiskReturn_t = \frac{MarketRiskPnl_t}{NAV_{t-1} + ExternalCashIn_t}
$$

`ExcludedPnl` 包括衍生品生命周期已实现 P&L、FCN coupon、衍生品入场费用、衍生品独立费用/税费、现金利息和无资产归属的现金费用/税费；这些现金结果仍完整留在 NAV、operational return、Calculation 与账本中。普通证券 price/total-return 变化和普通证券 dividend/coupon 不剔除。Base-currency cash 与衍生品 capital 因而按 0 return 稀释风险；non-base cash 的 FX 变动仍是市场风险。缺少所需 FX、没有 fresh market/FX observation 或没有任何 modeled market asset 时风险链不可用。

分红与未实现收益必须严格分层：

- 普通 `dividend / coupon` 在 entitlement date 确认为已实现 `Income`，税费进入独立 expense/tax bucket；它不释放或冲减 open-position cost basis，也不进入 Holdings `Unrealized P&L`；
- `dividend_reinvestment` 同时确认原持仓的已实现 `Income`，并按 reinvested gross amount 形成新的 open lot cost；不得把新份额零成本入账，也不得把同一分红再记现金；
- 只有明确的 `return_of_capital` 才冲减当前 open lots 的 remaining cost basis。它不是 dividend income；超过剩余成本的事实在支持超额资本返还收益分类前必须失败关闭；
- 除息后的 valuation price / NAV 下降会降低持仓未实现资本利得，已确认的分红则在 `Income` 中抵补经济收益。二者可以共同解释总 P&L，但不得相互改写分类。

Holdings 的 monetary 部分按结算现金账户子账拆分：

- `settled_cash`：真正已经入账且可支配的现金。instrument identity 为 `cash:{currency}`，持仓行 identity 为 `cash:{currency}:{account_id}`；只有这一层进入可用现金金额/比例；
- `restricted_cash`：法律上仍属现金但已冻结、质押或受限，必须有显式 restriction fact 才能确认；当前系统不得仅因存在一笔未完成交易就自行猜测为冻结现金；
- `pending_subscription`：现金已经划出而份额尚未在 EOD 持仓生效的申购结算应收；
- `settlement_receivable`：资产已按 position-effective boundary 减少但出售款尚未到账；
- `settlement_payable`：资产已按 position-effective boundary 增加但购买款尚未支付；
- pending monetary row 使用 `instrument_id = pending:{kind}:{cash_account}:{economic_instrument}:{currency}:{settlement_date}:{pending_until_date}`，归属于结算现金账户，并保留 `economic_instrument_id` 与 `transaction_ids` 以便穿透追踪；结算日或头寸待确认日缺失时对应日期段为 `undated`。日期属于 canonical identity，同账户、同资产、同币种但不同结算边界的余额不得合并或主键冲突；它不是对应资产账户的一笔虚构 position；
- monetary row 的 `market_value` 等于该币种余额，`market_value_base` 等于按 as-of date FX 转成组合 base currency 后的值；
- settled 与 pending monetary row 都不具有证券 `cost_basis`、`Book Avg Cost` 或证券未实现 P&L；但两者都维护独立的 `FX Cost Basis`，用于解释货币余额的未实现汇兑损益，不能把这项 monetary basis 冒充证券 book cost；
- monetary basis 从该价值第一次进入账本的 `monetary_recognition_date` 建立：普通买卖取现金/头寸价值首次生效日，dividend / coupon 取 entitlement date，现金先结算而头寸后确认时取 bridge 起始日。应收、应付或 subscription bridge 转成 settled cash 时必须原样继承该 basis，不能在 settlement date 重新按现汇定价；
- settled cash 增加敞口时以上述 historical base basis 并入账户内移动平均，减少敞口时按该平均 basis 释放；同币种内部账户划转继承来源 basis，不以划转日 FX 重置。真正的 `fx_conversion` 以成交两边的实际 countervalue 建立目标币种 basis：若一边就是 portfolio base currency，直接使用成交隐含汇率；若两边都不是 base currency，则以成交日 source/base FX 换算实际 source consideration。`monetary unrealized FX P&L = current base value - historical monetary basis`；历史或当前 FX 缺失时失败关闭；
- base-currency cash 的 instrument return、day return 和 volatility 为 `0`；
- non-base cash 的 instrument return / day return 来自该现金币种兑 base currency 的 FX series；
- pending monetary balance 不属于 settled cash、没有行级 instrument total-return series、不得进入资产协方差矩阵；其 FX 重估仍按 `PendingSettlementCurrencyGain` 单独入账，settlement 只改变余额状态，不产生第二次汇兑损益。

Holdings 使用同一份 as-of workspace 和 canonical NAV，按语义定义四个表面。Securities、FCN、Options、Cash & Settlement 只在对应 rows 非空时显示；全部为空时只使用一个统一 Holdings 空状态。组合 headline 已提供 NAV 等总览，分类构成和 `Portfolio Total` 统一由 Overview `Asset Mix` 展示，Holdings 不重复第二套总计。Securities、FCN 与 Options 使用一致的 `View`、`Columns` 控件，Securities 另有 `Group By`；字段固定的 Cash & Settlement 不提供 View / Columns。instrument 数量紧邻表标题。

| 表面 | 行类型 | 主要字段 |
| --- | --- | --- |
| `Securities` | 股票、基金、ETF 等 Registry 普通资产 | quantity、quote、base position value、book/economic cost、price/FX/total unrealized、instrument total return/risk、Forward RC；可独立切换视图与字段，也是唯一允许 Group By 的表 |
| `FCN` | FCN contract rows | 独立视图与字段；显式展示 contract/account/underlying terms、notional、coupon、maturity/events、remaining basis、signed NAV amount、historical carrying basis、carrying FX translation、portfolio weight、lifecycle/valuation status，以及 underlying 当前价格相对 initial/strike/KI/KO 的位置；当前价格不得冒充已发生 barrier event |
| `Options` | long/short Call、long/short Put | 独立视图与字段；显式展示 side/type/underlying、underlying spot、moneyness、expiry、strike、contracts/multiplier、remaining basis、signed NAV amount、historical carrying basis、carrying FX translation、strike notional、portfolio-level backing、risk state、portfolio weight、lifecycle/valuation status |
| `Cash & Settlement` | settled cash 与 pending monetary rows | 固定字段；展示 currency/account/availability、local/base amount、FX cost basis、unrealized FX P&L、portfolio weight，以及 settlement/pending dates 与 status |

Short-option row 的 `required_underlying_quantity = open_contract_quantity × contract_multiplier`，`strike_notional = strike × required_underlying_quantity`。written Call 的 backing 只比较组合中现有 underlying shares 与全部同标的 open written Call 的 required quantity；written Put 只比较该币种 settled cash 与全部同币种 open written Put 的 strike notional。它是组合层即时风险提示，不把股票或现金分配给具体合约，也不等同于券商 collateral。Operational summary 聚合 open contract count、`expired_or_due / next_7_days / next_30_days / next_90_days / later / unknown` 到期桶、option-obligation strike notional，以及 pending settlement 的 receivable、payable、net、最早结算日、逾期数和无法换算 base currency 的行数。Alerts 覆盖到期已到/七日内、逾期结算和 settlement FX unavailable，并返回真实 `related_line_ids`。这些 API 字段保留给生命周期和结算逻辑，Holdings 不单独渲染 `Operational Status` 面板；组合任一页面打开时，已过期但仍有 open long/writer quantity 的期权会出现一次简洁确认弹窗，用户确认作废、现金结算或实物行权/指派后直接入账，也可稍后从页头小入口重新打开。

期权 `moneyness` 使用以 strike 为分母的有符号距离：Call 为 `(spot - strike) / strike`，Put 为 `(strike - spot) / strike`；严格大于 0 才是 in the money，等于 0 是 at the money。它只描述当前 underlying 与 strike 的关系，不是期权收益率或 fair value。

`holding_category` 不是用户可选的 Group By 字段。`Group By` 由底层限定为只在 Securities 内部生成二级分组；FCN、Options 与 Cash & Settlement 不参与 taxonomy 或属性分组。Securities 的 Taxonomy / Taxonomy Leaf 使用当前默认 planning taxonomy 与当前 active assignment；它是管理分类，不随 Holdings `as_of_date` 回放历史版本。风险 eligibility、Research 和 materialized calculation identity 仍按各自的 effective-dated analytics scope 合同处理，不能与这里的展示标签混为一体。

Holdings 表格只有 instrument 或 contract 名称承担详情导航；普通字段单元格不绑定整行跳转，并支持按住鼠标左右拖动横向浏览宽表。

Holdings CSV/XLSX 只为非空的 `Securities`、`FCN`、`Options`、`Cash & Settlement` 输出独立 block；不使用统一 `Category` schema，也不重复导出组合总计。Securities、FCN 与 Options 跟随各自当前视图，Cash & Settlement 使用固定字段；Securities 额外跟随筛选、排序和可选 Group。市场收益、图表、未实现盈亏和回撤的不适用值写 `N/A`；衍生品的 Vol / Forward RC 同样写 `N/A`，只有明确 modeled-zero 的 monetary risk 写数值 0。不得用 carrying/liability amount 填充 fair-value 字段。

Analytics scope 是独立于 taxonomy node 名称的 effective-dated policy。每条 policy 明确 `risk_eligible`、`risk_budget_eligible`、`performance_scope`、`valuation_basis` 和 exclusion reason；`risk_budget_eligible=true` 必须同时满足 `risk_eligible=true`。`performance_scope` 只允许 `ordinary / derivative_lifecycle / operational_only / unallocated`。Instrument row、transaction cash activity 和 materialized calculation identity 都必须携带 as-of 解析出的 policy/configuration/selection version；衍生品相关 cash leg 继承 originating instrument 的 performance scope，不得自动落入 ordinary sleeve。

当前实现只发布 scope disclosure 和 `cash_scope_breakdown`，不发布 ordinary-sleeve TWR。`cash_scope_breakdown` 按 `performance_scope + transaction currency` 分桶，金额是对应交易币种的 local cash activity；不同币种不得直接相加，也不得冒充 base-currency cash flow。`ordinary_sleeve_twr_status` 固定为 `unavailable`，原因是尚未维护可逐日对账的 sleeve cash subledger；不得从 total operational return 中删除 derivative rows 后伪造 ordinary TWR。Total operational/carrying-basis return、完整 fair-value performance 和 scoped risk 是三个不同对象。

`market_value_base`（UI：`Position Value (Base)`）对 `valuation_basis=market_quote` 的正式资产表示 base-currency fair value，等于 `quantity * selected valuation quote` 再按 as-of date FX 转换；对 settled cash 与 pending monetary balance，它表示对应 monetary balance 的 base-currency value。对 `carried_cost` 和 `premium_liability` 行，该字段只是保持 NAV 加总合同的 signed operational amount，真实 basis 必须分别从 `carrying_value(_base)` 或 `liability_value(_base)` 读取，`fair_value` 必须为空。全部 workspace rows 的 signed amount 必须与同日 canonical NAV 对平；这个领域不变量不依赖 Holdings 是否展示总计行。

Holdings 行级 `Weight = market_value_base / portfolio NAV`，所以 written liability 使用负权重。在估值与 FX 完整时，正式资产、event-valued assets、written liabilities、settled cash 与 pending monetary rows 的 signed weights 合计必须为 100%。pending monetary balance、event-valued asset 和 option obligation 都没有可用 instrument return series：Return、Chart、Unrealized P&L 和 Drawdown 必须为 `N/A`。衍生品的 Vol 与 Forward RC 也必须为 `N/A` 并标记为 excluded；base-currency cash / pending monetary row 才可以按明确 monetary 口径显示风险 0。

普通证券同时保留会计成本和经济回本口径：

- `Book Cost (Local)` 是当前开放 lots / moving-average bucket 的 remaining accounting cost。普通分红不冲减 book cost，只有明确的 `return_of_capital` 冲减；
- 每个 remaining cost slice 必须保留 acquisition date 与 remaining local cost。FIFO 释放实际 lot 来源；moving average 的部分卖出按各来源剩余成本比例释放，不能因合并 bucket 丢掉历史 FX 来源；
- `Book Cost (Base, Current FX)` 按 as-of FX 换算 local book cost；`Book Cost (Base, Trade FX)` 对每个 remaining cost slice 使用 acquisition-date FX 后求和。缺少任一历史 FX 时，historical base cost、FX P&L 与 total base unrealized 全部为空，不得用 current FX 代替；
- `Net Invested (Local)` 在当前连续持仓周期内以买入总成本和费用为正，以部分卖出的净回款、已实现 dividend/coupon 和资本返还为负；完全平仓后再次买入会开始新周期。`Break-even Price = Net Invested / current quantity`；
- `Price P&L (Local) = MV_local - BookCost_local`；`Price P&L (Base) = PricePnl_local × FX_current`；`FX P&L (Base) = BookCost_local × FX_current - BookCost_historical_base`；`Total Unrealized P&L (Base) = Price P&L (Base) + FX P&L (Base) = MV_base - BookCost_historical_base`；
- `Price Return (Local)` 以 local book cost 为分母；`Total Unrealized Return (Base)` 以 historical base book cost 为分母。零 book-cost 头寸仍保留金额盈亏，但百分比不因零分母而伪造。

Holdings read model 仍计算 as-of date 当前持仓规模的一天经济市场变动，供组合 headline、快照和其他内部消费者使用；Security 表不再把 Day P&L / Day Return 作为可选字段：

- 非现金资产优先使用 `quote_selection_policy.total_return` 选出的当前点与上一可用同 basis 点；只有 total-return basis 不可用时才回退到 selected valuation basis。这样已确认的拆分、分配或分红不会被误判为单日价格暴跌；
- `local_day_return = current_return_point / previous_return_point - 1`；本币市场价值变化先形成 `local_day_change_value`。组合币种总变动必须使用当前与上一估值日各自的 FX：`day_change_value_base = current_local_value × current_fx - previous_local_value × previous_fx`，不能把两端都按当前汇率换算；
- Holdings read model 同时输出 `local_day_change_value_base = (current_local_value - previous_local_value) × current_fx` 与 `fx_day_change_value_base = previous_local_value × (current_fx - previous_fx)`，两者必须精确加总为 `day_change_value_base`。当前/上一 FX rate、rate date 与 source instrument ids 随行保留；
- total-return basis 中包含的分配只用于描述当前持仓篮子的单日经济市场收益，不改变 dividend 的已实现 `Income` 分类，也不进入 book `Unrealized P&L`；
- `carried_cost` 和 `premium_liability` 行的 day change / day return 必须为 `null`，即使旧 payload 或下游聚合传入数值零，UI 与 export 也必须显示 `N/A`；
- base-currency cash 的 day change 为 `0`；
- non-base cash 的 day return 使用当前 FX 与上一估值日 FX，local P&L 为 0，`fx_day_change_value_base = day_change_value_base = cash_amount × (current_fx - previous_fx)`；
- 若缺少当前 valuation point、可比 return point、上一点或 FX，相关字段必须为空，不得用 0 或 chart sample 补齐。可见 `Quote` / `Quote Date` 仍只描述 valuation point，不能拿 chart 日期补成 quote 日期。

Holdings 可以展示 quote-derived instrument market trend 指标，作为扫描当前持仓标的自身近期市场表现的辅助列：

- `Chart 1M / 3M / 6M / 1Y` 是前端展示用的 sampled path；
- `1W / 1M / 3M / 6M / MTD / YTD / 1Y Total Return` 只使用标的自身经 Registry 明确确认的本币 total-return series，计算为 `latest_total_return_level / anchor_total_return_level - 1`；它包含分红等复权，不包含组合基准币种 FX；不得用 `official_nav`、普通 `close / last` 或 valuation price 冒充 total return；
- total-return series 严格保持 `quote_selection_policy` 的顺序和单一 basis。优先 basis 有数据但历史不足时返回空值，不得因为次选价格序列更长就切换口径；Portfolio 与 Watchlist 各自实现读路径，但在相同 instrument、as-of date、total-return basis 和窗口边界下数值必须一致；
- `Chart *` 可以展示策略允许的 price-return 或 total-return path，但必须保留真实 return semantics；图表序列不得反向充当上述 Return、Volatility 或 Drawdown 字段的替代输入；
- `1W / 1M / 3M / 6M / 1Y Total Return` 的窗口目标日期从请求的 as-of date 回看，anchor quote 是该目标日期或之前最近 quote；终点值则是 as-of date 或之前的最新可用 total-return point。即使终点点位略早于 as-of，也不得把窗口锚点随之向前挪；月度目标日期按自然月回看，不按固定天数截断；
- `MTD` / `YTD` 的 anchor quote 是严格早于月初 / 年初的最近 quote；若该锚点不存在则返回空值，禁止拿期间内第一条 quote 冒充完整自然期间收益；
- `Current DD` 使用 confirmed total-return series，计算为 `latest_total_return_level / max_available_total_return_level_to_date - 1`；
- `Max DD` 使用同一序列，计算历史各点相对此前峰值的最小值 `min(level_t / running_peak_t - 1)`；
- `Held Max DD` 只在 instrument row 计算，起点是当前开放头寸中最早的 holding start date；它不纳入已经平掉的旧头寸；
- 普通证券的 `Vol 1M / 3M / 6M / 1Y` 使用同一 confirmed total-return series 的日频收益，再按实际 elapsed days 年化；不得使用 `Chart *` sampled points。衍生品显示 `N/A`，modeled-zero monetary row 显示 0；
- volatility 窗口必须有接近窗口起点的初始 quote、足够 elapsed-day 覆盖和最小收益样本数，否则为空。日频门槛为 `10 / 30 / 60 / 120`，分别对应 `1M / 3M / 6M / 1Y`；
- 这些指标不读取 quantity、cash flow、cost basis、FIFO / moving average、realized gain 或 income，因此不属于组合 TWR、holding contribution 或 book P&L。

Holdings `Forward RC` 是当前正式风险持仓的组合级 forward risk contribution：

- 只对 active formal `risk_eligible=true` ordinary positions 建立协方差矩阵；衍生品不参与，行级 `forward_risk_share`、contribution 和 modeled volatility 留空，状态为 `excluded`。base-currency cash/pending monetary rows 可以标记 `modeled_zero`；non-base monetary exposure 在没有 FX total-return series 时仍为 unavailable；
- `risk_eligible` 必须来自 as-of date 生效的 analytics taxonomy selection、taxonomy configuration revision 和独立 scope policy record。解析顺序为 assigned node exact policy、最近祖先、taxonomy root；`__unassigned__` 只解析自身，找不到 policy 时 fail closed。结果保留 policy/configuration/selection version，物化 identity 变化时必须重建；
- event-valued asset 或 derivative liability 的存在不阻断 eligible market sleeve 的 covariance。输出必须同时披露 `modeled_net_exposure`、`modeled_gross_exposure`、`excluded_carrying_value`、`excluded_liability`、`cash_unallocated_exposure`、coverage ratio 与逐行 `excluded_rows`；衍生品以 `N/A / excluded` 表达，不用 0 冒充风险判断；
- 权重使用 eligible row 的 signed base exposure 除以 total NAV；衍生品与 base-currency monetary rows 不进 covariance、收益视为 0。risk-share 分母是同一个 total-portfolio variance，不是 instrument 自身风险或 group-local denominator；
- covariance model、lookback、calculation frequency、missing-return policy 与 contribution mode 必须来自组合级 `Production Risk Model`；
- 窗口固定锚在请求的 holdings as-of date；较早的 latest observation 只能触发 trailing-staleness 诊断，不能把整个 lookback window 一起向前移动；
- 每个 leaf return 必须有合法且与其他成员一致的 period start/end；taxonomy group 的 Forward RC 只加总 leaf `forward_risk_share` 和 contribution，不重新估计 group covariance；
- 若没有任何 eligible risky holding，或任一 eligible member 缺少完整收益窗口、base-currency return、权重、共同 period identity 或正的组合 variance，Forward RC 进入 `unavailable`，不得把 policy-excluded rows 重新纳入，也不得用短窗口、0 return、pairwise covariance 或现金归一化兜底；
- Holdings 普通证券的 `Vol 1M / 3M / 6M / 1Y` 仍是标的自身 trailing sample volatility 观测列，不受 Production Risk Model 的 lookback 或 covariance model 影响，也不能替代 Forward RC；衍生品固定为 `N/A / excluded`。

Holdings group rows 不是后端 period-performance group：

- `Position Value (Base)`、两种 base book cost、base price/FX/total unrealized P&L、weight 与 open lots 等可加总绝对量按组内 rows 汇总；本币 position value、cost 和 P&L 不跨币种求和；
- Securities group/subtotal 的 `Total Unrealized Return (Base)` 使用组内 `sum(total unrealized P&L base) / sum(book cost at trade FX base)` 重算，不是成员百分比的平均；cash unrealized FX 只属于 Cash & Settlement，不混入证券未实现收益；
- Securities group 只要包含非零 event-valued asset，price/FX/total unrealized P&L、unrealized return 与当前权重 instrument return 均为 `N/A`。不得先剔除 event row 再汇总其余资产，也不得用 carrying amount 减 book basis 制造零未实现盈亏；
- `1W / 1M / 3M / 6M / MTD / YTD / 1Y Total Return` 使用 as-of date signed base value 权重合成，Securities group/subtotal 使用该组当前 base value 作分母。行级序列仍是标的本币 total return；未提供 FX overlay 时跨币种聚合不可用。覆盖不足时为空。它们是 theoretical current-basket diagnostics，不是历史实际组合 TWR，后者只属于 Overview / Performance；
- group/subtotal volatility / risk drawdown 用普通证券共同 period return series 与该组当前 base value 权重计算，不等于成员风险数值的简单加权平均；
- `Held Max DD` 不计算 group 或 subtotal：成员持有起点不同，截断长度不同的持有期序列没有可稳定解释的共同分组起点；
- group return / volatility / drawdown 只在每个有当前价值的非零收益成员都能解释为 portfolio base-currency return 时计算。本币 instrument return 不能因为成员恰好使用同一种外币就直接拼接；后端未提供逐期 base-currency overlay 时必须留空；
- Securities、FCN、Options、Cash & Settlement 是四个语义表面，不是可选 Group By 结果；无对应 rows 时不渲染。资产表 subtotal 由当前行实时聚合，只用于阅读，不是源事实，也不参与 sort 或 detail。用户分组只在 Securities 内部生成二级 subtotal。

每个 Holdings 字段的 group / subtotal 处理必须属于以下明确类别；未列为可聚合的字段一律留空：

| 类别 | 字段 | 分组规则 |
| --- | --- | --- |
| 点位绝对量 | `Position Value (Base)`、`Book Cost (Base, Current FX)`、`Book Cost (Base, Trade FX)`、`Weight`、`Open Lots`、`Price P&L (Base)`、`FX P&L (Base)`、`Total Unrealized P&L (Base)` | 对当前 rows 加总；含非零 event exposure 时未实现 P&L 为 N/A |
| 重新计算的比例 | `Total Unrealized Return (Base)` | 使用组级 `Total Unrealized P&L (Base) / Book Cost (Base, Trade FX)`；禁止平均成员百分比，含非零 event exposure 时为 N/A |
| 当前权重历史收益 | `1W / 1M / 3M / 6M / MTD / YTD / 1Y Total Return` | 用 as-of signed base value 权重合成；行级不含 FX，跨币种或覆盖不完整时为空 |
| 当前篮子路径风险 | `1M / 3M / 6M / 1Y Vol`、`Current DD`、`Max DD` | 用共同 period 的成员 total-return series 与当前权重先生成组 return path，再计算风险；return currency 不一致时为空 |
| 组合风险贡献 | `Forward RC` | workspace forward-risk status 完整时，对 eligible 成员相对于同一全组合 variance 的 risk share 加总；衍生品排除且为 N/A，modeled-zero monetary rows 贡献 0，纯 modeled-zero monetary group 为 0；其他成员缺失则为空 |
| 仅 instrument row | Instrument / Ticker / Instrument Type / Taxonomy / Taxonomy Leaf / Currency、`Holding Since`、`Quantity`、`Cost Method`、`Book Avg Cost`、`Book Cost (Local)`、`Weighted Cost FX`、`Net Invested (Local)`、`Break-even Price`、`Price P&L (Local)`、`Price Return (Local)`、Quote 及其日期/口径/provider/status、`Accounts`、`Chart *`、`Coverage`、`Held Max DD` | 不生成 group 或 subtotal 值 |

`Holding Since` 是当前开放头寸最早的 holding start date，不是 workspace as-of date；不同 instrument 的份额单位、报价单位、平均成本和起始日期不可直接相加或平均。

#### Unrealized P&L

$$
PricePnL_i^{local} = MV_i^{local} - BookCost_i^{local}
$$

$$
PricePnL_i^{base} = PricePnL_i^{local} \times FX_t
$$

$$
FXPnL_i^{base} = BookCost_i^{local} \times FX_t - BookCost_i^{historical\ base}
$$

$$
TotalUnrealizedPnL_i^{base} = PricePnL_i^{base} + FXPnL_i^{base}
= MV_i^{base} - BookCost_i^{historical\ base}
$$

#### Realized P&L

每次卖出时：

- `FIFO` 按最早未平的 lot 依次匹配；
- `moving_average` 按卖出时的 rolling average cost per unit 释放成本。

本币账面实现损益为：

$$
RealizedPnL^{local} = NetSaleProceeds^{local} - ReleasedBookCost^{local}
$$

换成组合基准币后，价格与汇率必须分开：

$$
RealizedPricePnL^{base} = RealizedPnL^{local} \times FX_{disposal}
$$

$$
RealizedPositionFXPnL^{base} = ReleasedBookCost^{local} \times FX_{disposal} - ReleasedHistoricalBookCost^{base}
$$

$$
RealizedPositionPnL^{base} = RealizedPricePnL^{base} + RealizedPositionFXPnL^{base}
$$

说明：

- realized P&L 是成本法相关的 book/tax P&L；
- `ReleasedHistoricalBookCost` 按被释放 lot 的每个剩余成本来源及其 historical FX 计算；FIFO、moving average、部分卖出和内部转仓都不得抹掉这些来源；
- 卖出形成的应收或现金按自己的 `monetary_recognition_date` 建立 monetary basis。它与头寸 realized P&L 是两条连续但不同的资产负债表链，后续汇率变化属于 receivable / cash FX，不得再次计入 position realized FX；
- complete fair-value TWR 不使用 realized P&L 作为收益率输入，因此成本法切换只重算 book capital gain / cost basis，不应改变 fair-value based return。若组合包含按 lot basis carried 的 event-valued position，相关结果属于 operational/carrying-basis accounting，不能借用这条规则声称其为 fair-value TWR。

### 3.5.2 Internal position transfer

对 `transfer_object_type = position` 的内部转仓，canonical 规则如下：

- 它不是卖出再买入，不得形成 realized P&L；
- source account 使用自己的成本法决定 transferred cost basis；
- `FIFO` source 按 open lots 顺序切分并搬迁 source lot metadata；
- `moving_average` source 按 rolling average cost 生成一个 average-cost transfer slice；
- destination account 再按自己的成本法接收：FIFO 生成 destination lot，moving average 合并到 destination bucket；
- 0 成本 lot 可以内部转仓，只要数量充足并且 source slice 存在；
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
- `share_split` 在 `effective_date` BOD 生效，ratio 定义为 `new_units / old_units`；账户总量先按公告规则处理碎股，再按 lot 比例分摊，不能逐 lot 截位。
- 拆分不改变账户总成本基础；旧 lot 关闭并以 lineage 连接到 carry-cost successor lot，单位成本按 ratio 反向变化。
- provider factor/价格连续性只能生成 `detected` 候选，不能入账；只有 issuer / exchange / CSD 确认事件才可形成数量 posting。
- 若登记日与生效日之间存在交易而系统没有 due-bill 事实，计算必须 fail closed；`cash_in_lieu` 没有金额/应收事实时也必须 fail closed。
- 原始 `close / official_nav` 用于交易校验与市值；`adjusted_close / total_return_nav` 用于 confirmed total-return Return / Risk。Chart 可以展示策略允许的 valuation、price-return 或 total-return path，但必须标明真实 semantics；Holdings Day P&L 优先使用完整 total-return pair，仅在其不可用时才使用完整 valuation pair，不能把两个 basis 混配。
- `dividend / coupon` 进入 `Events` 时，若已经入账，则必须引用对应 `Transaction`；不得在事件层再次形成独立 ledger posting。

## 4. FX 口径

### 4.1 基础换算

所有组合级价值对象先换算到 `base_currency` 再聚合。

Portfolio、account、transaction、ledger posting、position lot 与 market-data point 的币种都是必填事实。缺失或空币种必须使对应估值/计算失败关闭；不得默认成 `USD`、组合基准币或交易另一侧币种。

Portfolio 只消费 Registry 维护的 canonical daily FX time series。当前维护的 `USD/HKD`、`USD/CNY`、`USD/EUR`、`USD/GBP`、`USD/CHF` 均使用 FMP 历史日线源；不保留手工 `legacy`、CFETS 特例或静默 provider fallback。直接币对不存在时只允许通过已维护的 USD legs 形成可追溯 cross rate，结果必须携带全部 source instrument ids。

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
- 本地收益与 FX 收益分解是解释层附加输出，但必须与 base-currency 总 P&L 精确对平；
- `cash_currency_gains`、`instrument_currency_gains` 与 `pending_settlement_currency_gains` 分别解释现金、非现金资产与负债净敞口（含期权负债）和未结算货币余额的汇兑损益；Performance 的 `Total FX Attribution` 是三者之和；
- Performance FX 是沿每日真实持仓路径形成的区间归因，保证与区间总 P&L 对平；Holdings / Transactions 的 realized 与 unrealized FX 是截至某一时点的会计状态分类。两者回答的问题不同，不能把区间 attribution 当成期末 realized/unrealized balance，也不能把期末余额差直接冒充区间收益；
- 同一币种、同一 instrument 分散在多个账户时，必须先按币种聚合全部 local exposure 再计算汇兑损益，不能用 instrument-keyed map 覆盖其中一个账户；
- 当前边界缺少 fresh FX 时，该 snapshot 必须标记 `stale_fx_flag` 并排除出 fresh-complete 选择，book P&L / FX attribution 对应 coverage 为 partial/unavailable；上一边界可以使用当时实际采用且保留 source date 的有效 FX 点，休市日 carry-forward 不得让下一个已有 fresh FX 的市场日永久失去完整性。

## 5. 绩效口径

### 5.1 默认返回语义

除非明确写成 `absolute_change`、`delta` 或 `pnl`，否则系统中的“return”默认指：

- 组合级：`TWR`
- 资金使用效率：`IRR / MWROR`
- 基准对比：`benchmark-relative return`

GIPS-informed 规则：

- TWR 是默认组合绩效语言；
- MWR / IRR 是补充资金效率指标，不得在 UI 或 API summary 中替代 TWR；
- 若 IRR 因现金流符号、同日窗口或数学求根原因不可得，不能据此把已完整计算的 TWR 结果标记为失败。

Overview 展示 `Monthly Return Matrix`，按 year x month 展示月度 TWR。完整 YTD 必须从上年 `12-31` EOD anchor 开始链接；若缺少该锚点，不得只把年内可用月份复合后冒充完整 YTD。

Overview 的组合经营收益与 TWR chart 使用 operational `daily_twr`；1M / 3M VOL、risk drawdown 和 benchmark risk compare 使用 `market_risk_daily_return`。两条链使用同一个 fresh complete as-of 作为窗口终点，不得混用。若组合最新物化日期中只有部分持仓资产更新，Overview 不得使用该日期计算组合层 return / risk。单资产 Holdings Return / Volatility / Drawdown 使用其 confirmed total-return series，窗口仍由请求的 Holdings `as_of_date` 锚定，实际终点取不晚于该日期的最新有效点；Chart 可以沿用策略允许且明确标注 semantics 的展示序列。

Performance 页面使用用户选择的区间作为唯一窗口。UI 的主要结构为：

- `Return & Risk Metrics`：组合级 operational TWR / annualized TWR、IRR / MWR，以及基于 `Market Risk Return` 的 volatility、Sharpe、Sortino、Calmar 与 risk drawdown。operational carrying-basis 区间不与 benchmark 比 return；risk compare 只按双方共同的 market-risk eligible dates 计算；
- `Calculation`：合并 realized risk attribution、initial value、group rows、external flow、portfolio total 与 final value。表格有和 Holdings 一致的 view selector；系统默认视图命名为 `Default`，展示区间期初权重、平均权重、期末权重、区间收益、收益贡献、标的自身风险、相关性和风险贡献；`Beta to Portfolio` 保留为高级可选列，不进入默认视图。Group By 默认是 `None`，语义是直接展示 instrument lines，不做额外分组；也可按 instrument type / currency / account / default planning taxonomy 聚合。instrument type 与 currency 是底层 contribution axis，不允许仅在前端把 instrument rows 相加；taxonomy 聚合用于期间复盘时优先使用区间期末 assignment 并保留 cash 独立组，不把 reclassification residual 当成真实 P&L；若 instrument 期末已清仓且期末不再有 active assignment，则使用其区间内有效 assignment 承接历史 P&L，不归入 Unassigned。`TWR` 来自对应 group 的 daily return slices；`Contribution` 来自 daily contribution 聚合。表格采用 `Initial Value + Deposits - Withdrawals + Period P&L = Final Value` 的桥接口径。
- Performance group daily return 使用组内 flow-neutral `total_pnl`。普通内部买入按既有 `BOD-in / EOD-out` 约定，分母为 `beginning_value + weighted capital flow in`；但 cash 先结算、头寸下一 EOD 才确认的 `position_recognition_bridge` 是结算日的 **EOD flow**，不得进入结算日收益分母，而应作为下一日 beginning value。这样收盘后申购不会稀释当天原有持仓收益，确认日又能完整计入成交成本至确认日收盘的价格变化。直接 axis、taxonomy regroup 与 calculation detail 聚合必须保留同一 flow-timing 分类，不能在聚合时丢失或重置。
- Calculation 底层的 `Capital Gain` 使用期间绩效成本，而不是账户 book cost；它是 reconciliation 派生值，不作为默认表格列展示。显式区间的期初已有持仓按 `start_date` EOD market value 重置为期间成本，只重放 `(start_date, end_date]` 内交易；期末未卖出的持仓用 `end_date` EOD market value 计算 `Unrealized Gain`。
- `Capital Gain = Realized Gain + Unrealized Gain`；`Realized Gain` 是期间卖出部分相对于期间成本的资本利得，`Unrealized Gain` 是期末仍持有部分相对于期间成本的资本利得。FIFO / moving average 只影响 Holdings / book P&L，不改变 Performance Calculation 的期间资本利得拆分。
- `Income` 只包含 dividend / coupon / interest / dividend reinvestment 收益确认，不包含 realized capital gain。fees、taxes、FX P&L 分列。P&L 与 book attribution 不和 benchmark 对比。
- Performance 中的区间风险贡献是 realized market-risk attribution，不另设 Risk tab。每个 daily slice 独立保存 `market_risk_excluded_pnl`、`market_risk_total_pnl`、`market_risk_daily_return`、`market_risk_daily_contribution` 及其 coverage/eligibility；分组、taxonomy 和 calculation detail 聚合都必须消费这些字段，不能复用 operational `daily_return` / `daily_contribution`，也不能靠分类过滤猜测风险范围。纯衍生品与本币现金 slice 没有 eligible observation；非本币现金的 FX return/contribution 正常进入矩阵。对每个 eligible group，`Vol / Sharpe` 使用 group 自身 market-risk daily return；`Corr to Portfolio` 使用 group return 与 portfolio `market_risk_daily_return`；`Beta to Portfolio = Cov(R_g, R_p) / Var(R_p)` 保留为高级可选列；`Realized RC` 使用 `Cov(MarketRiskContribution_g, R_p) / Var(R_p)`。行级 `Obs` 表示真正对齐后的 observation count。少于 12 个对齐 period 的 Corr / Realized RC 可以计算，但 UI 必须明确标记为 low-sample preliminary estimate。这些指标服务区间复盘，不使用 Risk 页的 point-in-time covariance lookback。

Overview 的 chart compare 与 Performance 的 benchmark compare 是独立选择状态，因为用户可能对图表和区间绩效选择不同对比对象。

### 5.2 Daily TWR

正式版组合级 TWR 采用 Portfolio Performance 的日级 true time-weighted 逻辑：

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
- buy / sell 是组合内部的现金与证券转换，不进入 `CF_in / CF_out`。头寸按实际 `quantity` 和 `gross_amount` 入账，未结算款进入 pending settlement；日末用收盘 fair value 估值，所以区间中买入的 `EOD market value - actual trade cost - attached charges` 只进入当日经济 P&L 一次。不得用收盘价反推成交成本，也不得在 NAV 已扣现金后再次从 TWR 分子减买入金额。
- 除 funded-segment start 外，显式 `start_date` 当日的 buy / sell 已包含在 start-date EOD anchor；若需要观察该笔成交到当日收盘的收益，应把查询起点设为前一 EOD boundary。中途新增 instrument 不是新的组合 inception；组合保留现金也不是 segment restart。
- deposit / withdrawal 的绩效生效日使用 settlement date，缺失时使用 trade date；响应中的 `external_flow_date` 仅展示该派生结果，不是可覆写事实。内部证券交易的头寸与经济收益使用 `position_effective_date`（缺省回退 trade date）；现金腿使用 settlement date。两者不同时通过 pending settlement / position-recognition bridge 保持 NAV 连续，不得重新把确认较晚的基金申购压回 trade date。
- 当前 daily engine 对外部流采用确定性的 BOD contribution / EOD withdrawal convention。`trade_at` 只控制同日事实顺序，不能替代盘中组合估值；若盘中大额外部流需要精确 TWR，必须在流发生前后保存完整组合估值并几何链接子期间，不能用交易时间或单只证券成交价臆造 intraday NAV。
- 当前 daily snapshot engine 对每个 `as_of_date` 估值，因此外部现金流发生日天然有估值；若未来支持非日频估值，必须引入 large cash flow policy 与子期间收益几何链接，不能静默改用近似 MWR 方法。
- 对显式区间 `2026-04-01` 到 `2026-04-20`，`initial value` 是 `2026-04-01` 的 `MVE / ending_nav`，`final value` 是 `2026-04-20` 的 `MVE / ending_nav`。区间 TWR 几何链接 `2026-04-02` 至 `2026-04-20` 的 daily returns；`2026-04-01` 当日交易和收益已进入期初状态。

### 5.3 Cumulative TWR

给定区间内共有 `n` 个子期间：

$$
R_{cum} = \prod_{t=1}^{n}(1+r_t)-1
$$

这是组合页面和绩效页中默认的区间收益口径。

组合的 `TWR Index` 是把 `R_cum` 归一到 100 后得到的组合表现曲线。它在语义上类似基金的分红再投资复权 `total_return_nav`，但不是组合会计单位净值；它只用于投资表现、回撤、波动和 benchmark comparison，不用于资产规模或账面 NAV 展示。

### 5.4 Annualized TWR

若区间长度为 `Y` 年（按 calendar-anniversary Actual/Actual 计算）：

$$
R_{ann} = (1 + R_{cum})^{1/Y} - 1
$$

只有当 `Y >= 1` 时才发布年化结果。若 `Y < 1`，`annualized_twr` 必须记为 `unavailable`；区间本身的 cumulative TWR 仍可展示。

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
- TWR 反映经理在中性化 external flows 后的投资表现；
- XIRR 数学结果天然是年化率；测量期不足一年时不得把它作为 annualized MWR 展示。若产品需要短期资金加权收益，必须另行计算并命名为 period MWR；
- UI 不允许用 IRR 替代 TWR 展示“组合收益”。
- XIRR solver 必须返回 `unique_root`、`no_root`、`multiple_roots` 或 `invalid_cash_flows` 状态；只有唯一、有限且满足残差容差的有效 root 可以发布。多根不得靠初始猜值任选一个结果；
- solver status 与 unavailable reason 必须进入 API/export，前端不能把 `no_root`、`multiple_roots` 或非法现金流统一渲染成数值 0。

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

在 period calculation 中，`Delta` 也等于本期 `Period P&L`，并应满足：

$$
FinalValue = InitialValue + NetExternalInflow + PeriodPnL
$$

其中：

$$
PeriodPnL = CapitalGain + Income - Fees - Taxes + SettledCashFXPnL + PendingSettlementFXPnL + InstrumentFXPnL
$$

其中：

$$
CapitalGain = RealizedCapitalGain + UnrealizedCapitalGain
$$

`RealizedCapitalGain` 和 `UnrealizedCapitalGain` 在 Performance Calculation 中使用期间绩效成本：

- 期初已有持仓按 `start_date` EOD 市值重置为期间成本；
- `(start_date, end_date]` 内买入按成交 gross amount 建立期间成本；
- `(start_date, end_date]` 内卖出释放对应期间成本并形成 `RealizedCapitalGain`；
- 期末仍持有的剩余数量形成 `UnrealizedCapitalGain`；
- 已在期末完全卖出的资产没有剩余 period lot，因此 `UnrealizedCapitalGain = 0`。

账户 book P&L 的 `EndingUnrealizedPnL - BeginningUnrealizedPnL` 可用于会计解释，但不得作为 Performance Calculation 的 capital gain split。

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

- 组合级 drawdown 必须基于 `TWR` 复合后的 `G_t`，不得基于资产规模 `NAV_t` 直接计算；
- 任何带 `start_date / end_date` 的区间 summary 都必须用区间内 `daily_twr` 重新复合并重算 drawdown，不能直接复用 inception-to-date 的 `cumulative_twr` 或 snapshot-level drawdown；
- 区间第一笔有效收益如果已经形成回撤，drawdown peak 应以区间起点锚点为基准，而不是把第一条收益观察日误当作峰值日；
- 若主图显示 `Portfolio Value`，下方 drawdown 仍然使用 `TWR Index`，因为外部出入金不应制造或稀释投资回撤。

### 5.8 Volatility / Sharpe / Sortino / Tracking Error

### Portfolio volatility

默认使用组合 `market_risk_daily_return` 的 simple returns 构造内部区间风险统计；`daily_twr` 只用于 operational performance。市场风险收益不是衍生品公允价值回报，也不得作为正式 GIPS performance return。若未来输出正式 GIPS Report 风格披露，必须另行满足完整 fair-value return 与 monthly-return 要求，不能直接把本风险链改名复用。资产规模 `NAV_t` 的变化本身不得作为组合级 volatility / Sharpe / Sortino 的输入。

$$
\sigma_{ann} = std(r_t) \times \sqrt{periods\_per\_year}
$$

### Sharpe ratio

$$
Sharpe = \frac{\mu_{ann} - r_f}{\sigma_{ann}}
$$

MVP 默认：

- `r_f = 0`，除非显式提供 risk-free series；
- `\mu_{ann}` 使用同一区间、同一 periodicity 的 arithmetic mean return 年化；`annualized_twr` 是收益展示字段，不作为 Sharpe / Sortino 的分子。
- 若未来接入 risk-free series，必须按同一日期窗口、同一 periodicity 构造 excess return，不得混用静态年化收益率和日/月收益率。

### Sortino ratio

$$
Sortino = \frac{\mu_{ann} - r_f}{\sigma_{downside}}
$$

### Tracking error

给定 benchmark 同频收益 `r_{b,t}`：

$$
a_t = r_{p,t} - r_{b,t}
$$

$$
TE_{ann} = std(a_t) \times \sqrt{periods\_per\_year}
$$

### Information ratio

$$
IR = \frac{mean(a_t) \times periods\_per\_year}{TE_{ann}}
$$

## 6. Benchmark-relative 口径

### 6.1 基准序列

benchmark 必须先转化到组合基准货币，并对齐到组合估值日期。

当前 Performance 页的手动 `Compare benchmark` 是轻量对比：仅在 benchmark chart currency 与 portfolio base currency 一致、存在 period start boundary 之前或当日的 benchmark level，且每个组合 eligible return date 都有同日 benchmark level 时输出比较值；若币种不同、起点锚点缺失或 benchmark 覆盖不完整，在未接入后端 benchmark FX conversion / coverage reporting 前不得输出 raw-currency、stale-filled 或半截区间相对指标。

`chart_basis` 与 `return_semantics` 是两个独立合同。`close/last` 只标识指数点位字段；指数是否为 `price_return` 或 `total_return` 必须来自 Registry 的显式声明。已确认全收益序列属于 canonical comparator；已确认价格收益序列只作为带口径差异披露的 exploratory comparator；Unknown 不得输出 excess return、tracking error、information ratio、beta 或 capture ratio。Portfolio Overview、Performance 与 Research 必须使用同一判断。

若 benchmark 提供的是 NAV / index level：

$$
r_{b,t} = \frac{B_t}{B_{t-1}} - 1
$$

### 6.2 Primary benchmark

当前实现尚无持久化 `PortfolioBenchmarkAssignment`，只支持页面选择的 manual comparator。现阶段所有页面、API 和 export 必须使用 `Manual comparator` 命名，并执行 6.1 的币种、收益语义、起点锚点和日期覆盖守卫；不得把手动选择描述成 canonical primary benchmark。

`PortfolioBenchmarkAssignment` 是 future design。只有完成持久化模型、生效区间解析、API contract、迁移和端到端测试后，才允许启用下面的 primary benchmark 规则。

Future primary benchmark 上线后，以下指标必须始终基于 resolved `primary benchmark`：

- benchmark return
- excess return
- tracking error
- information ratio
- active contribution

规则：

- current-state 页按 `as_of_date` 解析 active primary benchmark assignment；
- period 页按 `[period_start, period_end]` 解析 primary benchmark assignment；若区间内发生切换，period analytics 必须显式标记 mixed benchmark context 或按分段汇总；
- canonical benchmark resolution 不得读取 `Portfolio` 上的固定 benchmark pointer；唯一真相来源是 `PortfolioBenchmarkAssignment` 的生效区间解析；
- 若不存在可解析的 primary benchmark assignment，则 benchmark-relative 结果进入 `comparator missing / unavailable` 状态。

### 6.3 默认比较对象选择

系统不采用 `benchmark / target set / alert rule` 之间的静默互相替代。

当前 canonical 规则如下：

- market-relative return / active contribution / snapshot relative columns：只在用户显式选择并通过完整守卫的 `Manual comparator` 上展示；否则 comparator missing
- benchmark_active_weight / benchmark-relative exposure / benchmark-active bets：当前未实现；未来 primary benchmark composition 可用后再启用
- target_weight_gap / construction drift / rebalance diagnostics：在 `Risk` 中分别使用 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `weight` 维度
- limit checks / alerts：使用 configured `AlertRule`
- risk budget gap：在 `Risk` 中分别使用 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `risk_budget` 维度
- period target weight drift / target risk budget summary：使用 selected planning taxonomy 在 `[period_start, period_end]` 上的 resolved target timeline，并按已启用维度分别解释

其中：

- 被用于 drift / risk budget gap 的 selected taxonomy 必须是 `planning_enabled = true`；
- `Risk` 必须分别计算 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `weight` / `risk_budget` comparator；UI 合并展示为 `Weight Target Gap` 与 `Risk Target Gap` 两个面板。每个证券风险 sleeve 只占一条 row，右侧同图并列展示当前值、`SAA` target 与 `TAA` target。Derivatives 与 Cash 都出现在 Weight Target Gap，但不得出现在 Risk Target Gap。任一来源或维度未配置时，只标记对应 comparator unavailable，不跨 `SAA` / `TAA` 或 `weight` / `risk_budget` 回退；
- period timeline 仍必须显式携带各段 target source 信息；若区间内来源随时间变化，则标记为 `mixed_timeline`
- period analytics 必须把上述结果 materialize 为正式 `ResolvedTargetTimeline` / `ResolvedTargetSegment`，而不是匿名 timeline blob；
- `TargetSet(type = taa)` 在存储层必须已物化为对各维度 eligible members 完整的目标集，运行时不做稀疏 overlay 解析：`weight` 对证券节点、固定 Derivatives 与固定 Cash 成员完整，`risk_budget` 只对承担风险的证券节点完整；
- period 内若 target 发生切换，系统必须按生效区间分段汇总，而不是拿单一期初或期末 target 解释整个区间；
- 若用户切到纯分析 taxonomy，系统只能展示 `absolute only`，并标记 comparator missing。

若某项分析缺少其 canonical comparator：

- 结果状态记为 `unavailable`；若该页面本身支持绝对口径，则只展示 `absolute only`
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
- period analytics 只能输出 `benchmark-relative performance summary`，不输出 `benchmark-active bets summary`

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

按当前支持的 Securities grouping axis（taxonomy node、instrument type、currency、coverage）聚合时：

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
- drift 计算前必须先校验 selected `TargetSet` 启用了 `weight` 维度，且在 budgeting level 上形成完整节点集；同一 scope 的 direct members 必须加总为 `100% ± epsilon`
- `Risk` 的 `SAA Weight` 与 `TAA Weight` comparator 独立计算，并在 `Weight Target Gap` 面板合并展示；若某个 target source 未启用 `weight` 维度，只有该 comparator 记为 `unavailable` / `comparator missing`

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
- resolved `target_weight` 必须在 budgeting level 上形成完整节点集，并在同一比较分母下加总为 `100% ± epsilon`；若组合存在 gross leverage / overlay，必须由独立 overlay / leverage config 先解析成 implementation target，不能把 gross exposure 直接混入 canonical target drift；
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

Risk 页和 Research solver 使用同一套 covariance model id 与 contribution mode：

- `ewma_vol_shrinkage_corr_covariance`
- `ewma_covariance`
- `sample_covariance`
- contribution mode: `signed` / `abs`

这里的 `\Sigma` 是年化 covariance matrix。`sample_covariance` 的日频 return 样本使用 `n - 1` 分母；EWMA 和 Ledoit-Wolf 这类模型可在模型内部使用其自身估计口径，但必须通过 model id 明确区分。来源更新节奏不同时，先构造 2.6.1 的日频 mark-to-last 路径，再在共同日期上估计协方差和相关性，并按实际观察密度年化。若完整有效收益不足两期，solver 必须进入 insufficient-history 诊断。

### Marginal contribution to variance

$$
MCV_i = (\Sigma w)_i
$$

### Signed component contribution to variance

$$
VCTR_i^{signed} = w_i \times MCV_i
$$

### Absolute component contribution to variance

$$
VCTR_i^{abs} = |VCTR_i^{signed}|
$$

### 10.4 Forward covariance risk share

正式版同时保留 `signed` 与 `abs` 两种 forward covariance risk share 口径，并在 Holdings、Risk 和 Research 中使用同一组选项。Research solver 的 primary mode 是 `signed`；当 signed shares 因对冲或负相关导致目标预算不可稳定匹配时，只能由配置显式切换到 `abs` 作为 alternate diagnostic view，不能在求解失败后自动切换。

Signed share:

$$
RiskShare_i^{signed} = \frac{VCTR_i^{signed}}{w^\top \Sigma w}
$$

Absolute share:

$$
RiskShare_i^{abs} = \frac{VCTR_i^{abs}}{\sum_j VCTR_j^{abs}}
$$

`abs` 必须使用数学上的精确绝对值；不得为了数值平滑给零贡献项注入 epsilon，否则 0 contribution 会被伪造为非零 risk share。

Holdings / Risk 的 covariance matrix 只包含 as-of policy 解析后 `risk_eligible=true` 的 leaf rows，但每个 leaf 的 `w_i = signed market_value_base_i / total NAV`，所以权重和可以小于 1；excluded derivative capital、base-currency cash 与 pending settlement 不增加矩阵维度，数学上等价于 0-return members。Non-base monetary exposure 只有具备 FX return series 才能作为风险因子，否则结果 unavailable。Research 在每个 solve scope 内仍使用该 scope 的 eligible risky members，并在 capital overlay 层处理 fixed cash/derivative weights，不把事件型衍生品加入 covariance。

解释：

- 对冲或负相关位置可能产生负的 signed contribution；
- `signed` 更忠实地描述边际组合风险，适合 research solver 的 primary diagnostic；
- `abs` 更适合做 PM 视角的 alternate target risk share comparison；
- 页面展示必须标明当前 contribution mode，不能把两种口径混合比较。
- Performance `Calculation` 中的 realized risk attribution 使用实际区间组合路径和实际历史权重，是事后归因；不得用于判断当前持仓是否偏离风险预算，也不得和本节 forward risk share 直接相减。

### 10.5 Risk budget gap

若 `TargetRiskShare_i` 为目标风险预算占比，`RiskShare_i` 必须来自页面或 solver 当前选定的 contribution mode：

$$
RiskBudgetGap_i = RiskShare_i - TargetRiskShare_i
$$

总预算偏离：

$$
TotalRiskBudgetGap = \sum_i |RiskBudgetGap_i|
$$

其中：

- `TargetRiskShare_i` 来自当前 row 中对应的显式 risk comparator（`SAA Risk` 或 `TAA Risk`）的 `TargetSetLine.target_risk_share`
- canonical target 只能在 selected taxonomy 的 `budgeting_level` 上直接录入；父层节点目标必须派生汇总
- `RiskShare_i` 与 `TargetRiskShare_i` 必须使用同一风险分母和同一 contribution mode；首版 canonical top-level compare 的分母是 selected taxonomy 下 eligible modeled sleeve 的 forward risk share
- 层级 sleeve 内部的 `25%` 这类 local risk budget 表示“占父 sleeve 内部风险的 25%”，不是全组合风险的 `25% × 父层预算`
- 因而禁止通过祖先 `target_risk_share` 乘法把 local sleeve risk budget 铺平成全局 risk-budget target；若需要全局 leaf comparator，必须先由 solver / resolved implementation target 在全组合协方差下显式解出
- risk budget gap 计算前必须先校验 resolved risk-budget target 可用，且只包含承担风险的证券节点；这些节点的 `target_risk_share` 加总为 `100% ± epsilon`。Cash 与 Derivatives 系统成员的 `target_risk_share` 必须为 `NULL`，不能用 `0%` 占位
- `Risk` 的 `SAA Risk` 与 `TAA Risk` comparator 独立计算，并在 `Risk Target Gap` 面板合并展示；若某个 target source 未启用 `risk_budget` 维度，只有该 comparator 记为 `unavailable` / `comparator missing`

说明：

- `RiskBudgetGap` 就是首版里唯一的风险预算偏离指标；
- 其计算输入是当前持仓 forward `RiskShare_i` 与 `target_risk_share`；
- canonical 计算对象是 selected taxonomy 下按维度解析后的 risk-budget target，而不是简单等同于整套 active target set 或独立风险预算对象。
- 若用户 drill into 某个 sleeve 做 local compare，则结果对象与 UI 必须显式标记 denominator = `parent_local_risk`，不得静默沿用 portfolio-level 含义。

### 10.6 Weight basis for target risk share comparison

默认规则：

- `risk share` 基于参与风险计算的头寸权重；
- Holdings / Risk covariance 使用 `risk_eligible=true` 的 signed base exposure 除以 total NAV；它输出包含 base-currency cash 与衍生品 0-return capital 稀释的 total-portfolio forward volatility 与 risk share；
- total NAV、modeled/excluded exposure、cash 和 coverage ratio 必须与 modeled result 一起披露，使用户可以看到可观测市场风险覆盖多少资产负债表；
- base-currency 现金、待交收和衍生品不进入 covariance matrix，但在权重向量中等价于 0-return capital；non-base monetary exposure 必须有 FX total-return series，否则整个组合结果 unavailable。

Instrument-scope planning taxonomy 的根 scope 固定包含两个不可分类、不可移动的系统成员：

- `derivative_bucket:__derivatives__` 汇总全部 FCN 与 option carrying/liability amount；
- `cash_bucket:__cash__` 汇总全部现金与待交收金额；
- 两者都可以在 SAA 或 TAA 中设置 `target_weight`，用于资本配置和 Weight Target Gap；
- 两者都不进入 `risk_budget` target，`target_risk_share` 必须为 `NULL`，也不进入 risk-budget completeness、协方差或 Risk Target Gap；
- 当前产品口径把两者作为 fixed-capital members：只参与资金权重分配，不产生 return series，也不进入风险求解；Taxonomy / Research 的 Risk / RC 显示 `N/A`；
- UI 必须解释“capital share != risk share”。

### 10.7 Research target solve

Research current target solve 使用 planning taxonomy 的层级 scope 做递归求解：

- 从最末端 sleeve 开始求解，再把每个子 sleeve 的目标权重和收益序列上卷到父 scope；
- `scope_default` 只解析当前 scope 自己的 `default_target_dimension`，不得因为目标集缺失而静默切到另一个维度；
- 若当前选中 scope 显式指定 `weight` 或 `risk_budget`，该 override 只作用于选中 scope；子 sleeve 仍按自己的 scope default 求解；
- 多成员 scope 必须有 active complete `SAA` 或 `TAA` target set。`TAA` 优先于 `SAA`；两者都缺失、启用维度不完整或目标值加总不正确时，该 scope 求解失败，不生成等权或目标权重替代结果；
- `weight` scope 使用该 scope direct members 的 `target_weight` 拟合本地权重；已启用的 `weight` 维度必须逐成员显式给出且合计为 `100%`；
- `risk_budget` scope 只使用承担风险的证券 direct members 的 `target_risk_share` 求本地目标权重；Cash 与 Derivatives 的目标资金权重只用于确定 fixed-capital allocation，不进入风险份额向量；证券风险份额加总为 `100%`；
- 若某个成员被标记为 frozen，优先使用该成员 as-of actual weight；若 actual weight 不存在，只能使用已配置的 `target_weight`，不能把 `target_risk_share` 当作资金权重；
- risk-budget solve 至少需要两个完整对齐 return observations；`strict` policy 下任何 active member 缺失都会失败，`complete_case_drop` 只能在显式选择且通过缺失行比例、latest complete row 新鲜度和最小完整观测数约束后使用，不能把 target risk share 当作 target weight；
- risk-budget solve、current risk-share estimate 与 target-volatility overlay 必须使用组合级 `Production Risk Model` 的 covariance model、lookback、frequency、missing-return policy 和 contribution mode；
- risk-budget solve 的 achieved risk share 最大绝对误差必须在显式阈值内；当前阈值为 `1e-4` share units，即 `0.01 percentage points`。超过阈值或产生负 signed risk share 时，该 scope 求解失败，不切换到 `abs` mode，也不返回旧求解器状态；
- Research `Solved Result` 的 `Look-through RC` 使用最终 leaf 权重在全组合 leaf covariance 上重新计算。父 scope 的风险预算求解误差仍以该父 scope 的本地 covariance 为准；当 covariance model 在每层重新做 correlation shrinkage 时，look-through RC 可以与父层本地 achieved risk share 有差异，UI 和报告必须明确区分两种口径；
- 单成员风险证券 scope 只允许输出数学上唯一确定的本地风险权重；只有 Cash / Derivatives 而没有风险证券的 scope 进入 `no risky members / unavailable`，不生成风险预算；
- 根 scope 完成风险 sleeve 权重后，`target_volatility` / `volatility_cap` / `fixed_gross` capital overlay 才对风险证券权重整体放缩；剩余 capital 按已配置的 Cash / Derivatives 目标权重比例分配给两个 fixed-capital system members。两者都没有正的显式资金目标时，残余全部进入系统 Cash，Derivatives 保持 `0`，不得因系统桶存在而自动生成衍生品目标。root top sleeve bounds 只约束 root 的风险证券 sleeve；违反上下限、目标波动率不可解或与 frozen/fixed gross 不可行时，该 run 必须失败或显式 unavailable，不用 unit gross、等权或旧算法兜底。

Research 的历史模拟合同是 `Point-in-time target-policy simulation`，不是用今天 taxonomy/targets 回放历史的 current-policy replay：

`PUT /api/portfolios/{portfolio_id}/research/settings` 的 core run-setup 字段使用全量 PUT 语义，调用方应提交完整 core settings；`frozen_taxonomy_node_ids` 和 `top_sleeve_weight_bounds` 省略时保留、空列表时清空。`backtest_*` control 省略时保留当前值；nullable benchmark 显式 `null` 时清空，robustness scenarios 显式 `null` 时恢复系统默认 scenarios。API 不得把省略的 backtest control 静默重置为 schema default。

- universe 来自截至 run as-of date 的 taxonomy configuration revision 历史中所有曾经 active 的 instrument assignments；不能只用当前成员，也不能让当前已清退成员从历史样本消失；
- 每个计划 rebalance date 都按该日生效的 taxonomy configuration、assignment、TargetSet、frozen sleeves、top sleeve bounds 和 capital overlay 重新执行同一 Production Risk Model / target solve，只读取决策日及以前的市场观察；输出每次决策使用的 `taxonomy_configuration_version`、effective date、point-in-time coverage 和 skipped rebalance reason；
- 支持 `1w`、`1m` 与 `3m` rebalance。组合成员共同历史不足完整 risk window 时返回空 points 或跳过该次决策并给出 warning，不生成晚于 as-of 的日期，不以更短风险窗口替代；benchmark 历史只影响 benchmark 曲线和相对指标，不得推迟或阻断组合自身模拟起点；
- target 在配置的 calendar-day implementation delay 后，于全部目标资产第一条共同 return observation 的 EOD boundary 执行。该 observation 代表前一 EOD 到当前 EOD 的 close-to-close return，归属于执行前持仓；新 target 从下一条 observation 开始计收益。若执行边界上任一执行前持仓缺少完整 EOD return observation，必须跳过该次执行并在 point-in-time coverage 中标记 partial，不得用陈旧 NAV 成交。现金按实际日历天数和 annual cash yield 复合；commission 与 slippage 作用于 risky buys/sells，tax 只作用于 risky sells；execution record 必须披露 buy/sell/cash-leg turnover、各项成本、scheduled/actual execution date 和成本前后 NAV；
- top-sleeve contribution 使用 starting-NAV unit 的累计 arithmetic linking，cash 和 execution costs 是显式 component；每个点返回 `linked_contribution`、`nav_change` 与 reconciliation residual。它不是把 sleeve 百分比收益几何相加；
- robustness scenarios 用同一组 point-in-time decisions 在替代 cash yield、commission、tax、slippage 和 delay 假设下重放，输出 scenario metrics、相对 base period-return delta、turnover 和 total cost；
- OOS 稳健性使用连续 training/test calendar-month 的 rolling temporal holdout，只发布各 test window 开始后的 OOS observations，并给出逐窗口与聚合 OOS metrics。当前窗口不会在 training window 内重新拟合或选择参数，因此这是固定 point-in-time policy 的时间外推诊断，不是 walk-forward optimization；历史不足时必须明确 unavailable，不能把 in-sample 点冒充 OOS；
- 当前 execution model 假设目标在首个共同 observation 完整成交，尚未模拟 order rejection、partial fills、成交量/流动性约束或 market impact。文档、UI 和报告不得声称这些摩擦已经覆盖。

每次 run 必须输出 root `solve_event` 和完整 `scope_solve_events`，用于复核每层 scope 的默认维度、实际维度、solver、RC mode、risk gap 与成员数。

每次 run 还必须输出 `calculation_frequency` profile，其中 requested / resolved / default 均为 `daily`，并保留源数据发布节奏计数；同时输出 missing-return policy、rows before / after、missing rows、dropped rows、latest complete date 与 trailing staleness，便于复核日频样本。

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
- `requested_as_of_date` / effective `as_of_date` / `as_of_clamp_reason`（若发生终点 clamp）
- valuation / return / book-P&L / attribution 四维 coverage state
- `irr_solver_status`（若请求 MWR）
- risk `calculation_frequency`、sample count 与 minimum-sample / coverage reason（若适用）

### 12.1 物化快照刷新一致性

daily snapshot、holding snapshot、contribution slice 是可重建的读模型，不是源事实。刷新链路必须满足：

- 交易、账户、共享行情或 FX 变化先写入源事实，再把受影响组合标记为 `stale`；
- 每次 stale 标记生成新的 `refresh_request_id`，用于表示“至少需要覆盖到这次事实更新之后”；
- 物化 payload schema 或核心计算口径改变时必须提升 `calculation_version`，让旧 read model 自动失效并重建；不能在 daily snapshot、contribution regroup 或 calculation detail 聚合中长期保留旧字段兼容逻辑。
- 组合 summary、Overview 与默认 Holdings 的 as-of 选择必须复用同一套 latest fresh complete snapshot 规则；不得在不同读路径各自实现日期兜底，也不得因浏览器日期、服务器当前日期或部分资产已更新而改变组合层窗口终点。
- 同一组合的物化刷新串行执行；如果刷新期间又收到新的 `refresh_request_id`，当前计算结果不得把状态置为 `current` 或清空 `dirty_from`，必须继续按最新事实再计算一轮；
- 邮件、文件或批量行情导入完成后按资产/组合去重触发刷新，不应在单个数据点写入过程中反复启动组合重建；
- 读路径可以在发现状态不是 `current` 时触发 repair refresh，但 repair 必须复用同一套串行 claim 逻辑，不能并行删除/插入同一组合的物化表。
- 每次计算在读取源事实前记录 transaction/account generation 与共享行情 generation，在计算完成后以及正式 publish 前再次读取；任一 generation 改变时，丢弃该次混合世代结果并重试，不得发布半旧半新的 snapshot；
- refresh 结果显式返回 `source_generation_status = stable | stable_after_retry | discarded`、reason 与 before/after generation。重试仍不稳定时保持 stale，不覆盖最后一个已发布的稳定读模型。

## 13. 首版明确不锁死的高级口径

以下能力首版可以保留扩展位，但不在当前 canonical 规格中锁死：

- multi-period Brinson linking 的完整变体
- factor model ex-ante risk
- option Greeks
- CVaR / Expected Shortfall 的复杂参数化定义
- fixed income full accrual engine
- liquidity-adjusted scenario engine

## 14. 维护规则

任何计算相关改动必须同步检查：

- [02_GIPS_ALIGNMENT.md](./02_GIPS_ALIGNMENT.md) 是否仍准确描述 GIPS-informed 方法边界；
- Portfolio README 的计算层当前口径是否需要更新；
- 后端 Research / Performance / Risk 测试是否覆盖了新增或修改的失败状态；
- 前端是否显式展示 `partial`、`unavailable`、`comparator missing`、`insufficient-history` 等关键状态。
