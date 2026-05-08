# PMS 正式版产品需求文档（PRD）

更新时间：`2026-04-14`
关联文档：[`01_PMS_REFERENCE_BASELINE.md`](./01_PMS_REFERENCE_BASELINE.md)

> 说明：这份文档描述的是 `Portfolio` 的目标态产品定义，不等同于当前已发布页面集合。当前实现以 [README.md](../README.md) 为准，其中当前状态总览已经以 `Overview` 形式发布，`Snapshot` 不再作为独立工作面保留。

## 1. 产品定义

### 1.1 一句话定义

这是一个面向**小型全球多资产投资团队**的专业 PMS / 投资工作台，用于完成：

- 组合跟踪
- 交易与现金流记录
- 绩效计算与归因
- 风险监控与压力测试
- 风险预算驱动的组合管理

### 1.2 目标用户

本产品的核心用户不是散户，也不是超大型机构后台，而是一个典型的 `2-10 人` 投资团队，例如：

- `PM / CIO`：负责组合配置、风险预算、策略偏离和最终决策。
- `研究员 / 多资产分析师`：负责研究观点、环境判断、基准对比、绩效与风险归因。
- `风控 / 运营兼任角色`：负责交易录入、净值核对、风险阈值监控、月度复盘材料整理。

### 1.3 目标场景

系统优先服务以下真实工作流：

1. 每日开盘前/收盘后查看组合状态。
2. 跟踪组合相对 benchmark，以及 `SAA Weight` / `SAA Risk` / `TAA Weight` / `TAA Risk` 四个显式 target comparator 的偏离。
3. 计算区间绩效、收益来源和风险来源。
4. 在市场冲击或组合偏离时快速定位风险问题。
5. 做月度/季度 review，并形成可内部讨论的专业材料。
6. 把投资研究判断转化为可执行的风险预算调整，而不是停留在口头讨论。

## 2. 为什么做

### 2.1 当前替代方案的问题

用户原本可选择：

- `Aladdin / SimCorp` 一类大型机构平台；
- Morningstar 一类分析工具；
- Portfolio Performance 一类本地组合跟踪工具；
- Excel / 手工报表 / 券商导出。

但对小型团队来说，常见问题是：

- 大型平台太贵、太重、实施周期太长。
- 通用分析工具往往不承载自己的投资逻辑。
- 本地跟踪工具计算不错，但风控和投资操作系统不够完整。
- Excel 灵活，但难以沉淀一致口径、稳定复盘体系和可重算结果。

### 2.2 产品机会

这个项目的机会不是复制大型 OMS/IBOR 平台，而是做一个更聚焦的系统：

- **比 Excel 更稳定**
- **比 Portfolio Performance 更机构化**
- **比 Morningstar 更贴合自己的投研流程**
- **比 Aladdin / SimCorp 更轻、更便宜、更可控**

## 3. 产品原则

### 3.1 原则一：事实输入与派生结果要分层

系统中的所有分析都应来自明确的事实输入和配置输入。

事实输入至少包括：

- 交易
- prices / quotes
- FX
- benchmark series
- corporate actions / events

必要配置输入包括：

- portfolio / account / instrument metadata
- portfolio taxonomies / planning taxonomy config
- benchmark / target-set / alert-rule config

持仓、lots、trades、快照、绩效、风险、review 都属于派生层，而不是页面手工汇总字段。

### 3.2 原则二：风控是核心模块，不是附属页面

本产品不是“组合看板 + 几个风险图表”，而是默认把风控放在核心工作流中。

### 3.3 原则三：风险预算高于资金权重

系统不仅记录资金配置，还要记录：

- planning taxonomy
- `SAA`（Strategic Asset Allocation，长期战略目标）
- `TAA`（Tactical Asset Allocation，时变战术目标）
- active tilt
- realized risk share

### 3.4 原则四：面向专业团队，而不是面向大众理财

产品默认：

- 信息密度高；
- 术语专业；
- 基准对比强；
- drill-down 明确；
- 允许复杂但清晰的分析流程。

### 3.5 原则五：先做组合管理，再做交易执行和后台会计

第一阶段重点是 PM / 研究 / 风控工作台，不是完整的券商直连、订单路由或基金会计平台。

### 3.6 原则六：优先做稳定工作面，而不是零散功能堆叠

产品首先要定义清楚：

- 用户从哪里进入；
- 进入组合后看到哪些一级工作面；
- 每个工作面解决什么问题；
- 数据在这些工作面之间如何复用。

