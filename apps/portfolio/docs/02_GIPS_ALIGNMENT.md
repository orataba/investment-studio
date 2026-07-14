# GIPS-Informed Performance Methodology

> Scope note: 本文中的 `Research` 仅表示 `Allocation Research / Allocation Lab`。
> 普通组合仍使用 taxonomy 做分类、归因和跟踪；planning taxonomy 扩展、TargetSet、
> 风险预算和 policy drift 规则不适用于显式归类的 ETF 轮动策略组合。仅持有 ETF 的普通组合仍使用 taxonomy。

关联文档：

- [`01_CALCULATION_SPEC.md`](./01_CALCULATION_SPEC.md)

## 1. 定位

GIPS 是 CFA Institute 维护的投资绩效呈现标准，核心目标是让投资机构公平、完整、一致地呈现绩效。它对本项目有参考价值，但本项目当前定位是个人/小团队 PMS 计算系统，不是机构级 GIPS composite reporting 系统。

因此本文只定义 **GIPS-informed** 的计算治理原则，不构成、也不暗示本项目或用户组合满足 GIPS compliance。

官方参考：

- GIPS Standards for Firms: <https://www.gipsstandards.org/standards/gips-standards-for-firms/>
- GIPS Standards Handbook for Firms: <https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/>

## 2. 可借鉴原则

### 2.1 TWR 是默认绩效口径

GIPS 对普通组合绩效呈现默认要求使用 time-weighted returns，只有在特定条件下才允许用 money-weighted returns 替代。

本项目采用：

- `TWR` 作为 `Overview / Performance` 的默认组合收益口径；
- `XIRR / MWRR` 作为资金使用效率补充指标；
- UI 不允许用 XIRR 替代 TWR 展示“组合收益”。

### 2.2 外部现金流必须被中性化

GIPS 强调 TWR 要中性化 client-driven external cash flows，避免把出入金时点误算为管理能力。

本项目采用：

- `deposit`、`withdrawal` 和真实组合边界分配为 external cash flow；
- `buy / sell / dividend / coupon / fee / tax / internal transfer` 不作为组合级 external cash flow；
- `deposit` / `withdrawal` 以 cash value date（当前即 `settlement_date`）进入 NAV 和绩效；较早的 `trade_date` 只保留为指令日期，不能提前形成 pending NAV 或 TWR flow；缺少 value date 时 fail closed，不允许回退到 trade date。证券交易仍按 trade-date position 与 pending settlement 处理；
- 日频 TWR 公式固定为：

```text
r_t = (MVE_t + CF_out,t) / (MVB_t + CF_in,t) - 1
```

### 2.3 每个外部现金流日期都应有估值

GIPS 要求至少月度计算 TWR；如果不计算日收益，则大额外部现金流发生时要做子期间估值，并把子期间收益几何链接。最佳实践是尽可能在所有外部现金流日期估值。

本项目采用 Portfolio Daily immutable publication：

- worker 只读 sealed manifest，以 EOD `as_of_date` 生成 daily output，并与 holding、balance、lot 和 contribution 在同一 fenced publication 内发布；
- 每个 published daily output 显式保留 `beginning_nav` 与 `ending_nav`，用户选择区间时用首日 `beginning_nav` 作为 initial value、末日 `ending_nav` 作为 final value；
- 外部现金流发生日天然拥有当日估值；
- 当前系统不另设 large cash flow threshold；
- 现金流事实当前只有日期、没有可信的日内时间戳；计算政策先把 external flow 的业务日期固定为
  cash value / `settlement_date`，再把 `deposit` 视为该估值日期初流入、把 `withdrawal` 视为该估值日期末流出。
  因此这是带明确时点假设的 valuation-subperiod TWR，不声称能还原任意日内
  现金流顺序，也不把日内时间加权精度作为合规性标签；
- 因为本系统不使用 Modified Dietz 等区间估算方法，所以任何外部现金流边界都必须有
  fresh complete valuation。无论是 resolver 明示 stale，还是周末、节假日、周频/月频 NAV 的正常 calendar
  carry，只要当日发生 deposit / withdrawal，都不能把 carried NAV 代入公式；该日及所有跨越断点的 linked
  TWR、annualized TWR、drawdown 和 contribution 必须 unavailable；
- 没有外部现金流时，calendar carry 与 resolver-explicit stale 必须分开披露。前者使用
  `carried_forward_valuation_without_external_flow`，后者使用 `stale_valuation_without_external_flow`；
  原始观察日期早于自然日不是 stale 的充分证据；
- 断点后的第一笔 fresh complete valuation 只用于重新建立 return anchor。只有后续有效子期间才能恢复 TWR；
  新查询窗口从这个 re-anchor 开始时可以正常计算，跨越断点的历史窗口仍保持 unavailable；
- 若未来支持非日频估值，必须先引入大额现金流政策和子期间 return linking；若未来支持可信的
  日内现金流时点，则必须引入日内估值或相匹配的版本化时点政策。两者都不能静默改变既有 TWR 口径。

