# 05 Information Architecture

> 说明：这份 IA 文档保留目标态工作面设计。当前实际实现以 [README.md](../README.md) 为准；当前状态总览统一命名为 `Overview`，`Snapshot` 仅作为后端派生对象语义保留。

## 1. Purpose

这份文档的目标是把前四份已经确定的产品、领域模型和计算规格，映射成一套稳定的信息架构。

它主要回答：

- 系统的全局壳层如何组织；
- 单组合 workspace 里有哪些正式工作面；
- 每个工作面解决什么问题；
- 各工作面消费哪些对象；
- 页面之间如何 drill-down，而不形成重复页面；
- 哪些配置应放在高频工作面之外。

这份文档不是：

- 视觉设计稿；
- 组件级交互说明；
- API 设计文档；
- 前端技术实现细节。

## 2. IA Principles

### 2.1 Portfolio-First Shell

系统先进入 `Portfolios`，再进入单组合 workspace。  
不采用“从一个全局大看板直接跳进零散工具页”的结构。

### 2.2 One Question, One Owner Page

每个一级工作面都必须有清晰的主问题：

- `Overview`：现在整体怎样
- `Holdings`：现在持有什么
- `Performance`：这段时间赚了多少
- `Risk`：现在风险在哪里、偏了多少
- `Transactions`：原始账本流水是什么
- `Accounts`：底层账户结构是什么
- `Review`：一个周期内发生了什么
- `Research`：当前组合有哪些研究运行结果

同一个问题不应在多个一级页面重复出现。

### 2.3 Separate Current State, Period Analysis, Ledger, Config

首版页面分为五类：

- 当前状态页：`Overview`、`Holdings`、`Risk`
- 区间分析页：`Performance`、`Review`
- 账本页：`Transactions`、`Accounts`
- 运行页：`Research`
- 配置页：`Portfolio Configure`、`Portfolio Configure / Taxonomies`

### 2.4 Shared Context Must Be Explicit

所有页面共享的分析上下文都必须显式展示，而不是隐式继承：

- `portfolio`
- `as_of_date` 或 `period`
- `benchmark`
- `taxonomy`
- `target_resolution_mode`
- `base_currency`
- `coverage / unavailable state`

### 2.5 Drawers Over Route Sprawl

首版尽量避免为单资产、单节点、单事件再开一级页面。

优先采用：

- 当前页面内的 detail pane
- 过滤后的同页视图
- 带上下文的跳转

而不是生成大量碎路由。

### 2.6 Config Stays Outside High-Frequency Tabs

`Taxonomies` 不进入主导航；  
`AlertRule` 也不单独做一级页面。

配置能力应放在：

- `Portfolio Configure`
- `Portfolio Configure / Taxonomies`
- `Risk` 工作面中的 `Limits & Alerts` 管理区

## 3. Global Shell

### 3.1 Primary Navigation

全局主导航固定为：

1. `Portfolios`
2. `Settings`

### 3.2 Portfolios

`Portfolios` 是系统主入口，负责：

- portfolio list
- 组合状态与基础摘要
- 快速进入单组合 workspace
- portfolio create / archive / open

它不负责：

- 统一分析多个组合的复杂报表
- 组合内 taxonomy 配置
- 研究配置管理

### 3.3 Settings

`Settings` 只承载系统级共享对象：

- data sources
- benchmark library
- instrument / market-data integration settings
- optional taxonomy templates

它不承载：

- portfolio-scoped benchmark assignments
- portfolio-scoped active taxonomies
- portfolio-scoped target sets
- portfolio-scoped alert rules

## 4. Portfolio Workspace

### 4.1 Workspace Layout

单组合 workspace 使用统一布局：

1. **Context Header**
   显示 portfolio name、NAV、日变化、状态、最新完整 `as_of_date`
2. **Primary Navigation**
   切换一级工作面
3. **Page Toolbar**
   承载该页面自己的上下文与过滤器
4. **Analytic Canvas**
   页面主内容区