这比先罗列二十个功能点更重要。

其中必须显式区分：

- `Snapshot`：回答当前组合状态，面向客户或投委会也可读；
- `Review`：回答一个区间内发生了什么，面向内部复盘；
- `Research`：服务组合，但配置和方法仍在后台管理。
- `Taxonomy`：属于 portfolio-scoped 配置；`risk sleeve` 最多只是其中一种语义标签，而不是另一套独立树系统或特殊引擎对象。

## 4. 产品范围总览

正式版产品分为五个一级模块：

1. `PMS Kernel`
2. `Analytics, Performance & Attribution`
3. `Risk System`
4. `Investment OS`
5. `Workbench UI`

### 4.1 PMS Kernel

负责组合管理底座：

- 组合
- 账户
- 证券主数据
- 交易
- 现金流
- FX
- benchmark definitions
- portfolio benchmark assignments
- 公司行为

说明：

- `PMS Kernel` 提供账本真相源和估值输入边界；
- `Snapshot` 本身不是 Kernel 主数据，而是 Analytics 层产物。

### 4.2 Analytics, Performance & Attribution

负责回答“赚了多少、怎么赚的、相对基准如何”：

- snapshot generation / valuation cache
- NAV / cumulative return
- TWR
- IRR / MWROR
- benchmark-relative return
- drawdown
- return attribution
- optional intended-vs-actual diagnostics

### 4.3 Risk System

负责回答“风险在哪里、是否超标、受到什么冲击”：

- 持仓暴露
- 相对偏离
- 集中度
- 波动与回撤
- 场景压力测试
- 风险预算跟踪
- 告警系统

### 4.4 Investment OS

负责回答“这个组合为什么这样配、它在表达什么宏观判断”：

- portfolio taxonomies，尤其是 planning-enabled taxonomies
- taxonomy 上的 `TargetSet(type = saa | taa)`
- resolved target / intended target / actual 三层对照
- 宏观环境映射
- active tilts
- review memo / action log

### 4.5 Workbench UI

负责把以上能力组织成一套专业工作台：

- portfolio entry
- portfolio workspace
- snapshot / holdings / performance / risk / transactions / accounts / review / research

## 5. MVP 产品边界

### 5.1 MVP 资产范围

MVP 先支持**全球公开市场的标准化资产**：

- 现金
- 股票
- ETF
- 公募基金 / 共同基金
- 债券
- 指数与 benchmark time series
- 多币种现金与 FX 折算

说明：

- 这已经足够覆盖大多数小型全球多资产团队的核心组合观察与复盘需求。
- 衍生品会明显放大建模和风险复杂度，因此不放入首版硬要求。

### 5.1.1 债券首版边界

首版对债券的支持，明确采用 **valuation-first** 的 best-practice 边界：

- 支持 `plain-vanilla cash bonds` 的持仓、估值、票息、到期兑付、绩效和基础暴露分析；
- 组合级 NAV / performance 以 **dirty market value** 为 canonical 估值基础；
- 若数据源只提供 clean price，则必须同时提供 accrued interest，前端和报表都以合成后的 dirty value 为准；
- duration、convexity、yield、spread 等固收分析字段在首版可作为外部输入展示，但不作为首版自研计算引擎硬要求。

这意味着首版明确**不把债券做成完整固收分析平台**。以下内容不纳入首版硬边界：

- 内建 full accrual engine
- OAS / spread decomposition
- callable / structured / model-based bond analytics
- 复杂摊还或嵌入期权债券的定价逻辑

### 5.2 首版暂不做

- 期权
- 掉期
- OTC 衍生品
- 私募股权 / 非标资产
- 订单路由与券商执行接口
- 会计总账 / 基金会计 / 法定报表
- 合规规则引擎全套
- 客户 CRM / 营销门户

### 5.3 可作为第二阶段考虑

- 期货及保证金资产
- pre-trade what-if
- liquidity / capacity 分析
- factor risk model
- 更复杂的 benchmark relative attribution
- 自动化数据接入与 broker import

### 5.4 MVP 优先级

首版优先级明确如下：

- `P0`：PMS Kernel、Risk System、Analytics, Performance & Attribution
- `P1`：Investment OS 的完整表达层、Review 导出深化、自动化数据接入
- `P2`：更复杂的风险模型、衍生品支持、执行系统和外围集成

这意味着首版不是先做“能看持仓”的系统，再补风控，而是从第一版开始就要求：

- 有真实组合底座；
- 有稳定绩效口径；
- 有可日常使用的风控体系。

