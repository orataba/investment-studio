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
- 外部现金流发生日必须具备当日有效估值；若必要价格或 FX 缺失，同样在首个缺口停止，现金流记录本身不能证明估值完整；
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
- 经营收益的区间 daily series 与 summary 必须使用同一组 window-rebased `daily_twr`；风险 summary、risk drawdown 与 realized risk attribution 必须使用同一组 window-rebased `market_risk_daily_return`，两条链不得混用。
- MTD / QTD / YTD 分别使用上月末 / 上季末 / 上年 `12-31` EOD anchor；休市目标日的组合使用完整 EOD carry state，标的和 benchmark 使用目标日或之前最近一个有效收盘；
- 来源日历确认的休市 carry 可以形成完整 EOD 边界，并保留真实行情日期。预期交易日报价或必要 FX 缺失时在首个缺口停止，只发布此前可靠前缀；缺口补齐并重算前，后续价格恢复也不能重新建立 anchor。自然月、MTD/QTD/YTD 等期间还必须有可靠起止边界和连续 return coverage；
- 初次买入确认日无正式价格时，系统采用明确披露的成交基准初始估值：当日买入总 gross / 总 quantity，排除费用税费；正式价优先，后续仅合法休市可沿用，下一交易日继续严格要求正式价。该例外不包含非现金交付和期权实物交付关联的股票腿。它不是该日官方 EOD 行情，不能构造正式报价身份或市场风险观察；该初始估值约定不构成 GIPS compliance 声明；
- 用户请求终点超过 latest reliable endpoint 时，后端 clamp 到有效终点并返回 requested/effective as-of 与原因，所有区间组件共用这一终点。

### 2.5 成本法不得污染绩效收益率

GIPS-informed 绩效口径以 fair value、外部现金流中性化和几何链接为核心。FIFO、moving average 等成本法属于 book/tax P&L 解释口径，不是 TWR 的输入政策。

本项目采用：

- 账户级成本法只影响 `Cost Basis`、`Avg Cost`、realized capital gain、unrealized P&L 和 lot 展示；
- 公允价值覆盖完整时，组合级 TWR、annualized TWR、drawdown、IRR/MWROR 的 fair-value calculation 不读取 FIFO/MA 作为收益率分支；
- 已有资产交易后禁止更改账户成本法。FIFO 的默认释放顺序取原始 acquisition date 与开仓事实顺序，历史导入、转仓和拆股不重置取得先后；显式指定批次另按已记录选择执行。

FCN、长期权和期权卖方义务当前采用明确的 event-accounting boundary，不构成公允价值例外：

- 没有可靠 fair value 的 FCN/长期权只按 remaining transaction basis carried；written option liability 只按 remaining premium basis carried；
- event purchase charges 当日 expense，writer premium 在 sell-to-open 时先建立等额 liability，在 buy-to-close、writer expiry、writer cash settlement 或已确认实物 assignment 时按关闭份额释放并确认 option P&L；option contract 不预设 settlement mode；
- 总 NAV 为了资产负债表对账仍包含这些 carrying amounts，内部也可以产生 flow-neutral operational/carrying-basis daily return；
- operational daily return 可以形成独立、明确命名的经营收益链，用于 NAV reconciliation 与经营复盘，但不得命名为完整 fair-value 或 GIPS-informed return，也不得直接输入风险统计；
- volatility、Sharpe、Sortino、Calmar、risk drawdown、benchmark risk compare 与 realized risk attribution 使用独立 `Market Risk Return`：同一总 NAV 分母中将 base-currency cash 与衍生品作为 0-return capital，并从风险 P&L 分子剔除衍生品 cash result/FCN coupon/charges 和现金利息。普通证券 total return 与普通证券 income 保留，non-base cash FX 仍属于市场风险；
- 期权实物行权/指派在股票腿形成的 strike-to-market 交割损益及交割费用也从风险分子剔除；否则期权兑现会伪装成股票市场收益。股票交割后真实市场变动照常进入风险链，期权 premium book P&L 与股票 strike book cost 的账本政策保持独立；
- Holdings 的 event day change、fair value 和 quote identity 必须为空，priced coverage 不包含 event rows；Risk 只对 当前 policy 明确标记 `risk_eligible=true` 的 modeled market sleeve 建模，并披露 excluded carrying value/liability、coverage ratio 和 excluded rows；
- 因此任何包含 material event-valued asset 或 derivative liability 的 operational result 和 `Market Risk Return` 都不能命名为完整 fair-value 或 GIPS-informed TWR。0-return 是明确的风险建模约定，不是对衍生品未知公允价值收益的估计。
- 当前没有逐日 sleeve cash subledger，ordinary-sleeve TWR 明确 unavailable；从 total operational return 中过滤 derivative rows 不是可接受的 performance scope 计算。

