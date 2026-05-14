# PMS 正式版计算口径规格

关联文档：

- [`02_GIPS_ALIGNMENT.md`](./02_GIPS_ALIGNMENT.md)

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
- 涉及 `风控、风险预算、buy-side review` 的部分，再由本项目扩展。
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

### 1.3 正确性与失败策略

计算层不保留为了“让结果看起来可用”的兼容分支。任何 canonical 指标必须满足其输入、配置、覆盖率和数学条件；条件不满足时，结果应显式进入 `partial`、`unavailable`、`comparator missing`、`insufficient-history` 或失败状态。

严格规则：

- 不用目标权重、等权、历史旧算法或另一维度 target 替代 risk-budget 求解结果；
- 不用 stale price、跨 period forward fill 或不同长度持有期收益补齐 covariance、correlation、Sharpe、target-volatility overlay 或 risk contribution；
- 不在 `SAA` / `TAA`、`weight` / `risk_budget`、benchmark / target / alert rule 之间静默互相替代；
- 单成员 scope 允许输出唯一确定的 100% 权重；现金成员在 covariance-based risk budget 中允许 `target_risk_share = 0`。除此之外，未配置目标不是默认等权目标；
- UI、API、export 必须展示结果状态与 coverage / solver 诊断，不能把失败条件包装成正常结果。

## 2. 总体约定

### 2.1 估值时点

正式版默认使用 **end-of-day valuation**：

- 当日价格、FX、benchmark level 都视为当日收盘或该日最终可用估值；
- 当日 `MVB` 等于上一估值日的 `MVE`；
- 当日 `MVE` 为当日收盘后的组合总市值与现金合计。
- 物化 daily snapshot 必须同时保存 `beginning_nav` 与 `ending_nav`；任意用户查询区间 `[start_date, end_date]` 的 `initial value` 使用 `start_date` 当天的 `beginning_nav`，语义等同于上一估值日 EOD，`final value` 使用 `end_date` 的 `ending_nav`。

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
- fund 的 research/risk 序列使用 `total_return_nav`；若改用 `official_nav`，必须在结果中标记 quote basis，且不得把分红/派息收益伪装成已复权 total return；
- equity 的 research/risk 序列使用 `adjusted_close`；若改用 `close`，必须在结果中标记 quote basis，且不得把除权除息导致的机械跳空当成真实损失；
- chart / sparkline 必须展示实际采用的 quote basis。basis 缺失时，图表可以降级为 `partial / unavailable`，不能静默换基准。

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

- Return / IRR：`ACT/365.25`
- Daily volatility / tracking error：优先使用实际有效收益观察密度推断 `periods_per_year`
- Weekly volatility：`52`
- Monthly volatility：`12`

若观察频率是稳定交易日频，`periods_per_year` 通常接近 `252`；若存在节假日、缺价或非交易日 carry-forward，系统必须记录并使用实际有效收益观察密度，避免把无市场观察的 0 return 当作风险样本。

协方差 / 相关性计算必须先构造 active return matrix。默认 `strict` missing-return policy 要求矩阵中的每个收益日期对所有 active series 都完整；任何单成员缺失都进入 coverage / missing 诊断，不得被补成 0 return，也不得用 pairwise dates 拼出一个每个 entry 样本不同的 covariance matrix。`sample_covariance` 使用完整对齐样本的样本协方差估计量，分母为 `n - 1`，且至少需要两个完整 return observations；不得用总体协方差 `n` 分母作为普通样本风险估计。年化必须使用完整样本 index 的实际有效收益观察密度。Research 的 `ewma_vol_shrinkage_corr_covariance` 也必须在 missing-return policy 处理后的完整窗口上估计 EWMA volatility 与 shrinkage correlation。

### 2.6.1 计算频率与节假日

Risk 与 Research 的 covariance / correlation / risk contribution 必须先确定一个 target calculation frequency，再把各资产 NAV/price series 对齐到该频率：