5. **Detail Pane / Linked View**
   显示单资产或单节点的深入信息

### 4.2 Primary Navigation Order

单组合一级导航固定为：

1. `Overview`
2. `Holdings`
3. `Performance`
4. `Risk`
5. `Transactions`
6. `Accounts`
7. `Review`
8. `Research`

另有配置入口：

- `Portfolio Configure`
- `Portfolio Configure / Taxonomies`

### 4.3 Shared Context Model

| Context | Applies To | Default Rule | Notes |
| --- | --- | --- | --- |
| `portfolio_id` | all pages | current workspace portfolio | 不允许跨页面隐式切到其他组合 |
| `as_of_date` | `Overview` / `Holdings` / `Risk` | latest complete `as_of_date` | 当前状态页统一使用 |
| `period` | `Performance` / `Review` | user-selected | 区间页统一使用 |
| `resolved_primary_benchmark_assignment_id` | `Overview` / `Holdings` / `Risk` | resolved active assignment at `as_of_date` | 当前状态页不读取 portfolio 固定 benchmark pointer |
| `benchmark_resolution_mode` | `Performance` / `Review` | resolved primary benchmark over selected period | 区间内发生 benchmark assignment 切换时显示 `Mixed Benchmark` |
| `selected_taxonomy_id` | `Holdings` / `Performance` / `Risk` / `Review` | page-specific default | 非 planning taxonomy 不能进入 target compare |
| `target_resolution_mode` | `Risk` | dimension-aware resolved target source | `weight` / `risk_budget` 各自按 active `TAA` fallback `SAA` 解析；若来源不同显示 `Mixed Target Dimensions` |
| `resolved_target_timeline_id` | `Review` | resolved target timeline over selected period | 指向正式 `ResolvedTargetTimeline` 对象，而不是页面临时拼装结果 |
| `target_resolution_mode` | `Review` | resolved target timeline over selected period | 区间内发生 target 切换或维度来源不同均显示 `Mixed Targets` |
| `base_currency` | all analytic pages | portfolio base currency | 允许展示本地补充字段，但 canonical 输出以 base 为准 |
| `coverage_state` | all analytic pages | computed | 明确标识 `complete / partial / unavailable`，`coverage_ratio` 单独展示 |

### 4.4 Page Families

#### Current-State Pages

- `Overview`
- `Holdings`
- `Risk`

共同特点：

- 以单一 `as_of_date` 为核心
- 以 `PortfolioSnapshot` 为共同状态输入，其中 `Risk` 进一步消费 `RiskSnapshot`
- 不默认承载区间叙事

#### Period Pages

- `Performance`
- `Review`

共同特点：

- 以 `period_start / period_end` 为核心
- 消费 `PerformanceSnapshot`、`PeriodRiskSummary`、`ReviewPack`
- 服务归因、总结与行动

#### Ledger Pages

- `Transactions`
- `Accounts`

共同特点：

- 面向事实账本
- 消费 `Transaction`、`Account` 以及由 transaction 展开的 `LedgerPosting`
- 不承担衍生分析的主要责任

## 5. Canonical Page Definitions

### 5.1 Overview

**Primary question**

- 这个组合现在整体怎样？

**Primary objects**

- `PortfolioSnapshot`
- alert summary
- selected benchmark summary

**Core blocks**

- portfolio summary hero
- current composition summary
- top holdings / top groups
- benchmark-relative summary
- risk highlights
- alert highlights

**Comparator**

- default: `primary benchmark`
- target-related内容只做摘要或跳转，不在这里承担完整 drift 分析

**Drill-down**

- composition block -> `Holdings` grouped view
- benchmark-relative summary -> `Performance` or `Risk`
- risk highlights -> `Risk`
- alert highlights -> `Risk / Limits & Alerts`

**Not this page**

- 不做完整风险监控台
- 不做区间绩效页
- 不做 holdings 主表

### 5.2 Holdings

**Primary question**

- 当前持有什么？

**Primary objects**