Performance `Calculation` 使用 period bridge：`Initial Value + Net External Flow + Period P&L = Final Value`。其中 capital gain 使用 fair-value period basis：显式区间按 `start_date` EOD 市值重置期初持仓，只重放 `(start_date, end_date]` 内交易，期末仍持有部分形成 unrealized gain。这个拆分服务绩效解释，不读取 FIFO / moving average 的 book cost 分支。
Calculation 的 group axis 包括 instrument、instrument type、currency、account 与 planning taxonomy；TWR 和 contribution 必须在后端按目标轴从 daily slices 计算，不能在前端简单汇总 instrument rows。Group daily return 必须使用组内 `total_pnl / (beginning_value + period capital flow in)`，taxonomy regroup 与 calculation detail 聚合也必须保留同一 capital-flow denominator。taxonomy period view 按当前 assignment 重述整个历史区间，并保留 cash 独立组；已清仓 instrument 的历史 P&L 也按当前归属分组，没有当前 active assignment 时列为 Unassigned，不回退读取旧分类。

逐日贡献的算术和在 Calculation 中明确命名为 `Arithmetic Return Contribution`，与几何区间 TWR 的差额单列 `TWR Linking Difference`；它不是额外经济损益。表格导出保留该对平关系，并披露有效期间、本位币、收益 basis、起点边界、估值时区/截止规则、已记录费用、风险方法和百分比单位。

红利再投资在权益日确认收入，份额延后生效时先在原证券账户记录应收，确认日再转为有成本的新 lot；不得在等待期间漏掉资产或提前制造份额。关联资产的独立 fee / tax 未指定历史权益日时按费用发生前的实际同日顺序校验，支持先开仓后收费；显式历史权益日仍按 BOD 权益口径。现金同日同时间事实通过 `transaction_sequence` 重放，不能由页面排序改变汇率成本。

Holdings 只作为当前持仓状态表。资产级 TWR、区间 contribution、realized gain、income 和 closed positions 必须从 `Performance` 或 security detail 读取，避免把 current holdings 和 period performance 混成一个口径。

Holdings 中允许出现 `Chart 1M / 3M / 6M / 1Y`、`1W / 1M / 3M / 6M / MTD / YTD / 1Y Return`、Volatility 和 Drawdown。标的 Return / Volatility / Drawdown 只使用 Registry 已确认的 total-return series，不读取组合现金流、数量、成本法或 realized / income events；Chart 可以使用明确标注的 price-return 或 total-return path，但不能反向成为分析字段输入。`Held Max DD` 是从当前最早开放持仓日起截断的 instrument-row 辅助指标，不合成 group。

Holdings group / subtotal / total 的 Return 和 Risk 是当前 market-value 权重篮子在共同历史区间上的 theoretical / hypothetical 回看，不是实际组合 TWR、period contribution 或 GIPS-informed performance disclosure。它们必须以 `Current Basket` 明确标识，不得与实际组合收益链接成一条历史，也不得被描述为客户组合实际绩效。真实历史组合表现仍只来自 Overview / Performance；逐字段边界见 [`03_HOLDINGS_FIELD_REFERENCE.md`](./03_HOLDINGS_FIELD_REFERENCE.md)。

Risk / Research 的风险统计固定使用日频估值路径：1M / 3M 等窗口按请求 as-of 回看自然月形成 EOD boundary，并只链接 `(start EOD, end EOD]` 的 return rows；不能把 start 当天结束的前一区间收益带入。不同发布节奏的来源按最近有效 mark 构造日频组合路径，来源更新日确认变化；所有来源都未更新的日期不生成风险样本。预期应更新却缺失的数据进入 `risk_basis` incomplete 诊断，不得被 carry-forward 掩盖。协方差、相关性、Sharpe 与 target-volatility overlay 使用同一组日频日期和实际观察密度。

