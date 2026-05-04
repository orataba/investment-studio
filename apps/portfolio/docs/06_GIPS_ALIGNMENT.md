# GIPS-Informed Performance Methodology

更新时间：`2026-05-04`

关联文档：

- [`01_PMS_REFERENCE_BASELINE.md`](./01_PMS_REFERENCE_BASELINE.md)
- [`04_CALCULATION_SPEC.md`](./04_CALCULATION_SPEC.md)

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

- `TTWROR` 作为 `Overview / Performance / Review` 的默认组合收益口径；
- `IRR / MWROR` 作为资金使用效率补充指标；
- UI 不允许用 IRR 替代 TWR 展示“组合收益”。

### 2.2 外部现金流必须被中性化

GIPS 强调 TWR 要中性化 client-driven external cash flows，避免把出入金时点误算为管理能力。

本项目采用：

- `deposit`、`withdrawal` 和真实组合边界分配为 external cash flow；
- `buy / sell / dividend / coupon / fee / tax / internal transfer` 不作为组合级 external cash flow；
- 日频 TTWROR 公式固定为：

```text
r_t = (MVE_t + CF_out,t) / (MVB_t + CF_in,t) - 1
```

### 2.3 每个外部现金流日期都应有估值

GIPS 要求至少月度计算 TWR；如果不计算日收益，则大额外部现金流发生时要做子期间估值，并把子期间收益几何链接。最佳实践是尽可能在所有外部现金流日期估值。

本项目采用 daily snapshot engine：

- 每个 `as_of_date` 都生成 end-of-day snapshot；
- 外部现金流发生日天然拥有当日估值；
- 现阶段不需要另设 large cash flow threshold；
- 若未来支持非日频估值，必须先引入大额现金流政策和子期间 return linking，不能直接用 Modified Dietz 静默替代 true TWR。

### 2.4 方法必须一致且可解释

GIPS 强调一致应用计算方法、建立政策，并披露方法边界。

本项目采用：

- `04_CALCULATION_SPEC.md` 作为 canonical 计算政策；
- materialized snapshot 可重建，不能成为不可解释的手填事实；
- materialized snapshot 刷新必须可重复、可追踪，并在更新并发到达时保留最新 stale 请求；
- `coverage_state`、`stale_price_flag`、`stale_fx_flag` 必须随关键结果返回；
- 区间 daily series、summary、drawdown 必须使用同一组 window-rebased `daily_ttwror`。

### 2.5 风险统计必须来自收益序列

GIPS 的 ex-post risk disclosure 与行业实践都要求风险统计基于收益率序列，而不是资产规模路径。

本项目采用：

- portfolio realized volatility、rolling volatility、Sharpe、Sortino 默认使用 `daily_ttwror` simple returns；
- 非市场观察日的 stale-price carry-forward 0 return 不进入风险样本；
- `NAV_t` 只用于资产规模和现金流调节，不作为组合级波动率输入。

## 3. 当前实现映射

| 主题 | 当前实现 |
| --- | --- |
| 日频 TWR | `build_daily_portfolio_snapshots()` 生成 `daily_ttwror` |
| 几何复合 | `_compound_daily_ttwror()` 与区间 `daily_series.cumulative_ttwror` |
| 区间 rebasing | `_rebased_ttwror_series()` 对查询窗口重算 TWR index 和 drawdown |
| 回撤 | `_drawdown_stats()` 基于 TWR growth index，不基于 NAV |
| 风险样本 | `return_observation_eligible` 控制 realized risk 的有效收益观察 |
| 物化读模型 | `PortfolioDailySnapshotModel` / holding snapshot / contribution slice |
| 刷新治理 | `PortfolioCalculationStateModel.refresh_request_id` 对 stale 请求去重，刷新串行 claim；计算期间若收到新请求会再跑一轮 |
| MWR | `_solve_xirr()` 输出 `irr` / `mwror`，作为补充指标 |

## 4. 暂不覆盖的 GIPS 能力

以下能力不属于当前阶段：

- firm definition；
- composites、pooled funds 和 composite inclusion / exclusion policy；
- GIPS Composite Report / Pooled Fund Report；
- benchmark report disclosures；
- fee schedule、gross/net composite presentation；
- independent verification；
- GIPS advertising guidelines。

如果未来要声称 GIPS compliance，必须把这些能力作为独立合规工程处理，而不是只调整收益率公式。

## 5. 提交前计算检查清单

任何改动 portfolio 计算层的 PR 都应检查：

- 是否改变了 external cash flow 分类；
- 是否改变了 `daily_ttwror` 的分母、分子或现金流时点；
- 区间 summary 和 `daily_series` 是否都按查询窗口重新复合；
- drawdown 是否基于 TWR growth index；
- risk 是否只使用符合 `return_observation_eligible` 的 `daily_ttwror`；
- `IRR / MWROR` 缺失是否被解释为补充指标不可用，而不是 TWR 失败；
- materialized read path 和 dynamic fallback path 是否结果一致；
- 文档中的 canonical 口径是否同步更新。