- positions
- lots
- grouped exposures

**Core blocks**

- canonical holdings table
- group by taxonomy / selected planning taxonomy / account
- sortable and filterable columns
- relative-to-benchmark columns
- portfolio security detail page entry

**Comparator**

- default: `primary benchmark`
- target gap 不是该页的 canonical 比较对象
- 若 benchmark 只有 `return_only` 能力，则 benchmark-relative composition columns 不可用，并显示 `benchmark composition unavailable`

**Drill-down**

- security row -> portfolio security detail page
- taxonomy group -> grouped holdings view
- relative columns -> `Performance` or `Risk`
- account grouping -> `Accounts`

**Not this page**

- 不替代 `Transactions`
- 不承担完整 drift / target-risk-share 分析

### 5.3 Performance

**Primary question**

- 这段时间赚了多少，怎么赚的？

**Primary objects**

- `PerformanceSnapshot`
- `AttributionReport`
- benchmark-relative analytics

**Core blocks**

- return scorecard
- benchmark comparison
- drawdown summary
- contribution / attribution tables
- benchmark-relative contribution

**Comparator**

- default: `primary benchmark`

**Conditional blocks**

- Brinson / benchmark allocation-selection 归因只在 benchmark composition 可用时启用
- 若 benchmark 只有 `return_only` 能力，则只展示 excess return、tracking error、information ratio 等 return-relative analytics
- 若 selected period 内 benchmark assignment 发生切换，则页面必须显示 `Mixed Benchmark` 并按 resolved benchmark timeline 解释结果

**Drill-down**

- attribution group -> filtered holdings or grouped contribution view
- drawdown point -> period focus view
- benchmark-relative block -> benchmark coverage detail

**Not this page**

- 不做当前状态 holdings 主表
- 不承载 alert configuration

### 5.4 Risk

**Primary question**

- 当前风险在哪里？
- 过去这段时间风险是怎么走出来的？
- 现在哪些风险需要处理？

**Primary objects**

- `RiskSnapshot`
- `AlertEvent`
- `AlertRule`
- resolved `TargetSet`

**Core blocks**

- `Current`
- exposure
- target weight drift / `target_weight_gap`
- optional intended / unintended drift
- target risk budget gap / `target_risk_budget_gap`
- concentration
- limits & alerts
- `Realized`
- realized risk path
- drawdown / worst-day / volatility summary
- recent monitoring tape
- scenario results

**Comparator**

- target weight drift / target risk budget gap: resolved `TargetSet`
- limits / alerts: configured `AlertRule`
- benchmark-relative risk blocks: only when benchmark composition is available

**Conditional blocks**

- 若 resolved `TargetSet` 启用了 `weight` 维度，则展示 target weight drift
- 若 resolved `TargetSet` 启用了 `risk_budget` 维度，则展示 target risk budget gap
- 若某一维度未配置，则对应 block 不展示，并标记 `target dimension not configured`

**Page-local contexts**

- selected planning taxonomy
- target resolution mode（按 `weight` / `risk_budget` 分维度解析）
- comparison denominator label（`portfolio_total_risk` 或 sleeve drilldown 时的 `parent_local_risk`）
- `as_of_date`
- realized-risk window

**Rule management**

- `AlertRule` 的查看与维护放在 `Risk / Limits & Alerts`
- 首版不再单独做 `Alert Rules` 一级导航

**Drill-down**

- drift line -> filtered `Holdings`
- target risk budget gap line -> grouped holdings + scenario context
- alert event -> related holdings / accounts / transactions
- scenario result -> affected positions

**Not this page**

- 不承担 Review 的正式叙事输出
- 不承担 raw transaction ledger

### 5.5 Transactions

**Primary question**

- 账本里发生了什么？

**Primary objects**

- `Transaction`

**Core blocks**

- transaction ledger table
- filters by account / instrument / type / period
- direct edit / delete support
- cash flow visibility

**Drill-down**

- transaction instrument -> portfolio security detail page
- transaction account -> `Accounts`