## 6. 核心用户故事

### 6.1 PM / CIO

作为 PM，我希望：

- 每天打开系统就能知道组合总览、净值、主要暴露、关键风险和偏离情况。
- 快速看到相对基准和相对当前目标的偏离。
- 知道当前组合到底在赌什么宏观环境。
- 在 review 时看到本期收益与风险来源，而不是只看结果数字。

### 6.2 研究员 / 分析师

作为研究员，我希望：

- 能从 holdings 一路 drill-down 到 taxonomy、region、sector、selected planning taxonomy 和 benchmark 对比。
- 能在一个页面里看到收益、风险、回撤、归因和场景暴露。
- 能把研究判断沉淀成对 planning taxonomy 节点的偏移建议，而不是只写文字评论。

### 6.3 风控 / 运营

作为风控或运营，我希望：

- 交易录入后系统自动重算持仓和关键风险指标。
- 当组合偏离、集中度超限、回撤超阈值时能有明确告警。
- 月度复盘时能稳定导出统一口径的 review 材料。

## 7. 产品壳层与工作面架构

### 7.1 全局壳层

正式版先采用简单稳定的全局壳层：

- `Portfolios`
- `Settings`

说明：

- `Portfolios` 是主入口；
- `Settings` 管系统主数据、数据源、benchmark 库和可选 taxonomy templates，不直接承载某个组合的 active taxonomies 或 portfolio benchmark assignments。

### 7.2 Portfolio Entry

用户进入 `Portfolios` 后，先看到：

- portfolio list
- 总览 NAV / 日变动
- 组合数量与状态
- 快速进入某个 portfolio

这一步借鉴 Morningstar 的“先选组合、再进入组合工作面”的结构，而不是直接落到某个报表页。

### 7.3 Portfolio Workspace

进入单组合后，工作面建议固定为：

1. `Snapshot`
2. `Holdings`
3. `Performance`
4. `Risk`
5. `Transactions`
6. `Accounts`
7. `Review`
8. `Research`

除主工作面外，单组合还应提供一个配置入口：

- `Portfolio Configure > Taxonomies`

用于维护该组合自己的分类体系。

### 7.4 各工作面定位

#### Snapshot

回答：

- 这个组合现在整体怎样？
- 它当前暴露在什么结构上？
- 相对 benchmark 有什么关键特征？
- 相对当前 target 有哪些需要被提示的摘要性偏差？
- 当前有哪些需要被解释的风险摘要？

定位：

- 参考 Morningstar `X-Ray / Portfolio Snapshot`
- 是当前组合状态报告页
- 既服务内部快速判断，也适合作为客户/投委会沟通的基础导出页

它不是：

- 周期绩效复盘页；
- 纯风险监控页；
- 纯 holdings 表格页。

#### Holdings

定位：

- 参考 PP `Statement of Assets`
- 是正式版的 canonical 持仓页面

要求：

- 高密度表格；
- 可排序、可筛选、可分组、可切换列；
- 默认展示 asset、quote、quote date、Spark Chart、quantity、avg cost、cost basis、market value、weight、unrealized P&L；
- 允许按 taxonomy / selected planning taxonomy / account 聚合。
- 允许展示 quote-derived asset market trend 指标，用于扫当前持仓资产最近市场表现；这些指标不读取组合数量、成本法、现金流或 realized / income events。
- 不在 Holdings 默认承载资产级 TWR、realized gain、dividend / coupon income 或 closed positions；这些属于 Performance / security detail 的区间绩效视图。

#### Performance

回答：

- 赚了多少？
- 相对 benchmark 如何？
- 收益来源和回撤来源是什么？

首屏结构：

- `Return & Risk Metrics`：基于用户选择的自定义区间展示 TWR、annualized TWR、IRR / MWR、volatility、downside volatility、Sharpe / Sortino、drawdown 等收益风险指标；这些 fair-value return / risk 指标可选择独立 benchmark 做对比；
- `Calculation`：把 period boundary、group contribution 和 waterfall calculation 合并成一张可审计表。顶部为 initial value，group rows 可按 asset / asset type / currency / account / taxonomy 展示 start / end value、weights、realized gain、unrealized gain、income、fees、taxes、FX P&L、period P&L、group TWR、contribution；外部现金流按 deposits / withdrawals 单独列出，底部为 portfolio total 与 final value，并支持导出当前表格 CSV。
  Calculation 的桥接公式是 `Initial Value + Deposits - Withdrawals + Period P&L = Final Value`。capital gain 作为底层派生值用于 reconciliation；表格直接展示 realized gain 与 unrealized gain，避免把合计项误读为第三类收益来源。拆分使用期间绩效成本：期初已有持仓按期初市值重置，区间买入按成交 gross amount 入期间成本，期末仍持有部分形成 unrealized gain；账户 FIFO / moving average 不改变 Performance 的期间资本利得拆分。

