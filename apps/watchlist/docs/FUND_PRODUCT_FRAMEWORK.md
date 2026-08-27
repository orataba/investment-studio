# 公募 / 私募产品框架

适用范围：当前 Watchlist app 里 `public_fund` 与 `private_fund` 的分类、投资研究判断和监控评估框架。公募/私募身份来自共享 Registry 的 `instrument_type`；本文件中的 taxonomy 完全属于 Watchlist 私域。

## 1. 目标

watchlist 的当前产品模型不是“在一堆平铺标签里找产品”，而是：

1. 先按分类定义产品池
2. 再用定量数据横向筛选
3. 最后用有证据的定性研究判断和监控结论做决策

因此当前基金框架明确拆成三层：

- `Instrument Taxonomy`
- `Investment Research`
- `Monitoring Assessment`

这三层不会再混成一套“通用 fund tags”，也不会把 Watchlist 分类写回底层 Registry。

## 2. 三层模型

### 2.1 Instrument Taxonomy

回答的问题是：`这到底是什么产品`

当前基金分类已经从固定 `fund_category_l1/l2/l3` 升级成按 instrument type 隔离的真实树结构：

- taxonomy 定义表：`instrument_taxonomy_node`
- 当前赋值表：`instrument_taxonomy_assignment`
- Overview 页基础字段：`fund_vehicle`
- watchlist / monitoring / research applicability 使用派生字段：
  `instrument_taxonomy_level_1..7`、`instrument_taxonomy_leaf`、`instrument_taxonomy_path`

其中：

- `fund_vehicle`
  只描述公募/私募的法律或交易载体，比如场外开放式、LOF、契约型私募基金；ETF 是独立 `instrument_type`，不会作为该字段的选项
- instrument type
  `public_fund / private_fund` 在 Registry 层已经分开，决定详情页、数据源和可用分类树
- taxonomy path
  是 Watchlist 内部的策略分类树，支持不同分支不同深度；`instrument_taxonomy_level_1..7` 从各类型树的一级分类开始编号
- taxonomy assignment
  默认允许为空，不做自动推断；正式分类必须由研究人员在详情页 `Overview -> Fund Taxonomy` 手动确认

当前两棵基金分类树覆盖核心研究范围：

- 公募：
  `权益基金 / 固定收益基金 / 配置基金 / 另类策略基金 / 商品型 / 实物资产基金 / 货币市场 / 基金中基金 / 其他`
- 私募：
  `股票策略 / 信用策略 / 宏观策略 / 管理期货 / 事件驱动 / 相对价值 / 多策略 / 组合基金 / 其他`

代表性叶子包括：

- 公募：`主动权益 / 指数权益 / 投资级信用 / 高收益债 / 偏股配置 / 市场中性 / 贵金属基金 / 权益 FOF`
- 私募：`主观多头 / 指数增强 / 量化选股 / 股票市场中性 / 困境债 / 系统化宏观 / 趋势跟踪 / 并购套利 / 波动率与期权 / FOF / MOM`

示例（instrument type → Watchlist taxonomy path）：

- `private_fund → 股票策略 / 量化多头 / 指数增强`
- `private_fund → 管理期货 / 趋势跟踪`
- `private_fund → 相对价值 / 可转债套利`
- `public_fund → 固定收益基金 / 可转债`
- `public_fund → 基金中基金 / 配置 FOF`

分类是入口层，不是标签层；Overview 页先定池子，Research 页再看定性标签。

### 2.2 Investment Research Assessments

回答的问题是：`这类产品在研究上有什么特征`

它承载 FOF 研究里需要标准化的定性结论，但不是产品身份字段。
当前受控词表和判定口径以 [FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md](./FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md) 为准。这些字段可用于详情、列和筛选；除研究优先级、生命周期、论点状态和负责人等工作流字段外，定性结论不进入 `Group By`。

基金专属研究维度包括：

- `research_coverage`
  `focus_bucket`、`research_evidence_level`
- `research_edge`
  `investment_edge_quality`、`alpha_source`
- `research_process`
  `implementation_style`、`process_repeatability`、`decision_discipline`、`trading_universe`
