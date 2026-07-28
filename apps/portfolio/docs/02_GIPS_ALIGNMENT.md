# Portfolio GIPS-Informed Performance Methodology

> 适用范围：仅适用于 `apps/portfolio` 的组合级绩效计算与呈现。Watchlist Instrument Detail 的 Performance 是单资产 NAV/price 与 benchmark 分析，不属于本文合同。

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

本项目目标口径：

- `TWR` 作为 `Overview / Performance` 的默认组合收益口径；
- `IRR / MWROR` 作为资金使用效率补充指标；
- UI 不允许用 IRR 替代 TWR 展示“组合收益”。

### 2.2 外部现金流必须被中性化

GIPS 强调 TWR 要中性化 client-driven external cash flows，避免把出入金时点误算为管理能力。

本项目目标口径：

- `deposit`、`withdrawal` 和真实组合边界分配为 external cash flow；
- `buy / sell / dividend / coupon / fee / tax / internal transfer` 不作为组合级 external cash flow；
- external cash flow 默认在实际收到或支付的日期生效；只有资金已预先获知且当日确实可投资，并且存在书面且一致执行的政策时，才允许使用更早日期；
- 日频 TWR 公式固定为：

```text
r_t = (MVE_t + CF_out,t) / (MVB_t + CF_in,t) - 1
```

### 2.3 每个外部现金流日期都应有估值

GIPS 要求至少月度计算 TWR；如果不计算日收益，则大额外部现金流发生时要做子期间估值，并把子期间收益几何链接。最佳实践是尽可能在所有外部现金流日期估值。

本项目采用 daily snapshot engine：

- 每个 `as_of_date` 都生成 end-of-day snapshot；
- 每个 daily snapshot 显式保留 `beginning_nav` 与 `ending_nav`；组合已经存在时，显式区间把 `start_date`、`end_date` 都解释为 EOD boundary，使用首日 `ending_nav` 作为 initial value、末日 `ending_nav` 作为 final value，并只链接 `(start_date, end_date]` 的日收益；
- 普通起始日事实已经进入 initial value，不再重复计入区间 external flow、P&L 或 contribution。请求起点等于 funded-segment start（首次入金，或 NAV 真正归零后的再次入金）时，显式区间保留该日 BOD-to-EOD 子期间；导入式 opening balance 则作为 EOD anchor。留存现金仍属于组合资产，中途新买 instrument 也不构成 segment restart；
- 组合 NAV 归零后的无资本日期不生成虚构的 0% return observation。跨越这种 inactive gap 的连续 TWR fail closed，归零前后的 funded segments 分别计算；
- 外部现金流发生日天然拥有当日估值；
- buy / sell 是内部资产转换，不作为外部流；系统保留实际成交金额、交易日头寸与 pending settlement，成交价到 EOD fair value 的变化进入当日 P&L/TWR；
- 日频外部流目前采用 BOD contribution / EOD withdrawal convention。只有具备流发生时点前后的完整组合估值时，才能把盘中大额流拆成精确子期间；交易时间本身不能替代估值；
- 当前系统不另设 large cash flow threshold；
- 若未来支持非日频估值，必须先引入大额现金流政策和子期间 return linking，不能直接用 Modified Dietz 静默替代 true TWR。

### 2.4 方法必须一致且可解释

GIPS 强调一致应用计算方法、建立政策，并披露方法边界。

本项目采用：