`NAV Trend` 与 `Monthly Return Matrix` 属于 Overview，不在 Performance 重复展示。Overview 图表 compare 与 Performance benchmark 独立选择，避免把不同分析场景绑死到同一个 benchmark。

#### Risk

回答：

- 当前风险结构是什么？
- 当前偏离、集中和风险预算是否过大？
- 过去这段时间风险是怎么走出来的？
- 哪些资产或 sleeve 正在贡献主要风险？

这是本项目相对 Morningstar Web Portfolio 的新增核心工作面。

首版页面结构分为两部分：

- `Risk / Current`：当前结构、当前 drift、当前 risk budget gap、相关性与风险贡献
- `Risk / Realized`：rolling volatility、rolling Sharpe 与 benchmark-relative risk path

#### Transactions

定位：

- 参考 PP `All Transactions`
- 作为组合级总账流水页面

内容包括：

- buys / sells
- cash flows
- fees / taxes
- transfers
- 可按 account / instrument / type / period 过滤

#### Accounts

定位：

- 参考 PP `Deposit Accounts / Securities Accounts`
- 与 Holdings 有意形成不同视角，不是简单的“关联账户”设置页

内容包括：

- cash account / securities account 列表
- account balance
- account positions
- account-level transactions
- derived account ledger postings
- account workspace 必须支持按 `as_of_date` 回放；证券现金腿在 `settlement_date` 前仍留作 pending settlement，不得提前挪入 settled cash
- default settlement cash mapping
- paired-account view（一个 cash account 可服务多个 securities accounts）

#### Review

定位：

- buy-side 周期复盘页面
- 承载 commentary、actions、performance/risk summary，以及可归档的导出物
- 形态上更接近基金月报 / 季报式的 period review pack

它与 `Snapshot` 的区别是：

- `Snapshot` 关注 as-of-now；
- `Review` 关注一个时间区间内发生了什么以及下一步动作；
- `Review` 在系统内默认是当前工作版本，可随事实和配置修正而重算更新。

它与 `Risk` 的区别是：

- `Risk` 负责分析当前风险与过去风险路径；
- `Review` 负责把一个周期内的收益、风险、偏离、研究 handoff 和动作总结成可讨论的 pack。

#### Research

定位：

- portfolio 内部的轻量研究运行与结果工作面
- 服务当前组合，但不负责研究方法和设定管理

内容包括：

- research run 列表
- run status / update / rerun
- artifact display
- research output handoff 到组合分析语境

明确不做：

- 前端研究配置器
- configuration / method / request schema 编辑器
- 与账本对象混写的研究设定页面

#### Portfolio Configure

定位：

- 组合级配置页面，不是高频分析 tab
- 承载 portfolio-scoped benchmark assignments 与基础组合上下文

内容包括：

- 选择来自共享 benchmark library 的 primary benchmark
- 维护 portfolio benchmark assignments 的生效区间
- 查看 secondary benchmarks
- 维护组合级默认分析上下文

#### Portfolio Configure / Taxonomies

定位：

- 组合内的配置页面，不是高频分析 tab
- 参考 PP 的 `Taxonomies` 用法，用于构建该组合的多种分类方式

内容包括：

- 新建 taxonomy
- 从 template 复制 taxonomy
- 编辑 taxonomy tree / nodes
- 指定 `primary_assignment_scope`
- 管理 instrument / account / cash-bucket assignments 与 coverage checks
- 识别并处理 `Unassigned` coverage
- 指定 `default planning taxonomy`
- 为 planning-enabled taxonomy 指定 canonical budgeting level
- 为 planning-enabled taxonomy 维护 `TargetSet(type = saa)` 历史版本
- 为 planning-enabled taxonomy 维护 `TargetSet(type = taa)` 列表并标识 active target set

关键原则：