**Not this page**

- 不做 FIFO 派生 `Trades` 的独立主页面
- 不做完整持仓分析

### 5.6 Accounts

**Primary question**

- 底层账户结构和归属是什么？

**Primary objects**

- `Account`
- `LedgerPosting`
- linked transactions
- account balances / positions

**Core blocks**

- account list
- account balance summary
- account positions
- account-level ledger slice（derived from `LedgerPosting`）
- as-of replay boundary: account balances / positions / linked transactions 都按 selected `as_of_date` 截断；未到 `effective_date` 的证券现金腿只进入 pending settlement，不提前冲 settled cash
- default settlement cash mapping
- paired securities-to-cash view

**Drill-down**

- account position -> `Holdings`
- account transaction -> `Transactions`

**Not this page**

- 不替代 portfolio-level holdings
- 不承载 planning target configuration

### 5.7 Review

**Primary question**

- 这个周期最重要的结果和动作是什么？
- 要把哪些收益、风险和研究结论带到下一次讨论？

**Primary objects**

- `ReviewPack`
- `PerformanceSnapshot`
- `PeriodRiskSummary`
- action items

**Core blocks**

- scorecard
- performance summary
- risk summary
- benchmark-relative section（composition-aware；`return_only` 时降级为 performance-only）
- target drift summary
- target risk budget summary
- research handoff
- commentary
- actions
- export artifacts

**Comparator**

- fixed order: `absolute result -> primary benchmark -> resolved target timeline -> alert breaches`

**Comparator rules**

- `Review` 不复用 `Risk` 的单点 target comparator；
- `Review` 必须按 `period_start / period_end` 解析 resolved target timeline；
- 若所有已配置维度在整个区间都解析到同一 source，则可退化显示为单一 `SAA` 或单一 `TAA`；
- 若区间内发生 target 切换，或 `weight` / `risk_budget` 维度解析到不同 source，则显示 `Mixed Targets`，并按生效段分别汇总 target drift summary 与 target risk budget summary；
- 若某段 target 只定义 `weight` 或只定义 `risk_budget`，则 review 只展示该维度对应的 summary，并对缺失维度标记 `not configured`；
- 若区间内发生 benchmark assignment 切换，则显示 `Mixed Benchmark`；
- benchmark-relative section 只有在 benchmark composition 可用时才展示 `benchmark-active bets summary`；
- 若 benchmark 只有 `return_only` 能力，则降级为 `benchmark-relative performance summary`，并标记 `benchmark composition unavailable`。
- `ReviewPack` 保存的是 review 的当前上下文与派生结果；facts 或 configs 变化后允许重算刷新。
- 需要保留历史版本时，归档对象应是导出的 artifact，而不是冻结页面内的当前 review 对象。

**Drill-down**

- performance summary -> `Performance` with preserved period / benchmark
- risk summary -> review-local period risk detail surface
- current-state follow-up item -> `Risk` with latest complete `as_of_date`
- action item -> source alert / review section / research handoff

**Not this page**

- 不做当前状态快照
- 不做研究配置

### 5.8 Research

**Primary question**

- 当前 sleeve target solve 结果是什么，哪些需要 handoff？

**Primary objects**

- `ResearchRun`

**Core blocks**

- run setup
- current context
- run list
- status / rerun / update
- target weights
- member targets / leaf targets
- solve event diagnostics
- target weight gaps
- handoff to portfolio context
- current context 与 target solve actual rows 必须共享同一 `as_of_date` 边界；dated positions 不得配 undated cash

**Not this page**

- 不做通用 quant studio
- 不承载与组合无关的方法库配置

### 5.9 Portfolio Configure

**Primary question**

- 这个组合使用哪些 benchmark assignments 和基础上下文？

**Primary objects**

- `Portfolio`
- `PortfolioBenchmarkAssignment`

**Core blocks**

- base portfolio context
- primary benchmark selector
- benchmark assignment history
- optional secondary benchmark list