- `01_CALCULATION_SPEC.md` 作为 canonical 计算政策；
- materialized snapshot 可重建，不能成为不可解释的手填事实；
- materialized snapshot / contribution slice schema 变化通过 `calculation_version` 失效重建，不在收益读路径保留旧 schema 兼容层；
- materialized snapshot 刷新必须可重复、可追踪，并在更新并发到达时保留最新 stale 请求；
- 公允价值估值、return chain、book P&L 与 attribution 必须分别返回 coverage / reliability；单一 `coverage_state` 不得让成本或归因缺口阻断本来完整的 fair-value NAV/TWR；
- `stale_price_flag`、`stale_fx_flag` 及 requested/effective as-of 必须随关键结果返回；
- 区间 daily series、summary、drawdown 必须使用同一组 window-rebased `daily_twr`。
- MTD / QTD / YTD 分别使用上月末 / 上季末 / 上年 `12-31` EOD anchor；休市目标日的组合使用完整 EOD carry state，标的和 benchmark 使用目标日或之前最近一个有效收盘；
- external flow 落在不可靠估值 gap 内时，不跨 gap 几何链接；下一个可靠点只建立新 anchor。自然月、MTD/QTD/YTD 等期间还必须有可靠起止边界和连续 return coverage；
- 用户请求终点超过 latest reliable endpoint 时，后端 clamp 到有效终点并返回 requested/effective as-of 与原因，所有区间组件共用这一终点。

### 2.5 成本法不得污染绩效收益率

GIPS-informed 绩效口径以 fair value、外部现金流中性化和几何链接为核心。FIFO、moving average 等成本法属于 book/tax P&L 解释口径，不是 TWR 的输入政策。

本项目采用：

- 账户级成本法只影响 `Cost Basis`、`Avg Cost`、realized capital gain、unrealized P&L 和 lot 展示；
- 组合级 TWR、annualized TWR、drawdown、IRR/MWROR 的 fair-value calculation 不读取 FIFO/MA 作为收益率分支；
- 修改账户成本法时，系统从 transaction facts 重算成本相关 read models，而不是保留历史算法兼容层。

Performance `Calculation` 使用 period bridge：`Initial Value + Net External Flow + Period P&L = Final Value`。其中 capital gain 使用 fair-value period basis：显式区间按 `start_date` EOD 市值重置期初持仓，只重放 `(start_date, end_date]` 内交易，期末仍持有部分形成 unrealized gain。这个拆分服务绩效解释，不读取 FIFO / moving average 的 book cost 分支。
Calculation 的 group axis 包括 instrument、instrument type、currency、account 与 planning taxonomy；TWR 和 contribution 必须在后端按目标轴从 daily slices 计算，不能在前端简单汇总 instrument rows。Group daily return 必须使用组内 `total_pnl / (beginning_value + period capital flow in)`，taxonomy regroup 与 calculation detail 聚合也必须保留同一 capital-flow denominator。taxonomy period view 优先使用区间期末 assignment；期末已清仓且期末不再有 active assignment 的 instrument，使用其区间内有效 assignment 承接历史 P&L，避免把 closed-position attribution 误列为 Unassigned。

Holdings 只作为当前持仓状态表。资产级 TWR、区间 contribution、realized gain、income 和 closed positions 必须从 `Performance` 或 security detail 读取，避免把 current holdings 和 period performance 混成一个口径。

Holdings 中允许出现 `Chart 1M / 3M / 6M / 1Y`、`1W / 1M / 3M / 6M / MTD / YTD / 1Y Return`、Volatility 和 Drawdown。标的 Return / Volatility / Drawdown 只使用 Registry 已确认的 total-return series，不读取组合现金流、数量、成本法或 realized / income events；Chart 可以使用明确标注的 price-return 或 total-return path，但不能反向成为分析字段输入。`Held Max DD` 是从当前最早开放持仓日起截断的 instrument-row 辅助指标，不合成 group。

Holdings group / subtotal / total 的 Return 和 Risk 是当前 market-value 权重篮子在共同历史区间上的假设回看，不是实际组合 TWR、period contribution 或 GIPS-informed performance disclosure。真实历史组合表现仍只来自 Overview / Performance；逐字段边界见 [`03_HOLDINGS_FIELD_REFERENCE.md`](./03_HOLDINGS_FIELD_REFERENCE.md)。