- taxonomy 默认归属于 `Portfolio`
- 一个 portfolio 可以有多套 taxonomies
- `risk sleeve` 可保留为 `taxonomy_type = risk_sleeve` 的语义标签，但不再决定 canonical planning behavior
- 同一时间只能有一套 `default planning taxonomy`
- taxonomy 必须声明 `primary_assignment_scope`
- taxonomy 在首版只允许一个正式聚合 scope；若需补充 scope，则只允许 `cash_bucket`
- 首版不允许同一 taxonomy 同时把 `instrument` 与 `account` 作为正式聚合基础
- 首版 `planning-enabled taxonomy` 必须以 `instrument` 为 `primary_assignment_scope`，可选补充 `cash_bucket`
- `account` taxonomy 仅用于 analysis / reporting / monitoring，不承载 `TargetSet`
- taxonomy 本身只定义结构；时间变化的目标通过 `TargetSet` 记录
- planning-enabled taxonomy 只允许挂完整的 `TargetSet`
- `TargetSet(type = saa)` 与 `TargetSet(type = taa)` 在首版至少包含 `target_weight`、`target_risk_share` 中的一种，也可以同时包含两者
- 首版 `TargetSet.target_weight` 的 canonical basis 固定为 `portfolio_nav`
- `TargetSet` 是 `Risk / Review` 的正式 comparator，必须工作在单一比较分母上；它不是递归 sleeve construction recipe
- 层级 sleeve 内部的 `risk_budget` 默认是 parent-local 语义，不能通过父层 `target_risk_share` 乘法铺平成全局 portfolio `target_risk_share`
- 层级 sleeve 内部的 `weight` 只有在每层都显式以 capital share 定义且不存在内部 optimizer / leverage / overlay / netting 改写时才允许乘法展开；否则必须先解算成 portfolio-level resolved weight
- 每套 planning-enabled taxonomy 可维护多条 `TargetSet(type = taa)`，但同一时点最多一条 active TAA target set
- assignment 至少支持 `instrument`、`account`、`cash_bucket` 三种 target scope

### 7.5 Security Detail Pane

在 `Holdings` 工作面中，选中单个资产后，应进入统一的 security detail pane，而不是跳转到一个完全不同的系统。

这个 detail pane 应优先包含：

- `Quotes`
- `Transactions`
- `Trades`
- `Events`
- `Data Quality`

说明：

- `Quotes / Transactions / Trades / Events` 的组合是 PP 非常成熟的交互模式；
- 这比为每个资产单独做很多碎页面更稳健；
- 也更适合专业用户在 holdings 分析时保持上下文不丢失。
- `Trades` 和 `Events` 留在单资产层，不升级为 portfolio 一级导航。

### 7.6 默认比较链路

正式版不采用“一个页面统一和所有对象比较”的混合逻辑，而是按问题类型绑定 canonical comparator。

默认规则固定如下：

- `Snapshot`：默认比较 `primary benchmark`
- `Holdings`：相对列默认比较 `primary benchmark`
- `Performance`：默认比较 `primary benchmark`
- `Risk / Drift`：展示当前 planning taxonomy 下 `SAA Weight` 与 `TAA Weight` 两个显式 comparator；某一来源未配置该维度时，该 comparator 标记为 unavailable
- `Risk / Target Risk Budget`：展示当前 planning taxonomy 下 `SAA Risk` 与 `TAA Risk` 两个显式 comparator；某一来源未配置该维度时，该 comparator 标记为 unavailable
- `Review`：按固定顺序输出 `absolute result -> primary benchmark -> resolved target timeline -> alert breaches`

关键原则：

- `benchmark`、`TargetSet`、`AlertRule` 语义不同，系统不得静默互相替代；
- 若某页面缺少其 canonical comparator，则退回 absolute view，并明确标记 comparator missing；
- `Risk` 的 drift / target gap 结果都必须显式展示当前 `taxonomy` 与四个 comparator 的独立可用性，不得把 `TAA` 缺失维度静默回退到 `SAA`；
- `Review` 必须显式展示 `target_resolution_mode` 与 `target timeline summary`；若 review period 内发生 target 切换，则标记 `Mixed Targets`，并按生效段分别汇总 `target drift summary` 与 `target risk budget summary`；
- `Review` 不复用 `Risk` 的单点 target comparator，而是按 review period 解析 resolved target timeline；
- 只有用户显式切换时，页面才允许改用其他比较对象。

## 8. 核心工作流

### 8.1 每日组合跟踪

流程：

1. 导入或录入最新交易与价格。
2. 更新持仓、lots、trades、估值和 NAV。
3. 查看 `Snapshot`、`Holdings` 和 `Risk`。
4. 检查 drift、风险暴露、集中度和告警。
5. 必要时记录 action items。

### 8.2 周期绩效复盘