- `auto` 规则：全部资产可判定为日频时使用 `daily`；日频和周频混合时使用 `weekly`；存在月频资产时使用 `monthly`。样本很短且间隔不规则时，不得把缺失观测误判为周频或月频，除非所有间隔都一致落在对应频率区间。
- Research 允许用户显式选择 `daily` / `weekly` / `monthly`，但不能选择高于数据支持的频率。例如日频+周频混合不能强制按日频计算。
- 对齐规则：每个资产在目标 period 内只取最后一个有效观测点；不得跨目标 period 前向填充生成假 NAV。若某资产缺少某个目标 period，该资产该 period 的 return 为 missing。
- 节假日规则：若标准资产在某个交易所共同节假日都没有更新，则该日期不会进入共同收益样本；若只有单个资产缺失，而其他资产在该目标 period 有观测，则这是该资产的缺失数据，不应被当作 0 return 或 stale return。
- Missing-return policy：默认 `strict`，任何 active member 在目标 period 缺失都使该风险/研究样本不可解。Research 可以由用户显式选择 `complete_case_drop`，但只能删除含缺失成员的整行，并受缺失行比例 `10%`、latest complete row 尾部新鲜度上限（日频 `5` 天、周频 `14` 天、月频 `62` 天）和最小完整观测数约束；结果必须暴露 rows before / after、dropped rows、latest complete date 与 trailing staleness。
- 周频 period end 使用自然周五；若 as-of date 落在周中，则最后一个未完整周以 as-of date 作为 capped period end。月频使用自然月末，同样以 as-of date cap 最后一个 period。
- 若未来传入显式交易日日历，日频对齐应以日历校验 holiday vs missing：共同非交易日不生成样本；日历交易日缺价必须进入 coverage / missing 诊断，而不是隐式填值。

### 2.7 缺失数据与覆盖率

如果某一估值日存在缺失数据：

- 价格缺失时，状态型 NAV / holdings 可以使用最近可用价格维持账面连续性，但必须记录 `stale_price_flag`，且该日不得作为正常市场收益观察进入 return/risk 样本；
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

### 3.5.1 Bond valuation boundary

首版债券估值采用 **valuation-first** 口径：

- 首版 `bond` 默认指 `plain-vanilla cash bond`；
- 组合 NAV、持仓市值、绩效计算统一以 **dirty market value** 为 canonical basis；
- 若数据源提供的是 clean price，则必须同时提供 accrued interest，再合成为 canonical dirty value；
- coupon 作为现金收益进入 ledger；到期本金回收通过 `maturity_redemption` 或等价显式事件入账；
- duration、convexity、yield、spread 等字段可作为解释性外部输入展示，但不是首版 canonical 自研计算结果。

#### Open position book cost

对任意当前头寸，`Cost Basis` / `Purchase Value` 定义为 open-position book cost：

- 当前仍未 disposed / transferred out 的 remaining cost basis；
- 包含已资本化到 buy/opening/reinvestment 成本的费用和税费；
- 不包含已经 realized 或 transferred out 的部分；
- 在 `FIFO` 下等于 open FIFO lots 的 remaining cost basis 之和；
- 在 `moving_average` 下等于 rolling average bucket 的 remaining cost basis。

`Avg Cost` 定义为：

$$
AvgCost^{book}_i = \frac{RemainingCostBasis_i}{RemainingQuantity_i}
$$

它不是某一笔交易的 purchase price。真实交易价格在 lot 层用 `entry_price = entry_gross_amount / entry_quantity` 表示，且不包含资本化费用和税费；`entry_cost_per_unit` 才包含资本化费用和税费。`moving_average` 的 synthetic lot 没有真实 tax-lot purchase price，展示时应优先使用当前 `Avg Cost`。

Holdings 是当前持仓状态表，只展示当前仍然 open 的 position quantity、settled cash balance、quote、market value、weight、open-position cost basis 与 unrealized P&L。资产级 TWR、period contribution、realized gain、dividend / coupon income、fees / taxes impact 和 closed positions 属于 `Performance` / security detail 的区间绩效视图，不进入 Holdings 默认列，也不作为 Holdings 的 canonical 语义。