Research target solve 不允许把不可解问题包装成正常 target：多成员 scope 必须有完整有效的 `SAA` 或 `TAA` target set；`sample_covariance` 使用同一组完整对齐收益的样本估计量 `n - 1`；risk-budget 求解在完整有效收益不足、目标加总错误、missing-return policy 失败、求解误差超过 `1e-4` share units 或 signed risk share 为负时必须失败或显式 unavailable，不回退到目标权重、等权或 alternate contribution mode。

Research 的历史结果属于 current-target historical simulation：本次运行冻结当前分类、成员、目标与求解资格，全段历史使用同一快照，市场观察截止各决策日。它不是实际客户组合绩效或 GIPS presentation，不声称当前目标在历史上已经已知。模拟以组合成立日收盘的真实持仓、现金及结算余额为起点，不重复计入成立日收益或初始买入费用；初始证券持续获得后续收益，直至首次可执行目标调仓，不能因风险样本不足改成现金。后续实际证券交易与申赎不注入模型路径，已披露的衍生品固定资金生命周期例外保留。模型包含现金收益、commission、sell-side tax、slippage 与 EOD implementation delay；目标调仓必须晚于成立日收盘，并假设在满足估值条件的执行观察日完整成交。当前没有 order rejection、partial fill、流动性容量或 market-impact 模型。报告保留目标快照、历史数据覆盖、skipped rebalances、execution records 和 contribution reconciliation。

### 2.6 风险统计必须来自收益序列

GIPS 的 ex-post risk disclosure 与行业实践都要求风险统计基于收益率序列，而不是资产规模路径。

本项目采用：

- portfolio realized volatility、rolling volatility 默认使用 `market_risk_daily_return` simple returns 做标准差并年化；Sharpe、Sortino 作为 additional risk measures，使用同一区间、同一 periodicity 的 arithmetic mean excess return 年化后除以年化 volatility / downside volatility（MVP `r_f = 0`）；
- Calmar 使用同一区间市场风险收益的几何年化收益除以最大回撤绝对值，采用自然周年门槛及 anniversary-aware Actual/Actual 年分数；不使用算术年化均值。风险状态不可用时，mean、volatility / downside volatility、Sharpe / Sortino 等样本统计留空，保留覆盖、样本数和不可用原因；完整收益链上独立计算的合法 drawdown 可以保留在 API，Performance headline 风险展示仍服从风险状态；
- 若输出正式 GIPS Composite / Pooled Fund Report 风格披露，3-year ex-post standard deviation 必须使用 36 个 monthly returns，组合与 benchmark 必须使用同一 periodicity 与同一计算方法；
- 所有必要市场与 FX 来源均未更新的合法休市日不生成风险样本；预期缺价不以 carry-forward 0 return 补齐；
- event-valued carrying asset、premium-basis liability 和 derivative lifecycle activity 对应的 operational return 不进入风险样本；它们的资本通过 total NAV 分母按 0-return capital 进入 `market_risk_daily_return`；
- `NAV_t` 只用于资产规模和现金流调节，不作为组合级波动率输入。
- 手动 benchmark 对比只有在币种一致、所选期间起点锚点存在且 benchmark 覆盖全部组合 eligible return dates 时才输出；不得仅取期间内有数据的尾段冒充全期。Benchmark 几何年化与组合采用相同自然周年门槛和 Actual/Actual 年分数；当前未接入后端 benchmark FX conversion / coverage reporting，不能用 stale-filled 或 raw-currency 序列替代。

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

直接债券当前不在 Registry 或 Portfolio 交易范围内，因此系统不发布直接债券 fair value 或相关 GIPS 口径。债券公募/私募与债券 ETF 仍按各自 `public_fund / private_fund / etf` 行情和收益合同处理。

## 3. 当前实现映射与边界