流程：

1. 选择周期（WTD / MTD / QTD / YTD / since inception）。
2. 生成区间绩效和 benchmark 对比。
3. 查看收益归因和风险归因。
4. 复核 drawdown、情景风险、benchmark-active bets 与 target drifts。
5. 输出 review memo。

### 8.3 风险预算管理

流程：

1. 选择 planning taxonomy，并声明其 `primary_assignment_scope` 与 `budgeting_level`。
2. 维护 `TargetSet(type = saa)`，其完整定义该 taxonomy 在 budgeting level 上的长期目标。
3. 必要时新增或更新 `TargetSet(type = taa)`，其对已启用维度保存为完整目标集，而不是稀疏增量存储。
4. 若某个 sleeve 采用内部 risk parity / optimizer，其子层预算属于 research / construction recipe，而不是直接录入 portfolio-level `TargetSetLine`。
5. 计算 actual portfolio 的 realized exposure 和 risk share。
6. 首版允许每套 `TargetSet` 只定义资金权重目标、只定义风险预算目标，或同时定义两者。
7. `Risk` 页面必须把 active `SAA` 与 active `TAA` 的 `weight` / `risk_budget` 维度拆成四个 comparator 独立展示；缺失的 `TAA` 维度只影响对应 `TAA` comparator，不回退到 `SAA`。
8. 若配置了 `target_weight`，则跟踪 `weight drift`；若配置了 `target_risk_share`，则跟踪 `risk budget gap`。
9. 风险预算比较只允许拿同一分母下的 realized risk share 与 `target_risk_share` 对比；若 drill into 某个 sleeve，则必须切到 sleeve-local denominator 并显式标记。
10. 在 `resolved target / optional intended target / actual` 之间识别偏离；首版 canonical 输出优先为 `intentional gap` 与 `unintended gap`。

## 9. 风控系统 PRD

这是首版产品的一级模块，不是附录。

### 9.1 风控系统目标

风控系统必须回答以下问题：

1. **我持有什么风险？**
2. **我相对目标偏了多少？**
3. **我的风险集中在哪里？**
4. **如果发生冲击，我会怎么亏？**
5. **当前风险是否仍然符合我的投资意图？**

### 9.2 风控系统一级能力

#### A. 暴露风险（Exposure Risk）

展示并聚合：

- asset class exposure
- region exposure
- country exposure
- sector / industry exposure
- currency exposure
- issuer exposure
- strategy / selected planning taxonomy exposure

要求：

- 所有暴露都能 drill down 到持仓层。
- 支持 absolute exposure 与 relative-to-benchmark exposure。

#### B. 偏离风险（Relative / Drift Risk）

比较对象包括：

- actual vs benchmark
- actual vs SAA
- actual vs selected TAA

关键输出：

- target weight gap / drift
- target risk budget gap（当 risk budget target 已配置）
- benchmark active weight（仅当 benchmark composition 可用）
- benchmark-active bets by selected planning taxonomy / bucket（仅当 benchmark composition 可用）
- unintended drift
- optional intentional gap

#### C. 集中度风险（Concentration Risk）

至少支持：

- top holdings concentration
- top issuers
- HHI
- top region / sector concentration
- bucket concentration

#### D. 实现风险（Realized Risk）

至少支持：

- realized volatility
- rolling volatility
- drawdown
- downside deviation
- tracking error

#### E. 场景风险（Scenario Risk）

首版至少支持一组可配置压力情景：

- 全球股市下跌
- 利率上行
- 通胀上行
- 美元走强/走弱
- 信用利差走阔

输出至少包括：

- total P&L impact
- bucket / planning taxonomy contribution
- top affected positions

#### F. 风险预算跟踪（Risk Budget Tracking）

这是本产品与普通组合工具的关键差异点。

系统必须支持：

- 定义 planning-enabled taxonomy，并可选保留 `risk_sleeve` 语义标签
- 为 planning-enabled taxonomy 维护完整 `TargetSet(type = saa)`
- 为 planning-enabled taxonomy 维护完整 `TargetSet(type = taa)` 列表
- 观察 actual risk share
- 观察 `actual vs SAA` 与 `actual vs TAA` 的 gap

风控系统不仅看“仓位偏没偏”，还要看：

- 组合的风险是否偏离了设计意图；
- 风险贡献是否集中到了少数位置；
- 当前组合是否失去环境平衡。

#### G. 风险告警（Risk Alerts）

`Risk` analytics 页面首版不承载告警管理；告警属于后续独立风控工作流，不与当前风险归因表格混在一起。