Holdings 中的现金行按 settled cash ledger 逐币种生成，`instrument_id = cash:{currency}`：

- `market_value` 等于该币种 settled cash amount；
- `market_value_base` 等于该现金金额按 as-of date FX 转成组合 base currency 后的值；
- `cost_basis`、`cost_basis_base`、`Avg Cost` 与 unrealized P&L 对现金不适用；
- base-currency cash 的 instrument return、day return 和 volatility 为 `0`；
- non-base cash 的 instrument return / day return 来自该现金币种兑 base currency 的 FX series；
- pending settlement 不生成 Holdings cash row，但仍进入组合 NAV 与 pending settlement total。

`Market Value Base` 是任意 holding row 的 base-currency fair value。对非现金资产，它等于 `quantity * selected valuation quote` 再按 as-of date FX 转换；对现金，它等于 settled cash amount 的 base-currency value。Holdings `Portfolio Total` 的 market value 包含非现金市值与 settled cash，不包含 pending settlement；NAV 另行等于 market value 加 pending settlement。

`Day Change` / `Day Return` 是 as-of date 当前持仓规模上的一天市场变动，不是区间绩效：

- 非现金资产使用当前 selected valuation quote 与同一 quote basis 的上一可用 quote，`day_return = current_quote / previous_quote - 1`；
- 非现金资产的 `day_change_value` 使用当前 quantity 乘以 quote 变动；`day_change_value_base` 再按 as-of date FX 转 base currency；
- base-currency cash 的 day change 为 `0`；
- non-base cash 的 day return 使用当前 FX 与上一可用 FX，`day_change_value_base = cash_amount * (current_fx - previous_fx)`；
- 若缺少当前点、上一点或 FX，相关字段必须为空，不得用 0 或 chart sample 补齐。

Holdings 可以展示 quote-derived instrument market trend 指标，作为扫描当前持仓标的自身近期市场表现的辅助列：

- `Chart 1M / 3M / 6M / 1Y` 是前端展示用的 sampled path；
- `1W Return / MTD / YTD / 1Y` 只使用标的自身 selected quote series，计算为 `latest_quote / anchor_quote - 1`；
- selected quote series 按 `quote_selection_policy.total_return -> chart -> valuation -> reference` 选择；若策略为空，则选用截至 as-of 最新的一条 quote basis，不能混用多个 basis；
- `1W Return` / `1Y` 的 anchor quote 是目标日期或之前最近 quote；
- `MTD` / `YTD` 的 anchor quote 是月初 / 年初之前最近 quote；若历史不足，则只能使用期间内第一条 quote 作为明确标记的 partial-data anchor，不能展示为完整区间收益；
- `Current DD` 计算为 `latest_quote / max_available_selected_quote_to_date - 1`；
- `Vol 1M / 3M / 6M / 1Y` 使用同一 selected quote series 先按组合 resolved risk frequency 取 daily / weekly / monthly period returns，再按实际 elapsed days 年化；不得使用 `Chart *` sampled points；
- volatility 窗口必须有接近窗口起点的初始 quote、足够 elapsed-day 覆盖和最小收益样本数，否则为空。当前门槛为 daily `10 / 30 / 60 / 120`、weekly `3 / 6 / 12 / 24`、monthly `2 / 2 / 4 / 6`，分别对应 `1M / 3M / 6M / 1Y`；
- 这些指标不读取 quantity、cash flow、cost basis、FIFO / moving average、realized gain 或 income，因此不属于组合 TWR、holding contribution 或 book P&L。

Holdings group rows 不是后端 period-performance group：

