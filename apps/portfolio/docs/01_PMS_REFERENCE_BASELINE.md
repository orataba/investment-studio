# PMS 正式版参考基线

更新时间：`2026-04-14`

> 说明：这份文档记录参考产品与目标态工作面设计，不代表当前已发布页面集合。当前实际落地范围以 [README.md](../README.md) 为准。

## 1. 目标

这个项目要做的是一个正式版的个人/小团队组合管理 PMS（Portfolio Management System）。

当前明确的四条参考主线是：

1. **Morningstar**：主要参考产品设计、信息架构、页面组织、报告感和整体 UI 语言。
2. **Portfolio Performance**：主要参考组合管理领域模型、账户/持仓/交易抽象、收益率与绩效计算、分类与报表能力。
3. **Bridgewater**：主要参考风险预算投资逻辑、宏观环境分解方法、组合“灵魂”和投资操作系统。
4. **GIPS**：主要参考绩效计算治理、TWR 优先原则、外部现金流处理、估值频率和方法一致性。

这四条线的职责要严格分开：

- **Morningstar 决定我们怎么呈现。**
- **Portfolio Performance 决定我们怎么记账和计算。**
- **Bridgewater 决定我们为什么这样配置组合。**
- **GIPS 决定绩效口径如何保持可解释、一致和可审计。**

## 2. 参考框架

| 参考源 | 在本项目中的角色 | 重点借鉴内容 | 明确不照搬的部分 |
|---|---|---|---|
| Morningstar | 设计/UI 参考 | 导航结构、工作台布局、Snapshot / X-Ray 风格当前状态报告、基准对比、专业报告表达 | 不做像素级复刻，不照搬其面向顾问/订阅产品的商业包装 |
| Portfolio Performance | 领域模型与计算参考 | 账户、组合、交易、现金流、绩效指标、Taxonomy/分类、导入导出思路 | 不照搬其 Java/Eclipse 桌面架构 |
| Bridgewater | 投资逻辑参考 | 风险预算、环境平衡、增长/通胀驱动、风险而非资金作为配置对象 | 不声称使用 Bridgewater 官方方法论或内部实现 |
| GIPS | 绩效治理参考 | TWR 优先、外部现金流中性化、估值频率、几何链接、方法一致性、披露边界 | 不声称 GIPS compliance；不在首版实现 firm/composite/report/verification 合规体系 |

## 3. Morningstar 参考要点

### 3.1 参考定位

Morningstar 对本项目最有价值的不是“单一功能”，而是它把组合分析组织成一套**可理解、可沟通、可导出的专业工作界面**：

- 先有组合，再有分析视角。
- 分析默认围绕**benchmark / 对比基线**展开。
- 同一套组合可以在资产、风格、区域、行业、费用、风险等多个维度切换观察。
- 表格和图不是装饰，而是用来回答“我现在到底暴露在什么上”。

需要注意 Morningstar 当前公开产品线已经分成两层：

- **零售/个人投资者层**：Investor / Portfolio / X-Ray 这类轻量组合分析与持仓管理界面。
- **顾问/专业层**：Advisor Workstation 正在向 **Direct Advisory Suite** 迁移。Morningstar 在 **2025-01-13** 正式发布了 Direct Advisory Suite，并在 2025-2026 年间继续替换旧顾问工作流。

### 3.2 本项目应吸收的产品语言

- **Workbench 式布局**：左侧导航 + 顶部上下文 + 中央分析区。
- **Portfolio 先于 analysis**：先进入组合列表，再进入单组合工作面，而不是先看零散报表。
- **基准优先**：不是只显示“我有什么”，而是显示“我相对目标/基准偏到了哪里”。
- **分解可下钻**：资产类别、区域、风格、行业、费用、统计指标都应该能 drill-down 到底层持仓。
- **表格优先于花哨图表**：高信息密度，服务专业判断。
- **报告感**：页面本身就应该接近可导出的投顾/投委会材料，而不是消费型 App。

### 3.3 必要参考资料