首版至少支持阈值型告警：

- 单一持仓超限
- 单一 bucket / planning taxonomy node 超限
- benchmark active weight 超限（仅当 benchmark composition 可用）
- target weight gap 超限
- drawdown 超阈值
- concentration 超阈值
- tracking error 超阈值
- scenario loss 超阈值

### 8.3 风控系统的产品定位

风控系统不是只为“风险部门”设计，而是给 PM 和研究团队日常使用。

因此它必须同时满足：

- **足够专业**：指标和分解口径要严肃。
- **足够可解释**：出现问题时，要快速定位到驱动项。
- **足够接近决策**：能和 benchmark、taxonomy targets、alerts 连起来。

### 8.4 风控系统首版不做

首版不强求：

- 全套因子模型
- VaR / CVaR 的复杂参数化模型
- 衍生品 Greeks 全栈
- 实时交易前风险控制
- 合规黑名单和法规规则引擎

## 10. 绩效与归因系统 PRD

### 9.1 核心问题

系统需要回答：

- 这个组合赚了多少？
- 相对 benchmark 多赚/少赚了多少？
- 收益来自哪些资产、区域、行业、taxonomy / planning taxonomy？
- 表现差是配置问题、选标的问题，还是执行问题？

### 9.2 首版必须支持

- NAV history
- daily return series
- TWR
- IRR / MWROR
- benchmark comparison
- excess return
- drawdown table
- holdings / bucket / taxonomy / planning taxonomy return contribution
- fee and cash-flow aware performance view

### 9.3 归因层级

首版归因至少支持：

- asset / taxonomy contribution
- region contribution
- sector contribution
- planning taxonomy contribution
- benchmark-relative active contribution

## 11. 投资操作系统 PRD

### 11.1 定位

这是本产品区别于普通投资记账工具的另一核心层。

系统不是只记录事实，还要记录投资团队的**设计意图**：

- benchmark 是什么
- 这个组合采用哪一套 `default planning taxonomy`
- taxonomy 上的 `TargetSet(type = saa)` 是什么
- 当前生效的 `TargetSet(type = taa)` 是什么
- 当前 target 定义了 `weight`、`risk_budget` 还是两者同时定义
- 当前 active tilt 是什么
- 为什么这样做

### 11.2 首版必须支持

- 从 benchmark library 选择并维护 portfolio benchmark assignments
- 定义 portfolio taxonomies
- 指定 `default planning taxonomy`
- 在 planning-enabled taxonomy 上维护完整 `TargetSet(type = saa)`
- 为 planning-enabled taxonomy 维护完整 `TargetSet(type = taa)` 列表与 active target set
- 首版允许每套 `TargetSet` 按需只配置 `weight` 或 `risk_budget`
- 记录 active tilts / notes / actions
- 在 review 中展示 `resolved target / optional intended target / actual -> realized outcome`

### 11.3 首版价值

这样做的结果是：

- PM 的判断不是口头化的；
- 风险偏离有明确语义；
- review 时可以复盘“思路是否正确”，而不仅是“结果好不好”。

## 12. 信息架构

首版前端建议按“全局壳层 + portfolio workspace”组织：

### 12.1 全局主导航

1. `Portfolios`
2. `Settings`

### 12.2 单组合二级导航

1. `Snapshot`
2. `Holdings`
3. `Performance`
4. `Risk`
5. `Transactions`
6. `Accounts`
7. `Review`
8. `Research`

另有组合配置入口：

- `Portfolio Configure`
- `Portfolio Configure > Taxonomies`

### 12.3 Snapshot

回答：

- 组合现在怎么样？
- 当前结构和暴露是什么？
- 相对 benchmark 的关键偏差是什么？
- 相对当前 target 的摘要性偏差是什么？
- 当前有哪些重要风险摘要和告警需要解释？

### 12.4 Holdings

回答：

- 当前持有什么？
- 这些头寸的成本、权重和未实现损益是什么？
- 单个资产的 quotes / transactions / trades / events 是什么？
- 当前持仓对应的 quote date 是否足够新？

它也应该支持：

- `group by asset type / currency`
- `group by taxonomy`
- `group by selected planning taxonomy`

### 12.5 Performance

回答：

- 本期收益如何？
- 相对 benchmark 如何？
- 收益与回撤由谁驱动？