### 2.4 方法必须一致且可解释

GIPS 强调一致应用计算方法、建立政策，并披露方法边界。

本项目采用：

- `01_CALCULATION_SPEC.md` 作为 canonical 计算政策；
- daily / holding / balance / lot / contribution 是 sealed manifest 的可复算不可变输出，不是手填事实；
- output schema 或方法变化时必须提升显式版本并生成新 run；历史 output 保留取证，读路径不翻译旧 schema；
- 事实修订在同一 PostgreSQL 事务中推进 scope generation 并写入 durable recompute intent；worker 的 lease/fencing 保证陈旧 attempt 不能发布；
- GET 只读 current publication，没有 publication 时 fail closed，pending 新 generation 时可返回带 stale/pending lineage 的旧 publication，但不同步计算或写库；
- fair-value `nav_coverage_state`、book-P&L `book_pnl_coverage_state`、TWR reliability、`stale_price_flag` 与 `stale_fx_flag` 必须分别随关键结果返回；三者不能互相代理或降级；
- 区间 daily series、summary、drawdown、calendar 与 contribution 必须由一次 consolidated performance report 读取同一 publication 和同一 window gate。

### 2.5 成本法不得污染绩效收益率

GIPS-informed 绩效口径以 fair value、外部现金流中性化和几何链接为核心。FIFO、moving average 等成本法属于 book/tax P&L 解释口径，不是 TWR 的输入政策。

本项目采用：

- 账户级成本法只影响 `Cost Basis`、`Avg Cost`、realized capital gain、unrealized P&L 和 lot 展示；
- 组合级 TWR、annualized TWR、drawdown、XIRR/MWRR 的 fair-value calculation 不读取 FIFO/MA 作为收益率分支；
- 修改账户成本法时，事实事务使 Portfolio Daily publication 失效，worker 从 transaction revisions 重算成本相关输出，而不是保留历史算法兼容层。

Performance 的 portfolio monetary bridge 使用同一会计符号契约闭合：`Initial Value + Net External Flow + Period P&L = Final Value`。金额桥与各轴 contribution 来自同一 publication 的 daily outputs，不从另一份 summary 重构。

Performance 的 attribution axis 包括 instrument、currency、account 与 taxonomy。每日返回贡献先精确闭合到 portfolio subperiod return，再以 Decimal50 Frongello 链接到所选区间，并显式披露 balancing / rounding evidence；不再发布独立的 group TWR contract。taxonomy 归因只读 sealed manifest 中的 assignment snapshot，不用 live taxonomy 重分组已发布贡献。

Holdings 只作为当前持仓状态表。资产级 TWR、区间 contribution、realized gain、income 和 closed positions 必须从 `Performance` 或 security detail 读取，避免把 current holdings 和 period performance 混成一个口径。

Holdings 中允许出现 `Chart 6M`、`1W Return / MTD / YTD / 1Y` 和 `Current DD`，但它们必须明确是 quote-derived instrument market trend：只基于标的自身 selected quote series，不读取组合现金流、数量、成本法或 realized / income events。它们用于持仓扫盘，不作为 GIPS-informed portfolio return 或 contribution disclosure。

Risk / Research 的风险统计也必须保持估值频率一致性：先按 daily / weekly / monthly calculation basis 对齐目标 period，再用 period-end 有效观测计算收益；共同节假日不生成样本，单资产缺价默认进入 `strict` missing-return 诊断。不得用跨 period stale price、缺失收益补 0、pairwise covariance entry 或不同长度持有期收益去补 covariance、correlation、Sharpe 或 target-volatility overlay。Research 只有在用户显式选择 `complete_case_drop` 且通过缺失行比例、latest complete row 新鲜度和最小完整观测数约束时，才允许整行删除缺失 period 后继续求解。

Allocation Research target solve 不允许把不可解问题包装成正常 target：多成员 scope 必须有完整有效的 `SAA` 或 `TAA` target set；`sample_covariance` 使用同一组完整对齐收益的样本估计量 `n - 1`；risk-budget 求解在完整有效收益不足、目标加总错误、missing-return policy 失败、求解误差超过 `1e-4` share units 或 signed risk share 为负时必须失败或显式 unavailable，不回退到目标权重、等权或 alternate contribution mode。

### 2.6 风险统计必须来自收益序列

GIPS 的 ex-post risk disclosure 与行业实践都要求风险统计基于收益率序列，而不是资产规模路径。

本项目采用：