- Morningstar 发布 Direct Advisory Suite（`2025-01-13`）：<https://newsroom.morningstar.com/news/news-details/2025/Morningstar-Launches-Direct-Advisory-Suite-a-Powerful-Modernized-Software-Solution-for-Financial-Advisors/default.aspx>
- Direct Advisory Suite demo 页：<https://www.morningstar.com/try/direct-advisory-suite>
- Advisor Workstation Training Guide（官方 PDF）：<https://advisor.morningstar.com/Enterprise/VTC/AWSOverview.pdf>
- Morningstar Portfolio Analytics Service（官方 PDF）：<https://www.morningstar.com/content/cs-assets/v3/assets/blt4eb669caa7dc65b2/bltdf8da2041b6c3f05/626ffe115f7b601eaaebb172/Portfolio_Analytics_Service_A4_SustainalyticsLogo.pdf>
- Morningstar Direct Portfolio Analysis for New Users（官方 PDF）：<https://morningstardirect.morningstar.com/clientcomm/PAForNewUsers.pdf>
- Morningstar Portfolio Manager 持仓录入帮助：<https://www.morningstar.com/help-center/premium-and-portfolio-manager/add-holdings-to-portfolio-manager>
- Morningstar Portfolio 下载帮助：<https://www.morningstar.com/help-center/portfolio/download-portfolio>

### 3.4 设计提炼结论

正式版 UI 应该是：

- 专业、克制、密度高；
- 默认面向“懂组合的人”；
- 所有页面都能回答一个明确问题；
- 页面之间围绕“组合入口 -> 单组合工作面 -> 分析与复盘”组织，而不是围绕零散工具组织。

### 3.5 Morningstar 结构映射结论

结合当前截图和 Morningstar 顾问端报告体系，可以把它的产品结构提炼成三层：

1. **Portfolio 入口层**
   先看到 portfolio list / all portfolios。
2. **Portfolio workspace 层**
   进入单一组合后，再切 `Overview / Holdings / Performance / Risk / Transactions / Accounts / Review / Research`。
3. **Report / analysis 层**
   在 workspace 内再消费底层 holdings / performance / risk 结果，生成当前状态报告与周期复盘材料。

对本项目最重要的结论是：

- Morningstar 的 `X-Ray / Portfolio Snapshot` 更像**当前组合状态报告**，适合客户或投委会沟通，而不只是一个简单结构页。
- 本项目不再单独保留一个一级 `X-Ray` 工作面，而是把其核心能力沉淀为 `Overview` 页面和可导出的当前状态报告。
- `Stock Intersection` 更像顾问端辅助报告，对本项目不是核心能力，可明确排除。
- Morningstar 缺少一个面向 buy-side 小团队的独立 **Risk** 工作面，这恰好是本项目需要补上的核心差异。

## 4. Portfolio Performance 参考要点

### 4.1 参考定位

Portfolio Performance（PP）是正式版最重要的**计算与领域模型参考**。它对我们最有价值的地方是：

- 组合和账户分离；
- `Deposit Accounts / Securities Accounts` 分工清晰；
- 交易和现金流定义清晰；
- 绩效计算长期打磨；
- 分类、报表、Taxonomy 非常成熟；
- 本地优先、以投资人实际账本为中心。

### 4.2 本地参考仓库

参考仓库：

- 上游仓库：<https://github.com/portfolio-performance/portfolio>
- 本地快照 commit：`85041a52fcad47d39b8c7540531af044cd28836b`

说明：

- 如需本地参考代码库，可自行 clone 到任意未纳入版本控制的目录；不要在项目文档中记录个人机器路径。
- 后续需要更新时，可在本机参考目录内执行 `git pull`。

### 4.3 优先阅读的代码模块

#### 领域模型

- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/Client.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/Account.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/Portfolio.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/Transaction.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/AccountTransaction.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/PortfolioTransaction.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/BuySellEntry.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/Classification.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/Taxonomy.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/model/TaxonomyTemplate.java`

#### 绩效与快照

- `name.abuchen.portfolio/src/name/abuchen/portfolio/snapshot/ClientSnapshot.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/snapshot/PortfolioSnapshot.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/snapshot/PerformanceIndex.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/snapshot/ClientPerformanceSnapshot.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/snapshot/ClientIRRYield.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/snapshot/GroupByTaxonomy.java`
- `name.abuchen.portfolio/src/name/abuchen/portfolio/snapshot/SecurityPosition.java`

#### UI 组织

- `name.abuchen.portfolio.ui/src/name/abuchen/portfolio/ui/views/dashboard`
- `name.abuchen.portfolio.ui/src/name/abuchen/portfolio/ui/views/holdings`
- `name.abuchen.portfolio.ui/src/name/abuchen/portfolio/ui/views/securities`
- `name.abuchen.portfolio.ui/src/name/abuchen/portfolio/ui/views/payments`
- `name.abuchen.portfolio.ui/src/name/abuchen/portfolio/ui/views/taxonomy`
- `name.abuchen.portfolio.ui/src/name/abuchen/portfolio/ui/views/securitychart`
- `name.abuchen.portfolio.ui/src/name/abuchen/portfolio/ui/views/settings`

### 4.4 必要参考资料

- 项目主页：<https://www.portfolio-performance.info>
- GitHub 仓库：<https://github.com/portfolio-performance/portfolio>
- README：<https://github.com/portfolio-performance/portfolio/blob/master/README.md>
- 用户手册首页：<https://help.portfolio-performance.info/en/>
- Performance 概览：<https://help.portfolio-performance.info/en/concepts/performance/>
- Time-Weighted Rate of Return：<https://help.portfolio-performance.info/en/concepts/performance/time-weighted/>
- Money-Weighted Rate of Return：<https://help.portfolio-performance.info/en/concepts/performance/money-weighted/>
- All Transactions：<https://help.portfolio-performance.info/en/reference/view/accounts/all-transactions/>
- Performance Dashboard：<https://help.portfolio-performance.info/en/reference/view/reports/performance/>
- Taxonomies：<https://help.portfolio-performance.info/en/reference/view/taxonomies/>
- Statement of Assets：<https://help.portfolio-performance.info/en/reference/view/reports/statement/>

### 4.5 计算层提炼结论

正式版 PMS 至少应当从 PP 吸收这些原则：

- **组合账本是真相源**，不是 UI 页面。
- **账户、组合、证券、现金流、公司行为要分层建模**。
- **收益率口径必须明确区分**：TTWROR、IRR/MWROR、绝对收益、基准相对收益。
- **分类不是装饰字段**，而是一等公民对象，后续要承载资产类别、planning taxonomy、主题、策略、账户维度等多重视图。
- **快照是运行时派生分析层**，报表和分析应基于快照和账本计算，而不是手写汇总字段。

进一步说，PP 风格的 `Taxonomies` 对本项目的启发应落成两条约束：

- taxonomy 默认是 **portfolio-scoped** 的多分类体系，而不是系统级唯一真相源；
- `risk sleeve` 不是另一套独立树系统；若需要保留，它更适合作为 `taxonomy_type = risk_sleeve` 的语义标签，而 canonical planning behavior 由 `planning_enabled` 与 portfolio-level `default_planning_taxonomy_id` 决定。

### 4.6 PP 交互结构提炼结论

对本项目最有价值的 PP 交互模式有三条：

1. **Accounts 模型**
   使用 `Deposit Account` 与 `Securities Account` 分离现金与证券账本。
2. **Statement of Assets 作为 canonical holdings 页**
   持仓页首先是一张高密度、可分组、可排序、可切换列定义的资产表。
3. **Security detail pane**
   选中单个资产后，不跳去单独系统，而是在当前工作面进入 detail view / info pane，查看：
   - quotes
   - transactions
   - trades
   - events
   - data quality

这意味着本项目的 `Holdings` 页面不应只是一个静态表，而应当是：

- 顶部为组合级 holdings table；
- 底部或右侧为 selected security detail pane；
- detail pane 承载单资产历史、交易、trade matching 和事件解释。

## 5. GIPS 参考要点

### 5.1 参考定位

GIPS 对本项目的价值不在 UI 或账本模型，而在 **绩效计算治理**：

- 默认用 TWR 呈现管理绩效；
- 把 external cash flows 从业绩中中性化；
- 对估值频率、现金流发生日估值、几何链接和方法一致性提出明确要求；
- 要求对方法、覆盖率和不可用边界保持清晰披露。

本项目只采用这些原则作为计算设计约束，不声称 GIPS compliance。

### 5.2 必要参考资料

- GIPS Standards for Firms：<https://www.gipsstandards.org/standards/gips-standards-for-firms/>
- GIPS Standards Handbook for Firms：<https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/>
- 本项目 GIPS 对照说明：[`06_GIPS_ALIGNMENT.md`](./06_GIPS_ALIGNMENT.md)

### 5.3 计算层提炼结论

正式版 PMS 应从 GIPS 吸收这些原则：

- **TWR 是默认绩效语言**：IRR / MWROR 只能作为补充资金效率指标。
- **外部现金流政策必须稳定**：`deposit / withdrawal` 与真实组合边界分配才是组合级 external flows。
- **估值日期必须支撑现金流处理**：当前 daily snapshot engine 等价于对所有外部现金流日期估值；若未来做非日频估值，必须先定义 large cash flow policy 与子期间链接。
- **区间收益必须几何链接**：不能把日收益简单相加后当作 TWR。
- **方法一致性优先于表面精度**：summary、daily series、drawdown、risk 必须消费同一条 TWR 序列。
- **合规声明必须克制**：缺少 firm/composite/report/verification 体系时，只能写 GIPS-informed methodology，不能写 GIPS compliant。

## 6. Bridgewater 参考要点

### 6.1 参考定位

Bridgewater 对这个项目最重要的贡献不是“某个指标”，而是组合管理的底层问题定义：

- 投资要先理解**环境驱动**，再谈资产配置。
- 配置对象首先是**风险暴露**，而不是资金权重。
- 组合应该围绕**环境平衡**构建，而不是围绕某一种市场叙事孤注一掷。
- 研究判断、风险预算、资本放缩、执行实现，是不同层级的问题。

### 6.2 本项目应吸收的核心原则

#### 原则 A：风险比资金更重要

正式版 PMS 的长期配置对象应该优先定义为：

- planning taxonomy
- `TargetSet(type = saa)`（Strategic Asset Allocation，长期战略目标）上的 target weight / target risk share
- `TargetSet(type = taa)`（Tactical Asset Allocation，时变战术目标）上的 target weight / target risk share

而不是单纯的名义资金占比。

#### 原则 B：宏观环境要显式化

组合分析不能只停留在：

- 股票/债券/黄金/商品/现金

还要显式回答：

- 这个组合在增长上行时会怎样？
- 在增长下行时会怎样？
- 在通胀上行时会怎样？
- 在通胀下行时会怎样？

#### 原则 C：研究与组合运行要分层协同

项目后续建议把系统拆成至少四层：

1. **Research**：负责状态判断、环境分解、相对风险预算偏移。
2. **PMS Kernel**：负责真实组合、账户、交易和估值输入事实。
3. **Analytics**：负责从事实层生成 snapshots、performance、monitor 和 review 输入。
4. **Allocator / Target Layer**：负责把 taxonomy 上的长期 `SAA TargetSet`、时变 `TAA TargetSet` 和当前偏离拼起来。

前端层面的含义是：

- `Research` 可以进入单组合 workspace；
- 但研究设定、方法和后台配置不应和组合账本 UI 混在一起；
- 组合前端只负责运行、更新、展示结果和 handoff。

#### 原则 D：组合操作系统要能解释“为什么”

正式版不应只回答：

- 当前持仓是什么；
- 当前收益是多少；

还要回答：

- 当前组合在赌什么宏观状态；
- 风险主要集中在哪些 selected planning taxonomy 节点；
- 与当前 `TargetSet` 目标的偏离来自哪里；
- 哪些偏离是主动判断，哪些偏离是市场漂移。

### 6.3 必要参考资料

- Bridgewater《The All Weather Story》：<https://www.bridgewater.com/research-and-insights/the-all-weather-story>
- Bridgewater《The Biggest Mistake in Investing》：<https://www.bridgewater.com/research-and-insights/the-biggest-mistake-in-investing>
- Bridgewater《A New Era of Higher Inflation Risks》：<https://www.bridgewater.com/research-and-insights/a-new-era-of-higher-inflation-risks>

### 6.4 投资逻辑提炼结论

正式版 PMS 的投资内核应当默认支持：

- 基于环境驱动的资产/策略分解；
- 风险预算而非单纯 capital weight；
- benchmark、`TargetSet` 目标、actual 结果三层对照；
- `SAA TargetSet -> TAA TargetSet -> implementation -> realized outcome` 的完整链路。

## 7. 当前正式版的基础判断

基于以上四条参考线，正式版的产品方向可以先锁为：

### 7.1 产品气质

- Morningstar 风格的专业投研/组合工作台；
- Portfolio Performance 风格的账本与绩效底座；
- Bridgewater 风格的风险预算和环境平衡投资逻辑；
- GIPS-informed 的绩效计算治理和方法一致性。

### 7.2 首版核心能力

首版应优先覆盖：

1. 组合与账户建模
2. 交易账本与现金流
3. 由 Analytics 生成的持仓与估值快照
4. TTWROR / IRR / benchmark-relative performance
5. taxonomy / planning taxonomy / bucket / benchmark 视图
6. snapshot / performance / risk / review 四类分析工作面
7. taxonomy 上的 `TargetSet` targets、drift / risk budget gap / attribution

### 7.3 产品壳层结论

正式版前端应采用两层结构：

#### 全局壳层

- `Portfolios`
- `Settings`

其中 `Settings` 只承载系统主数据、数据源、benchmark 库和可选 taxonomy templates，不直接承载某个组合的 active taxonomies。

#### 单组合工作面

- `Snapshot`
- `Holdings`
- `Performance`
- `Risk`
- `Transactions`
- `Accounts`
- `Review`
- `Research`

#### 组合配置入口

- `Portfolio Configure`
- `Taxonomies`

其中：

- `Holdings` 参考 PP `Statement of Assets`
- `Snapshot` 参考 Morningstar `X-Ray / Portfolio Snapshot`
- `Accounts` 参考 PP `Accounts`
- `Risk` 是本项目新增的一等工作面
- `Review` 是本项目相对 Morningstar/PP 的 buy-side 周期复盘层
- `Research` 位于组合内，但仅承载 run / update / display / handoff，不暴露复杂配置器
- `Taxonomies` 位于组合配置入口下，用于构建该组合的多分类体系；每套 taxonomy 应声明 `primary_assignment_scope`，首版只允许 `primary scope + optional cash_bucket`，而 planning-enabled taxonomy 负责维护 `TargetSet`（`saa` / `taa`）及其 `TargetSetLine` 目标

### 7.4 数据架构结论

正式版的数据结构应按“事实输入 -> 配置输入 -> 派生分析”理解：

#### 狭义事实输入

- `Transactions`
- `Quotes`
- `FX`
- `Benchmark series`
- `Corporate Actions / Events`

#### 必要配置输入

- `Portfolio / Account / Instrument metadata`
- `Benchmark definition`
- `Portfolio-scoped taxonomies / target-set config / alert-rule config`

#### 派生分析层

- `Positions`
- `Lots`
- `Trades`
- `Snapshots`
- `Performance`
- `Risk`
- `Review`
- `Snapshot report`

结论是：

- `Quotes` 是跨组合共享的市场数据，不属于某个 portfolio 私有；
- `Transactions` 是组合私有账本；
- `Trades` 不是原始事实，而是 FIFO lot matching 后的分析结果；
- `Snapshots` 由 Analytics 从事实层生成，不属于 Kernel 原始主数据；
- `Review` 和 `Snapshot report` 都消费派生分析层，而不是直接手填页面字段。

### 7.5 明确的非目标

当前不应做的事情：

- 不做 Morningstar 的像素级仿制品；
- 不直接搬 Portfolio Performance 的 Java 桌面代码；
- 不把 Bridgewater 的公开材料机械翻译成“固定模板”；
- 不在产品一开始就堆太多“投顾 CRM / 客户管理 / 营销页面”能力。

## 8. 下一步文档建议

这个参考基线文档之后，建议紧接着补四份正式版文档：

1. `02_PRODUCT_PRD.md`
   明确用户、核心场景、必须做/不做。
2. `03_DOMAIN_MODEL.md`
   明确 Portfolio / Account / Transaction / Position / Benchmark / Taxonomy / Risk budget 等对象。
3. `04_CALCULATION_SPEC.md`
   明确估值、成本、收益率、现金流、基准、归因和风险预算口径。
4. `05_INFORMATION_ARCHITECTURE.md`
   明确正式版前端页面结构、工作流和导航。

---

## 附录：本地参考检查清单

- `portfolio-performance` 本地仓库已存在于 `references/portfolio-performance`
- 当前仅作为参考代码库，不纳入本仓库版本控制
- 文档中的外部链接均为截至 `2026-04-14` 可访问的公开资料