- market value、cost basis、day change、open lots 等绝对量按组内 rows 汇总；
- unrealized return 使用组内非现金 `unrealized P&L / cost basis`，不是成员百分比的加权平均；
- `1W / MTD / YTD / 1Y Return` 使用 as-of date base-currency market value 权重对成员自身 return 加权；覆盖不足时为空；
- group volatility / drawdown 用组内成员 return series 在共同 period 上组成当前权重的组 return series 后计算，包含协方差效果，不等于成员 volatility 或 drawdown 的加权平均；
- base-currency cash 可作为 0-return 成员参与覆盖；non-base cash 使用其 FX return series；
- UI 中的 `Non-cash Portfolio` 行是当前 rows 的非现金 subtotal，只服务展示和导出，不是源事实、不参与 group、sort、detail 或 portfolio totals。

#### Unrealized P&L

$$
UnrealizedPnL_i = MV_i^{base} - PurchaseValue_i^{open}
$$

#### Realized P&L

每次卖出时：

- `FIFO` 按最早未平的 lot 依次匹配；
- `moving_average` 按卖出时的 rolling average cost per unit 释放成本。

$$
RealizedPnL = NetSaleProceeds - MatchedCostBasis
$$

说明：

- realized P&L 是成本法相关的 book/tax P&L；
- TWR 不使用 realized P&L 作为收益率输入，因此成本法切换只重算 book capital gain / cost basis，不应改变 fair-value based return。

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

- 组合级：`TWR`
- 资金使用效率：`IRR / MWROR`
- 基准对比：`benchmark-relative return`

GIPS-informed 规则：

- TWR 是默认组合绩效语言；
- MWR / IRR 是补充资金效率指标，不得在 UI 或 API summary 中替代 TWR；
- 若 IRR 因现金流符号、同日窗口或数学求根原因不可得，不能据此把已完整计算的 TWR 结果标记为失败。

Overview 展示 `Monthly Return Matrix`，按 year x month 展示月度 TWR，YTD 为可用月份的复合收益。

Performance 页面使用用户选择的区间作为唯一窗口。UI 的主要结构为：

- `Return & Risk Metrics`：组合级 TWR / annualized TWR、IRR / MWR、risk、drawdown。return / risk 类指标可选择 benchmark price series 做 period return、annualized return、volatility、drawdown 的轻量对比；
- `Calculation`：合并 realized risk attribution、initial value、group rows、external flow、portfolio total 与 final value。表格有和 Holdings 一致的 view selector；系统默认视图命名为 `Default`，展示区间平均权重、期末权重、区间收益、收益贡献、标的自身风险、相关性和风险贡献；`Beta to Portfolio` 保留为高级可选列，不进入默认视图。Group By 默认是 `None`，语义是直接展示 instrument lines，不做额外分组；也可按 instrument type / currency / account / default planning taxonomy 聚合。instrument type 与 currency 是底层 contribution axis，不允许仅在前端把 instrument rows 相加；`TWR` 来自对应 group 的 daily return slices；`Contribution` 来自 daily contribution 聚合。表格采用 `Initial Value + Deposits - Withdrawals + Period P&L = Final Value` 的桥接口径。
- Calculation 底层的 `Capital Gain` 使用期间绩效成本，而不是账户 book cost；它是 reconciliation 派生值，不作为默认表格列展示。期初已有持仓按 start date 的 beginning market value 重置为期间成本，区间内买入按成交 gross amount 建立期间成本，期末未卖出的持仓用 end date market value 计算 `Unrealized Gain`。
- `Capital Gain = Realized Gain + Unrealized Gain`；`Realized Gain` 是期间卖出部分相对于期间成本的资本利得，`Unrealized Gain` 是期末仍持有部分相对于期间成本的资本利得。FIFO / moving average 只影响 Holdings / book P&L，不改变 Performance Calculation 的期间资本利得拆分。
- `Income` 只包含 dividend / coupon / interest / dividend reinvestment 收益确认，不包含 realized capital gain。fees、taxes、FX P&L 分列。P&L 与 book attribution 不和 benchmark 对比。
- Performance 中的区间风险贡献是 realized attribution，不另设 Risk tab。对每个 group，`Vol / Sharpe` 使用 group 自身 daily return；`Corr to Portfolio` 使用 group daily return 与 portfolio daily TWR；`Beta to Portfolio = Cov(R_g, R_p) / Var(R_p)` 保留为高级可选列；`Realized RC` 使用 `Cov(Contribution_g, R_p) / Var(R_p)`，衡量该 group 的 contribution 路径对组合已实现方差的协方差占比。这些指标服务区间复盘，不使用 Risk 页的 point-in-time covariance lookback。

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
- 当前 daily snapshot engine 对每个 `as_of_date` 估值，因此外部现金流发生日天然有估值；若未来支持非日频估值，必须引入 large cash flow policy 与子期间收益几何链接，不能静默改用近似 MWR 方法。
- 对显式区间 `2026-04-01` 到 `2026-04-20`，`initial value` 是 `2026-04-01` 的 `MVB / beginning_nav`，即 `2026-03-31` EOD；`final value` 是 `2026-04-20` 的 `MVE / ending_nav`。区间 TWR 几何链接 `2026-04-01` 至 `2026-04-20` 的 daily returns。