- Performance consolidated report 使用完整 monthly 或 weekly calendar buckets 的 simple returns 计算标准差和 target-zero downside deviation，年化因子分别为 `12` 或 `52`；完整 bucket 的 effective return 必须从首日前一日 EOD 或更早的可靠 anchor 开始，并由 fresh endpoint 或保持同一可靠链的正常 calendar carry 覆盖到末日；首次 anchor / re-anchor 位于 bucket 中间时为 partial；partial / unavailable bucket 可展示 coverage，但不进入统计；
- 若输出正式 GIPS Composite / Pooled Fund Report 风格披露，ex-post standard deviation 必须使用 monthly returns，组合与 benchmark 必须使用同一 periodicity 与同一计算方法；
- 纯 calendar carry 日因没有新市场观察而不进入风险样本；resolver 明示 stale 的观察也必须排除。混合频率
  组合中，若一个 sleeve 有真实新观察而另一个 sleeve 仅按既定 cadence carry，calendar carry 不自动否定该日
  的 realized risk observation；
- `NAV_t` 只用于资产规模和现金流调节，不作为组合级波动率输入。
- 当前 Performance 不发布 benchmark-relative 结果。后续只能在 canonical total-return benchmark 被冻结进同一 publication 契约、币种/FX 与窗口覆盖可验证时启用，不得用 stale-filled、price/chart/valuation basis 或 raw-currency 序列代替。
- 少于一年的 observed period 不作为 annualized-return headline 展示；后端按 effective elapsed days 统一判断 365 日资格。annualized TWR 不合格时为 null；XIRR 的唯一数学解可以作为明确标注的 supplemental 结果保留，但不得冒充 headline。前端不计算日期跨度或阈值。该规则服务于当前 workbench 展示纪律，不替代正式 GIPS report 的完整披露与验证要求。
- Allocation Research 的 Policy Replay 属于假设模拟，不是 GIPS-compliant 实盘业绩；但仍采用同一反虚假精确度纪律。`allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe` 在不足 365 elapsed days 时只发布 period return，不发布 geometric annualized return 或依赖它的 Calmar；annualized volatility 与 Sharpe 属于频率明确的风险统计，Sharpe 分子使用同频收益算术均值年化，不借用几何年化收益。

## 3. 当前实现映射

| 主题 | 当前实现 |
| --- | --- |
| Valuation-subperiod TWR | `calculations/portfolio_daily/twr.py` |
| 不可变计算发布 | sealed manifest、durable worker、fenced Portfolio Daily publication |
| 区间复合与 rebasing | `calculations/portfolio_daily/reporting_engine.py` |
| Monetary bridge / return contribution | exact finite monetary bridge + Decimal50 Frongello linking and explicit balancing evidence |
| 回撤 | range-rebased method wealth index，不基于 NAV 规模 |
| 风险样本 | 完整 monthly/weekly calendar return buckets；partial bucket 不进入统计 |
| 样本协方差 | Risk 页与 Research `sample_covariance` 使用 `n - 1` 样本估计 |
| Allocation Research target solve | `_resolve_dimension_target_rows()` 校验完整 target set，`_solve_risk_budget_weights()` 在历史不足或求解失败时抛错 |
| 已发布读模型 | 按 `(run_id, output_fencing_token, natural key)` 不可变保存的 daily / holding / balance / lot / contribution outputs |
| 刷新治理 | 事实写事务原子递增 scope generation 并创建 durable recompute intent；worker 以 lease/fencing 计算，单一 current publication 指针 CAS 发布 |
| MWR | `reporting_engine.py` 使用 Actual/365 Decimal50 XIRR；少于 365 日的可解结果仅作为 supplemental |
| Performance group contribution | account / instrument / currency / taxonomy 日贡献先逐期闭合到同一 portfolio TWR，再以 Frongello 跨期链接 |
| Manual benchmark guard | 当前不发布 benchmark-relative 指标；后续只有接入同 publication 语义的 canonical total-return benchmark 后才能启用，price/chart/valuation series 不得替代 |

## 4. 暂不覆盖的 GIPS 能力

以下能力不属于当前系统边界：

- firm definition；
- composites、pooled funds 和 composite inclusion / exclusion policy；
- GIPS Composite Report / Pooled Fund Report；
- benchmark report disclosures；
- fee schedule、gross/net composite presentation；
- independent verification；
- GIPS advertising guidelines。

如果未来要声称 GIPS compliance，必须把这些能力作为独立合规工程处理，而不是只调整收益率公式。

## 5. 计算维护检查清单

任何改动 portfolio 计算层的 PR 都应检查：

- 是否改变了 external cash flow 分类；
- 是否改变了 `daily_twr` 的分母、分子或现金流时点；
- consolidated report 的 summary、`daily_series`、calendar 和 contribution 是否读取同一 publication 并按同一窗口重新复合；
- drawdown 是否基于 TWR growth index；
- Performance 统计是否只使用完整的显式 monthly / weekly calendar buckets；
- `XIRR / MWRR` 缺失是否被解释为补充指标不可用，而不是 TWR 失败；
- worker 是否只读 sealed manifest，业务 GET 是否保持零计算、零写入且不使用 live fallback；
- Research 是否拒绝缺失 target set、目标加总错误、历史不足或风险预算求解误差过大的 scope；
- `sample_covariance` 是否仍使用 `n - 1` 样本估计；
- 文档中的 canonical 口径是否同步更新。