Risk / Research 的风险统计也必须保持估值频率一致性：1M / 3M 等窗口按请求 as-of 回看自然月形成 EOD boundary，并只链接 `(start EOD, end EOD]` 的 return rows；不能把 start 当天结束的前一区间收益带入。先按 daily / weekly / monthly calculation basis 对齐目标 period，再用 period-end 有效观测计算收益；同一矩阵行的 period start 与 end 都必须一致。共同节假日不生成样本，单资产缺价默认进入 `strict` missing-return 诊断。不得用跨 period stale price、缺失收益补 0、静默取日期交集、pairwise covariance entry 或不同长度持有期收益去补 covariance、correlation、Sharpe 或 target-volatility overlay。`strict` 也必须校验 latest complete row 的尾部新鲜度；Research 只有在用户显式选择 `complete_case_drop` 且通过缺失行比例、latest complete row 新鲜度和最小完整观测数约束时，才允许整行删除缺失 period 后继续求解。

Research target solve 不允许把不可解问题包装成正常 target：多成员 scope 必须有完整有效的 `SAA` 或 `TAA` target set；`sample_covariance` 使用同一组完整对齐收益的样本估计量 `n - 1`；risk-budget 求解在完整有效收益不足、目标加总错误、missing-return policy 失败、求解误差超过 `1e-4` share units 或 signed risk share 为负时必须失败或显式 unavailable，不回退到目标权重、等权或 alternate contribution mode。

### 2.6 风险统计必须来自收益序列

GIPS 的 ex-post risk disclosure 与行业实践都要求风险统计基于收益率序列，而不是资产规模路径。

本项目采用：

- portfolio realized volatility、rolling volatility 默认使用 `daily_twr` simple returns 做标准差并年化；Sharpe、Sortino 作为 additional risk measures，使用同一区间、同一 periodicity 的 arithmetic mean excess return 年化后除以年化 volatility / downside volatility（MVP `r_f = 0`）；
- 若输出正式 GIPS Composite / Pooled Fund Report 风格披露，3-year ex-post standard deviation 必须使用 36 个 monthly returns，组合与 benchmark 必须使用同一 periodicity 与同一计算方法；
- 非市场观察日的 stale-price carry-forward 0 return 不进入风险样本；
- `NAV_t` 只用于资产规模和现金流调节，不作为组合级波动率输入。
- 手动 benchmark 对比只有在币种一致、起点锚点存在且 benchmark 覆盖组合 eligible return dates 时才输出；当前未接入后端 benchmark FX conversion / coverage reporting，不能用 stale-filled 或 raw-currency 序列替代。

### 2.7 少于一年不得年化

本项目对 TWR 与 MWR 使用同一披露门槛：

- 测量期不足一年时，可以展示 cumulative / period return；
- 不得展示 annualized TWR、annualized MWR / XIRR、Calmar 或其他依赖年化收益的结果；
- eligibility 必须由后端 canonical contract 返回，不能只在某一个页面隐藏；
- 满一年门槛按 calendar anniversary 判定，TWR 年分数使用 anniversary-aware Actual/Actual，并保持 API、UI、export 一致。

### 2.8 费用口径必须明确

当前交易模型只能证明已记录的 fees、taxes 与 transaction costs 已进入现金和 NAV，尚不能证明 investment-management fee accrual 完整，也没有足够分类生成正式 gross-of-fees / net-of-fees return。

因此当前收益只能描述为 **after recorded expenses**。在费用分类与完整性校验完成前：

- 不得标记为 GIPS gross-of-fees 或 net-of-fees；
- transaction costs 不得从 gross return 中加回；
- `fee_category` 缺失时返回 `unknown`，不能猜测 management / custody / transaction-cost 分类。

固定收益 fair value 使用 dirty-price boundary。percent-of-par 报价必须带明确 price unit/scale；clean price 只有在同日、同币种、同 unit/scale 的 accrued interest 唯一存在时才可合成 dirty price。clean-only 或身份含混的数据不得进入正式 NAV/TWR。