**Rules**

- shared benchmark definitions 来自 `Settings / benchmark library`
- portfolio 只在这里绑定 `primary / secondary benchmark assignments`
- review / performance / snapshot 页面只消费 resolved benchmark，不在分析页里改 assignment

**Not this page**

- 不编辑 taxonomy tree
- 不维护 planning target sets

### 5.10 Portfolio Configure / Taxonomies

**Primary question**

- 这个组合的分类体系和 planning targets 是什么？

**Primary objects**

- `Taxonomy`
- `TaxonomyAssignment`
- `TargetSet`
- `TargetSetLine`

**Core blocks**

- taxonomy list
- tree-table editor
- per-scope target editor
- `Unassigned` diagnostics
- default planning taxonomy selector
- taxonomy root default-target selector
- planning target-set management

**Rules**

- `planning_enabled taxonomy` 必须是 instrument-scoped，且可选补 `cash_bucket`
- `account taxonomy` 可用于分析和报表，但不承载 `TargetSet`
- `TargetSet` 对已启用维度必须覆盖当前 scope 的 direct members；未启用维度保持空值
- `Risk / Review` 只比较当前层 siblings，不把层级 risk budget 静默铺平成全局 leaf target

**Not this page**

- 不做高频分析工作面
- 不做独立 benchmark analytics

## 6. Shared Detail Surfaces

### 6.1 Portfolio Security Detail

Portfolio-specific security detail 的 canonical 入口从 `Holdings` 行进入，但形态是独立子路由 `/portfolios/:portfolioId/holdings/:assetId`，而不是页面底部普通 section、fixed panel 或 modal。点击 holdings 行后进入组合内单资产详情页，保留 `as_of_date` 等上下文，返回 `Holdings` 时保留列表筛选/排序状态。

包含：

- `Quotes`
- `Transactions`
- `Trades`
- `Events`
- `Data Quality`

Watchlist / Instrument Detail 是 asset-level research terminal 的 canonical 入口。Portfolio Security Detail 可以使用共享资产价格/NAV 事实，并提供资产级收益风险、研究和 monitoring 的 deep link，但不复制 Watchlist detail 的完整工作面。

其他页面如需进入单资产深层信息，应优先：

- 跳转到 portfolio security detail page
- 或在当前上下文提供明确的 `Open Security Detail` link

而不是在各工作面重复实现另一套单资产详情。

### 6.2 Node / Group Detail

taxonomy node、risk_sleeve node、benchmark-relative group 等分组对象，不单独做一级页面。

首版建议采用：

- 当前页内 detail drawer
- 带筛选的 linked view
- 带上下文跳转的 `Holdings` / `Risk` / `Performance`

## 7. Cross-Page Drill-Down Rules

| From | To | Rule |
| --- | --- | --- |
| `Overview` composition | `Holdings` grouped view | 保留 `as_of_date` 与 selected taxonomy |
| `Overview` risk highlight | `Risk` | 保留 `as_of_date` |
| `Holdings` row | portfolio security detail page | 保留 `as_of_date` 与列表返回状态 |
| `Holdings` group | `Risk` or `Performance` | 保留 taxonomy context |
| `Performance` contribution line | `Holdings` / grouped detail | 保留 period 与 grouping |
| `Risk` drift line | `Holdings` filtered view | 保留 planning taxonomy 与 target_resolution_mode |
| `Risk` alert event | `Holdings` / `Transactions` / `Accounts` | 跳到相关实体 |
| `Transactions` instrument | portfolio security detail page | 保留 instrument context |
| `Accounts` position | `Holdings` | 保留 account filter |
| `Review` performance summary | `Performance` | 保留 period, taxonomy, benchmark |
| `Review` risk summary | review-local period risk detail | 不跳到 `Risk` 当前态页面 |
| `Research` handoff | `Review` / `Risk` / `Overview` | 保留 referenced run id |

## 8. Canonical Object-to-Page Ownership