- `research_style`
  `style_profile`、`portfolio_construction`、`style_drift_risk`
- `research_manager`
  `manager_assessment`、`team_stability_assessment`
- `research_risk`
  `risk_management_quality`
- `research_terms`
  `capacity_bucket`、`liquidity_terms_fit`、`fee_value_assessment`
- `research_governance`
  `alignment_quality`
- `research_delivery`
  `historical_delivery`
- `research_role`
  `portfolio_role`

`coverage_status` 代表 watchlist / portfolio 生命周期状态，例如 `Watch / Proposed / Invested / Paused / Exited`，可以作为 watchlist 列和筛选字段使用，但不再被解释为 `Research Status`。Research 页使用 `Current Investment View` 描述研究判断，避免和生命周期状态混淆。

这里允许按分类做适用范围控制：

- 某些标签只适用于 `公募`
- 某些标签只适用于 `私募`
- 某些标签只适用于特定 `instrument_taxonomy_level_*`

因此它不是“所有资产共用同一张标签表”，而是同一套定义机制下按 instrument type 和 taxonomy 裁剪的研究问题；ETF、股票和指数使用各自独立的评估维度。

### 2.3 Monitoring Assessment

回答的问题是：`这只产品当前最该持续盯什么`

这层放那些会随时间变化、且需要周期性复核的观察结论。

当前主要分组包括：

- `monitoring_risk`
  `equity_correlation_bucket`
- `monitoring_regime`
  `preferred_regime`、`weak_regime`
- `monitoring_operations`
  `transparency_quality`

Monitoring 页面不会再静态要求所有基金补同一套字段；它只检查：

- `required_for_monitoring = true`
- 当前资产类型适用
- 当前分类路径适用

## 3. 当前工作流

围绕 watchlist 的实际工作流应当是：

1. `Fund Taxonomy`
   先定义 universe
2. `Screening`
   看收益、回撤、波动、容量、规模、tracking error 等定量指标
3. `Investment Research`
   看风格、团队、过程、容量、兑现度
4. `Monitoring`
   持续跟 freshness、缺失 label、open recalc 和关键观察结论

换句话说：

- 分类用于缩池
- 定量用于筛选和排序
- 定性标签用于解释和判断
- 监控用于持续复核

## 4. 当前实现如何落地

### 4.1 后端

- 定义表：`instrument_attribute_definition`
- 赋值表：`instrument_attribute_value`
- 字段注册：`field_registry_record`

关键元数据：

- `domain_code`
- `group_code`
- `display_order`
- `instrument_scope_json`
- `applicability_json`
- `rubric_json`
- `required_for_monitoring`

### 4.2 前端

Fund Detail 的第一页现在是 `Overview`，它承载两类内容：

- 基础情况
  全称、代码、管理人、peer category、fund vehicle 等
- Fund Taxonomy
  taxonomy path 下拉选择器；内部分类入口只保留这一块

其余产品框架分布改成：

- `Overview`
  产品身份和分类
- `Research`
  基金定性研究判断、与 Overview chart 打通的逐条 `Research Record`、`Rating` 和 `Current Investment View`
- `Monitoring`
  `Monitoring Assessment` 和 freshness / open items

Research / Monitoring 字段会按当前分类自动裁剪展示范围；分类未完成时，不再显示一整屏无意义的通用标签。

### 4.3 Watchlist

watchlist field registry 也已经按三层产品框架分类：

- `instrument_taxonomy`
- `research_framework`
- `monitoring_assessment`

这意味着 watchlist 的 filter 和 column selection 可按当前名单的资产类型范围使用研究字段；`Group By` 只暴露结构化维度与工作流字段，不把基金专属定性判断当成多资产分组。

## 5. 扩展原则

继续扩框架时，遵守下面几点：

- 先明确 `Fund Taxonomy`，再扩基金专属投资研究判断
- 各 instrument type 的分类树彼此独立，而且允许不同分支不同深度
- 每个标签都应有适用范围和判定口径，不接受只给 option 不给规则
- “管理人评价”和“产品风格标签”不要混在一个字段组里
- Monitoring 只放需要持续复核的观察结论，不回退成另一套身份标签