## 3. 当前实现映射与边界

| 主题 | 当前实现 |
| --- | --- |
| 日频 TWR | `build_daily_portfolio_snapshots()` 生成 `daily_twr` |
| 几何复合 | `_compound_daily_twr()` 与区间 `daily_series.cumulative_twr` |
| 区间 rebasing | `_rebased_twr_series()` 对查询窗口重算 TWR index 和 drawdown |
| 区间边界 | 已有组合的显式 `(start_date, end_date]` 使用 start/end 的 `ending_nav`；起点等于 funded-segment start 时保留该日 BOD-to-EOD 子期间，跨零 NAV inactive gap 不链接 |
| 回撤 | `_drawdown_stats()` 基于 TWR growth index，不基于 NAV |
| 风险样本 | `return_observation_eligible` 控制 realized risk 的有效收益观察 |
| 样本协方差 | Risk 页与 Research `sample_covariance` 使用 `n - 1` 样本估计 |
| Research target solve | `_resolve_dimension_target_rows()` 校验完整 target set，`_solve_risk_budget_weights()` 在历史不足或求解失败时抛错 |
| 物化读模型 | `PortfolioDailySnapshotModel` / holding snapshot / contribution slice |
| 刷新治理 | `PortfolioCalculationStateModel.refresh_request_id` 对 stale 请求去重，刷新串行 claim；计算期间若收到新请求会再跑一轮 |
| Source generation fence | snapshot 计算前、计算后与 publish 前核对源 generation；变化时丢弃并重试，不发布混合世代结果 |
| MWR | `_solve_xirr_result()` 只发布 `unique_root`；`no_root` / `multiple_roots` / `invalid_cash_flows` 显式不可用 |
| Performance group TWR | `_daily_group_return_from_components()` 在 instrument / taxonomy / calculation detail 聚合间复用同一 flow-adjusted denominator |
| Manual benchmark guard | Performance 页面要求同币种、起点锚点和 eligible return date 覆盖完整后才展示轻量 benchmark metrics |
| External-flow effective date | deposit / withdrawal 使用显式 external-flow date 或 settlement date；dividend / coupon 的经济日期可来自 entitlement date |
| 少于一年年化 | 后端以 calendar-anniversary eligibility 直接 withholding annualized TWR/MWR 与 Calmar；UI/export 沿用同一状态 |
| 费用 basis | 当前只能标记 `after recorded expenses`，尚不支持正式 gross/net-of-fees 声明 |
| Coverage 分层 | API 与物化 snapshot 分别返回 valuation / return / book-P&L / attribution coverage，成本或归因缺口不反向污染完整 NAV/TWR |

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
- external cash flow 是否仍按实际收付日生效，例外是否有显式政策；
- 是否改变了 `daily_twr` 的分母、分子或现金流时点；
- 区间 summary 和 `daily_series` 是否都按查询窗口重新复合；
- drawdown 是否基于 TWR growth index；
- risk 是否只使用符合 `return_observation_eligible` 的 `daily_twr`；
- `IRR / MWROR` 缺失是否被解释为补充指标不可用，而不是 TWR 失败；
- XIRR 是否只发布唯一有效 root，并保留 solver status；
- 少于一年时后端是否拒绝发布 annualized TWR / MWR / Calmar；
- 收益 basis 是否仍只描述为 after recorded expenses，除非 fee classification 与完整性已被证明；
- valuation / return / book-P&L / attribution coverage 是否保持分离；
- materialized read path 和动态重建校验路径是否结果一致；
- Research 是否拒绝缺失 target set、目标加总错误、历史不足或风险预算求解误差过大的 scope；
- `sample_covariance` 是否仍使用 `n - 1` 样本估计；
- 文档中的 canonical 口径是否同步更新。