| Object | Primary Page | Secondary Consumers |
| --- | --- | --- |
| `PortfolioSnapshot` | `Overview` | `Holdings`, `Risk` |
| positions / lots | `Holdings` | `Overview`, `Risk` |
| `Transaction` | `Transactions` | `Accounts`, portfolio security detail |
| `LedgerPosting` | `Accounts` | derived from `Transaction` |
| `Account` | `Accounts` | `Transactions`, `Holdings` |
| `PerformanceSnapshot` | `Performance` | `Review`, `Overview` |
| `AttributionReport` | `Performance` | `Review` |
| `RiskSnapshot` | `Risk` | `Overview` |
| `PeriodRiskSummary` | `Review` | exports |
| `AlertRule` | `Risk / Limits & Alerts` | monitor engine |
| `AlertEvent` | `Risk / Limits & Alerts` | `Review` |
| `ReviewPack` | `Review` | exports |
| `ResolvedTargetTimeline` | `Review` | `PeriodRiskSummary` |
| `ExportArtifact` | `Review` | archived export list / artifact viewer |
| `ResearchRun` | `Research` | `Review`, `Overview`, `Risk` via handoff |
| `PortfolioBenchmarkAssignment` | `Portfolio Configure` | `Overview`, `Performance`, `Risk`, `Review` |
| `Taxonomy` | `Portfolio Configure / Taxonomies` | `Holdings`, `Performance`, `Risk`, `Review` |
| `TargetSet` | `Portfolio Configure / Taxonomies` | `Risk`, `Review` |

## 9. Conditional Content Rules

### 9.1 Benchmark Composition Availability

若 benchmark 只有 `return_only` 能力：

- `Performance` 仍可展示 benchmark-relative return
- `Risk` 不展示 `benchmark_active_weight`
- `Overview` 不展示 benchmark composition-style active-bet blocks
- `Holdings` 不展示 benchmark-relative composition columns
- `Review` 不展示 `benchmark-active bets summary`，只保留 `benchmark-relative performance summary`
- Brinson 或 benchmark allocation/selection 归因不启用

### 9.2 Missing Benchmark Assignment

若当前组合不存在可解析的 primary benchmark assignment：

- `Overview`、`Holdings`、`Performance`、`Risk`、`Review` 的 benchmark-relative 区块都应进入 `comparator missing` 或 `absolute only`
- `Portfolio Configure` 必须明确提示 benchmark assignment 缺失

### 9.3 Missing Planning Context

若当前 taxonomy 不是 `planning_enabled`：

- `Risk / Drift`
- `Risk / Target Risk Budget`
- `Review` 的 target blocks

都应明确进入 `comparator missing` 或 `absolute only` 状态。

### 9.4 Missing Active TAA

若无 active `TAA`：

- 页面默认回退到 active `SAA`
- UI 必须明确标识当前 target mode = `SAA`

### 9.5 Coverage Problems

若 taxonomy assignment 不完整：

- `Portfolio Configure / Taxonomies` 必须显式显示 `Unassigned`
- `Holdings` / `Risk` / `Performance` 的相关分组结果必须带 coverage warning

### 9.6 No Review / No Research

若当前 period 尚无 `ReviewPack` 或 `ResearchRun`：

- 页面应展示明确 blank state
- 不应伪造空 summary 充当正式对象

## 10. Explicit Non-Goals

- 不新增一级 `X-Ray` 页面
- 不新增一级 `Trades` 页面
- 不新增一级 `Events` 页面
- 不把 `Taxonomies` 放到全局主导航
- 不把 `Research` 拉回全局壳层
- 不为单资产、单节点再造大量独立路由

## 11. Output of This Document

这份信息架构确定后，后续实现应按以下顺序推进：

1. 页面路由与导航骨架
2. 各工作面的 page-level state model
3. object-to-page API 切分
4. shared detail route / drawer 体系
5. 关键 blank state / unavailable state / comparator missing state

下一阶段不应再回头增加新的一级工作面或重新定义页面主问题。