### 5.3 Cumulative TWR

给定区间内共有 `n` 个子期间：

$$
R_{cum} = \prod_{t=1}^{n}(1+r_t)-1
$$

这是组合页面、绩效页和 review 中默认的区间收益口径。

组合的 `TWR Index` 是把 `R_cum` 归一到 100 后得到的组合表现曲线。它在语义上类似基金的 total-return NAV / cumulative NAV，但不是组合会计单位净值；它只用于投资表现、回撤、波动和 benchmark comparison，不用于资产规模或账面 NAV 展示。

### 5.4 Annualized TWR

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
- TWR 反映经理在中性化 external flows 后的投资表现；
- UI 不允许用 IRR 替代 TWR 展示“组合收益”。

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
PeriodPnL = CapitalGain + Income - Fees - Taxes + FXPnL
$$

其中：

$$
CapitalGain = RealizedCapitalGain + UnrealizedCapitalGain
$$

`RealizedCapitalGain` 和 `UnrealizedCapitalGain` 在 Performance Calculation 中使用期间绩效成本：

- 期初已有持仓按期初市值重置为期间成本；
- 区间买入按成交 gross amount 建立期间成本；
- 区间卖出释放对应期间成本并形成 `RealizedCapitalGain`；
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

默认使用组合 `daily_twr` 的 simple returns 构造内部区间风险统计；当输出正式 GIPS Report 风格披露时，ex-post standard deviation 必须改用 monthly returns，并按同一方法同时计算组合和 benchmark。资产规模 `NAV_t` 的变化不得作为组合级 volatility / Sharpe / Sortino 的输入。

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
- target_weight_gap / construction drift / rebalance diagnostics：在 `Risk` 中分别使用 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `weight` 维度
- limit checks / alerts：使用 configured `AlertRule`
- risk budget gap：在 `Risk` 中分别使用 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `risk_budget` 维度
- review target weight drift / target risk budget summary：使用 selected planning taxonomy 在 `[period_start, period_end]` 上的 resolved target timeline，并按已启用维度分别解释

其中：

- 被用于 drift / risk budget gap 的 selected taxonomy 必须是 `planning_enabled = true`；
- `Risk` 必须把 `SAA Weight` / `SAA Risk` / `TAA Weight` / `TAA Risk` 作为四个显式 comparator 计算；任一来源或维度未配置时，只标记对应 comparator unavailable，不跨 `SAA` / `TAA` 或 `weight` / `risk_budget` 回退；
- review timeline 仍必须显式携带各段 target source 信息；若区间内来源随时间变化，则标记为 `mixed_timeline`
- period analytics 必须把上述结果 materialize 为正式 `ResolvedTargetTimeline` / `ResolvedTargetSegment`，而不是匿名 timeline blob；
- `TargetSet(type = taa)` 在存储层必须已物化为对已启用维度完整的目标集，运行时不做稀疏 overlay 解析；
- review period 内若 target 发生切换，系统必须按生效区间分段汇总，而不是拿单一期初或期末 target 解释整个区间；
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
- drift 计算前必须先校验 selected `TargetSet` 启用了 `weight` 维度，且在 budgeting level 上形成完整节点集；同一 scope 的 direct members 必须加总为 `100% ± epsilon`
- `Risk` 的 `SAA Weight` 与 `TAA Weight` comparator 独立计算；若某个 target source 未启用 `weight` 维度，只有该 comparator 记为 `unavailable` / `comparator missing`

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