| 主题 | 当前实现 |
| --- | --- |
| 日频 TWR | `build_daily_portfolio_snapshots()` 生成 `daily_twr` |
| 几何复合 | `_compound_daily_twr()` 与区间 `daily_series.cumulative_twr` |
| 区间 rebasing | `_rebased_twr_series()` 对查询窗口重算 TWR index 和 drawdown |
| 区间边界 | 已有组合的显式 `(start_date, end_date]` 使用 start/end 的 `ending_nav`；起点等于 funded-segment start 时保留该日 BOD-to-EOD 子期间，跨零 NAV inactive gap 不链接 |
| 回撤 | 经营曲线按 `daily_twr` growth index；risk drawdown 按 `market_risk_daily_return` growth index，均不基于 NAV |
| 风险样本 | `market_risk_return_observation_eligible` 控制 realized risk 的有效收益观察 |
| 样本协方差 | Risk 页与 Research `sample_covariance` 使用 `n - 1` 样本估计 |
| Research target solve | `_resolve_dimension_target_rows()` 校验完整 target set，`_solve_risk_budget_weights()` 在历史不足或求解失败时抛错 |
| Scoped forward risk | covariance 只纳入 当前 policy 中 `risk_eligible=true` 的市场资产，权重为 signed market exposure / total NAV；衍生品与本币 monetary rows 按 0-return capital，非本币 monetary exposure 或 eligible matrix 不完整时 unavailable |
| Research historical simulation | 当前完整目标快照固定用于全部历史决策；行情按决策日截断，计入 cash yield、commission、sell tax、slippage 与 delay，输出滚动历史窗口和 contribution reconciliation；不声称历史目标重放或独立样本外验证 |
| 物化读模型 | `PortfolioDailySnapshotModel` / holding snapshot / contribution slice |
| 刷新治理 | `PortfolioCalculationStateModel.refresh_request_id` 对 stale 请求去重，刷新串行 claim；计算期间若收到新请求会再跑一轮 |
| Source generation fence | snapshot 计算前、计算后与 publish 前核对源 generation；变化时丢弃并重试，不发布混合世代结果 |
| MWR | `period_metrics.solve_xirr_result()` 在允许利率域内按导数临界点隔离真实根，只发布 `unique_root`；`no_root` / `multiple_roots_or_non_unique` / `invalid_cash_flows` 显式不可用；符号变化次数不等于实际根数 |
| Performance group TWR | `_daily_group_return_from_components()` 在 instrument / taxonomy / calculation detail 聚合间复用同一 flow-adjusted denominator |
| Manual benchmark guard | Performance 页面要求同币种、全期起点锚点和全部 eligible return date 覆盖完整后才展示轻量 benchmark metrics |
| External-flow effective date | deposit / withdrawal 使用 settlement date，缺失时回退 trade date；响应中的 external_flow_date 是派生值；dividend / coupon / dividend_reinvestment 的收入确认可来自 entitlement date |
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
- 经营 drawdown 是否基于 `daily_twr` growth index，risk drawdown 是否基于 `market_risk_daily_return` growth index；
- risk 是否只使用符合 `market_risk_return_observation_eligible` 的 `market_risk_daily_return`；
- `IRR / MWROR` 缺失是否被解释为补充指标不可用，而不是 TWR 失败；
- XIRR 是否只发布唯一有效 root，并保留 solver status；
- 少于一年时后端是否拒绝发布 annualized TWR / MWR / Calmar；
- 收益 basis 是否仍只描述为 after recorded expenses，除非 fee classification 与完整性已被证明；
- valuation / return / book-P&L / attribution coverage 是否保持分离；
- materialized read path 和动态重建校验路径是否结果一致；
- Research 是否拒绝缺失 target set、目标加总错误、历史不足或风险预算求解误差过大的 scope；
- Research 是否保存并全程使用同一当前目标快照，仍按决策日截断行情，并披露现金收益、交易摩擦、延迟、滚动历史窗口与尚未建模的成交限制；
- scoped risk 是否披露 excluded carrying value/liability、coverage 与 excluded rows，且没有 eligible risky holding 时明确 unavailable；
- `sample_covariance` 是否仍使用 `n - 1` 样本估计；
- 文档中的 canonical 口径是否同步更新。