页面默认由两块组成：区间 `Return & Risk Metrics`、合并 calculation / contribution / realized risk attribution 的 `Calculation` 审计表。Calculation 使用和 Holdings 一致的表格 view selector；系统默认视图命名为 `Default`，展示区间平均权重、区间收益、收益贡献和 realized risk attribution。区间由 start / end date 直接驱动；benchmark compare 与 Overview 的图表 compare 分开选择。

### 12.6 Risk

回答：

- 当前有哪些风险超标？
- 当前漂移、集中度和 risk budget gap 是否过大？
- 过去这段时间滚动波动率和滚动 Sharpe 是怎么走出来的？
- 当前 as-of date 下，不同资产和 taxonomy sleeve 的相关性与风险贡献是什么？
- 当前更应该对比 `SAA` 还是某条 `TAA`？
- 当前命中的 alert breaches 是什么？

### 12.7 Transactions

回答：

- 最近做了什么交易？
- 哪些现金流影响了组合？
- 交易导致了哪些 book 变化？

### 12.8 Accounts

回答：

- 组合底层账户结构是什么？
- 现金在哪些账户？
- 哪些持仓属于哪个 securities account？

### 12.9 Review

回答：

- 本周期最重要的结果是什么？
- 风险、收益、偏离和动作如何总结？
- 哪些 research 结论应该 handoff 到本期复盘？

### 12.10 Research

回答：

- 这个组合当前在所选 planning taxonomy / sleeve scope 下应如何求解 target weights？
- current context / construction rows 必须与所选 `as_of_date` 对齐，cash 口径不能混入晚于该日的交易或尚未到 `effective_date` 的 settled cash
- 哪些结果需要更新、重跑或 handoff 到组合讨论？
- 当前 research run 的 target weights、member targets、风险预算求解诊断和 target weight gaps 是什么？

### 12.11 Portfolio Configure

回答：

- 当前组合绑定了哪条 primary benchmark？
- 这个绑定来自哪条共享 benchmark definition？
- primary / secondary benchmark assignments 的生效区间是什么？

### 12.12 Portfolio Configure / Taxonomies

回答：

- 这个组合有哪些 taxonomies？
- 哪些 taxonomies 覆盖不完整或 assignment 有问题？
- 哪一套 taxonomy 被指定为 `default planning taxonomy`？
- 每套 taxonomy 的 `primary_assignment_scope` 是什么？
- 哪些 taxonomy 是 planning-enabled，并维护了哪些按层 `TargetSet`？

## 13. 非功能需求

### 13.1 数据与口径

- 所有关键计算应口径一致、可重复。
- 关键指标应能追溯到交易、价格和快照。
- benchmark、taxonomy、FX 口径必须显式。

### 13.2 使用方式

- 本地优先；
- 单机优先；
- 适合小团队内部使用；
- 支持导出 review 材料。

### 13.3 可重算性与归档

- 历史交易允许直接修正，系统必须自动重算受影响区间的持仓、净值、绩效、风险和 review。
- review 内容默认展示当前版本，可随 facts、benchmark、target 和 taxonomy 变化而更新。
- 导出的 review artifacts 需要可归档、可回看，但不反向冻结系统内对象。

## 14. MVP 成功标准

如果正式版 MVP 做完，用户应当能够：

1. 创建全球多资产组合并维护交易与现金流。
2. 生成稳定的持仓、估值和净值序列。
3. 查看 benchmark-relative performance。
4. 查看 taxonomy / planning taxonomy / bucket 的贡献和暴露。
5. 运行一套基本可用的风控系统。
6. 用统一模板生成月度/季度 review。
7. 用 taxonomy + `TargetSet` + capital target / risk budget 的语言描述组合，而不是只用资金权重描述组合。

## 15. 当前明确不追求

- 不追求替代 Aladdin / SimCorp 的全机构能力。
- 不追求做成零售理财 App。
- 不追求先做花哨可视化再补底层口径。
- 不追求首版覆盖所有资产和衍生品。
- 不追求复刻 Morningstar 的 `Stock Intersection`。

## 16. 下一步文档

在这份 PRD 之后，建议立即继续三份文档：

1. `03_DOMAIN_MODEL.md`
   锁定 Portfolio / Account / Transaction / Position / BenchmarkDefinition / PortfolioBenchmarkAssignment / Taxonomy / TargetSet / ReviewPack。
2. `04_CALCULATION_SPEC.md`
   锁定估值、收益率、归因、风险、FX、benchmark、drift、target gap 口径。
3. `05_INFORMATION_ARCHITECTURE.md`
   锁定页面结构、主导航、分析页布局和 drill-down 关系。