这里的 `\Sigma` 是年化 covariance matrix。`sample_covariance` 的日/周/月 period return 样本使用 `n - 1` 分母；EWMA 和 Ledoit-Wolf 这类模型可在模型内部使用其自身估计口径，但必须通过 model id 明确区分。混合日频 / 周频 / 稀疏 NAV 时，先按 2.6.1 的 target calculation frequency 取 period-end 观测，再计算收益；不做 stale 价格生成的 0 return。默认 `strict` policy 下，active return matrix 必须在所有参与成员上完整，协方差和相关性按同一组完整收益日期估计并按这些日期的实际观察密度年化。Research 只有在用户显式选择 `complete_case_drop` 且通过覆盖率与尾部新鲜度约束时，才可以整行删除缺失 period 后继续求解。若完整有效收益不足两期，solver 必须进入 insufficient-history 诊断，而不是用不同长度的持有期收益硬拼协方差。

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

### 10.4 Realized risk share

正式版同时保留 `signed` 与 `abs` 两种风险份额口径，并在 UI 和 Research 中使用同一组选项。Research solver 的 primary mode 是 `signed`；当 signed shares 因对冲或负相关导致目标预算不可稳定匹配时，只能由配置显式切换到 `abs` 作为 alternate diagnostic view，不能在求解失败后自动切换。

Signed share:

$$
RiskShare_i^{signed} = \frac{VCTR_i^{signed}}{w^\top \Sigma w}
$$

Absolute share:

$$
RiskShare_i^{abs} = \frac{VCTR_i^{abs}}{\sum_j VCTR_j^{abs}}
$$

解释：

- 对冲或负相关位置可能产生负的 signed contribution；
- `signed` 更忠实地描述边际组合风险，适合 research solver 的 primary diagnostic；
- `abs` 更适合做 PM 视角的 alternate target risk share comparison；
- 页面展示必须标明当前 contribution mode，不能把两种口径混合比较。

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

- `TargetRiskShare_i` 来自当前选定的显式 risk comparator（`SAA Risk` 或 `TAA Risk`）对应 `TargetSetLine.target_risk_share`
- canonical target 只能在 selected taxonomy 的 `budgeting_level` 上直接录入；父层节点目标必须派生汇总
- `RiskShare_i` 与 `TargetRiskShare_i` 必须使用同一风险分母；首版 canonical top-level compare 的分母是 selected taxonomy 下的 portfolio-level absolute risk share
- 层级 sleeve 内部的 `25%` 这类 local risk budget 表示“占父 sleeve 内部风险的 25%”，不是全组合风险的 `25% × 父层预算`
- 因而禁止通过祖先 `target_risk_share` 乘法把 local sleeve risk budget 铺平成全局 risk-budget target；若需要全局 leaf comparator，必须先由 solver / resolved implementation target 在全组合协方差下显式解出
- risk budget gap 计算前必须先校验 resolved risk-budget target 可用，且其非现金节点的 `target_risk_share` 加总为 `100% ± epsilon`，现金节点 `target_risk_share = 0`
- `Risk` 的 `SAA Risk` 与 `TAA Risk` comparator 独立计算；若某个 target source 未启用 `risk_budget` 维度，只有该 comparator 记为 `unavailable` / `comparator missing`

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

### 10.7 Research target solve

Research current target solve 使用 planning taxonomy 的层级 scope 做递归求解：

- 从最末端 sleeve 开始求解，再把每个子 sleeve 的目标权重和收益序列上卷到父 scope；
- `scope_default` 只解析当前 scope 自己的 `default_target_dimension`，不得因为目标集缺失而静默切到另一个维度；
- 若当前选中 scope 显式指定 `weight` 或 `risk_budget`，该 override 只作用于选中 scope；子 sleeve 仍按自己的 scope default 求解；
- 多成员 scope 必须有 active complete `SAA` 或 `TAA` target set。`TAA` 优先于 `SAA`；两者都缺失、启用维度不完整或目标值加总不正确时，该 scope 求解失败，不生成等权或目标权重替代结果；
- `weight` scope 使用该 scope direct members 的 `target_weight` 拟合本地权重；已启用的 `weight` 维度必须逐成员显式给出且合计为 `100%`；
- `risk_budget` scope 使用非现金 direct members 的 `target_risk_share` 求本地目标权重，现金的 `target_risk_share` 必须为 `0`，非现金风险份额加总为 `100%`；
- 若某个成员被标记为 frozen，优先使用该成员 as-of actual weight；若 actual weight 不存在，只能使用已配置的 `target_weight`，不能把 `target_risk_share` 当作资金权重；
- risk-budget solve 至少需要两个完整对齐 return observations；`strict` policy 下任何 active member 缺失都会失败，`complete_case_drop` 只能在显式选择且通过缺失行比例、latest complete row 新鲜度和最小完整观测数约束后使用，不能把 target risk share 当作 target weight；
- risk-budget solve 的 achieved risk share 最大绝对误差必须在显式阈值内；当前阈值为 `1e-4` share units，即 `0.01 percentage points`。超过阈值或产生负 signed risk share 时，该 scope 求解失败，不切换到 `abs` mode，也不返回旧求解器状态；
- 单成员 scope 只允许输出数学上唯一确定的本地目标：非现金/普通成员权重 `100%`，现金 risk budget `0%`；
- 根 scope 完成风险 sleeve 权重后，`target_volatility` / `fixed_gross` capital overlay 才对非现金目标权重整体放缩，并把残差写入 cash-like member；没有 cash-like member、目标波动率无法用正的估计波动率缩放或 overlay 后违反可行约束时，该 run 必须失败或显式 unavailable，不用 unit gross、等权或旧算法兜底。

每次 run 必须输出 root `solve_event` 和完整 `scope_solve_events`，用于复核每层 scope 的默认维度、实际维度、solver、RC mode、risk gap 与成员数。

每次 run 还必须输出 `calculation_frequency` profile，包括用户请求频率、最终解析频率、可选频率、源数据频率计数和状态文案；同时输出 missing-return policy、rows before / after、missing rows、dropped rows、latest complete date 与 trailing staleness，便于复核样本是否被严格保留或显式 complete-case 删除。组合 workspace 的组合名状态栏展示当前组合的默认 risk basis，方便用户确认当前是 daily / weekly / monthly 口径。

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

### 12.1 物化快照刷新一致性

daily snapshot、holding snapshot、contribution slice 是可重建的读模型，不是源事实。刷新链路必须满足：

- 交易、账户、共享行情或 FX 变化先写入源事实，再把受影响组合标记为 `stale`；
- 每次 stale 标记生成新的 `refresh_request_id`，用于表示“至少需要覆盖到这次事实更新之后”；
- 同一组合的物化刷新串行执行；如果刷新期间又收到新的 `refresh_request_id`，当前计算结果不得把状态置为 `current` 或清空 `dirty_from`，必须继续按最新事实再计算一轮；
- 邮件、文件或批量行情导入完成后按资产/组合去重触发刷新，不应在单个数据点写入过程中反复启动组合重建；
- 读路径可以在发现状态不是 `current` 时触发 repair refresh，但 repair 必须复用同一套串行 claim 逻辑，不能并行删除/插入同一组合的物化表。

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
