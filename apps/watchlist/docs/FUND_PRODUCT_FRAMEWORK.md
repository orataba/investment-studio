# Fund 产品框架

适用范围：当前 watchlist app 里 `fund` 资产的分类、研究标签和监控评估框架

## 1. 目标

watchlist 的未来使用场景不是“在一堆平铺标签里找产品”，而是：

1. 先按分类定义产品池
2. 再用定量数据横向筛选
3. 最后用定性标签和监控结论做判断

因此当前 fund 框架明确拆成三层：

- `Fund Taxonomy`
- `Research Tags`
- `Monitoring Assessment`

这三层不会再混成一套“通用 fund tags”。

## 2. 三层模型

### 2.1 Fund Taxonomy

回答的问题是：`这到底是什么产品`

当前 fund 分类已经从固定 `fund_category_l1/l2/l3` 升级成真实树结构：

- taxonomy 定义表：`instrument_taxonomy_node`
- 当前赋值表：`instrument_taxonomy_assignment`
- Overview 页基础字段：`fund_vehicle`
- watchlist / monitoring / research applicability 使用派生字段：
  `fund_regime`、`fund_taxonomy_level_1..6`、`fund_taxonomy_leaf`、`fund_taxonomy_path`

其中：

- `fund_vehicle`
  仍然是“产品形态”，比如 ETF、场外开放式、契约型私募基金
- taxonomy path
  才是策略分类树本身，支持不同分支不同深度；其中 `fund_regime` 单独代表 `公募 / 私募` 根节点，`fund_taxonomy_level_1..6` 从根节点内部的一级分类开始编号
- taxonomy assignment
  默认允许为空，不做自动推断；正式分类必须由研究人员在详情页 `Overview -> Fund Taxonomy` 手动确认

当前树先覆盖 fund 的核心研究范围：

- 公募：
  `股票型 / 混合型 / 债券型 / QDII / 商品型 / REITS / FOF / 其他`
- 私募：
  `股票策略 / 债券策略 / 期货及衍生品策略 / 多资产策略 / 组合基金 / 其他`

最近重点覆盖的叶子已经内置：

- `300指增 / 500指增 / 1000指增 / 2000指增 / 红利指增 / 量化选股 / 其他指增`
- `主观CTA / 量化CTA / 主观趋势 / 主观套利 / 主观多策略 / 量化趋势 / 量化套利 / 量化多策略`
- `纯债策略 / 债券增强 / 债券复合 / 转债交易`
- `宏观策略 / 套利策略 / 复合策略`
- `FOF / MOM`
- `标准股票型 / 指数股票型 / 偏股型 / 灵活配置型 / 股债平衡型 / 偏债型 / 策略型`
- `纯债型 / 普通债券型 / 可转债型 / 指数债券型 / 同业存单型`
- `QDII房地产信托 / QDII股票型 / QDII混合型 / QDII商品型 / QDII债券型`
- `贵金属基金 / 其他商品基金 / 股票型FOF / 债券型FOF / 混合型FOF / 养老目标FOF`

示例路径：

- `私募 -> 股票策略 -> 量化多头 -> 500指增`
- `私募 -> 期货及衍生品策略 -> 量化CTA -> 量化趋势`
- `私募 -> 债券策略 -> 转债交易`
- `公募 -> 债券型 -> 可转债型`
- `公募 -> FOF -> 养老目标FOF`

分类是入口层，不是标签层；Overview 页先定池子，Research 页再看定性标签。

### 2.2 Research Tags

回答的问题是：`这类产品在研究上有什么特征`

它承载 FOF 研究里需要标准化的定性结论，但不是产品身份字段。
当前受控词表和判定口径以 [FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md](./FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md) 为准。

当前主要分组包括：

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

`coverage_status` 代表 watchlist / portfolio 生命周期状态，例如 `Watch / Proposed / Invested / Paused / Exited`，可以作为 watchlist 列和筛选字段使用，但不再被解释为 `Research Status`。Research 页里的当前研究观点字段使用 `Research View`，避免和生命周期状态混淆。

这里允许按分类做适用范围控制：

- 某些标签只适用于 `公募`
- 某些标签只适用于 `私募`
- 某些标签只适用于特定 `fund_taxonomy_level_*`

因此它不是“所有 fund 共用同一张标签表”，而是同一套定义机制下的 taxonomy-aware label packs。

### 2.3 Monitoring Assessment

回答的问题是：`这只产品当前最该持续盯什么`

这层放那些会随时间变化、且需要周期性复核的观察结论。

当前主要分组包括：

- `monitoring_risk`
  `volatility_bucket`、`drawdown_control`、`equity_correlation_bucket`
- `monitoring_regime`
  `preferred_regime`、`weak_regime`
- `monitoring_operations`
  `style_stability`、`transparency_quality`

Monitoring 页面不会再静态要求所有 fund 补同一套字段；它只检查：

- `required_for_monitoring = true`
- 当前资产类型适用
- 当前分类路径适用

## 3. 当前工作流

围绕 watchlist 的实际工作流应当是：

1. `Fund Taxonomy`
   先定义 universe
2. `Screening`
   看收益、回撤、波动、容量、规模、tracking error 等定量指标
3. `Research Tags`
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
  `Qualitative Research Tags`、与 Overview chart 打通的 `Research Notes`、`Rating` 和 `Research View`
- `Monitoring`
  `Monitoring Assessment` 和 freshness / open items

Research / Monitoring 字段会按当前分类自动裁剪展示范围；分类未完成时，不再显示一整屏无意义的通用标签。

### 4.3 Watchlist

watchlist field registry 也已经按三层产品框架分类：

- `product_taxonomy`
- `research_framework`
- `monitoring_assessment`

这意味着 watchlist 的 filter / group by / column selection 可以直接围绕产品框架工作，而不是依赖旧的平铺 tag 语义。

## 5. 当前明确不再采用的做法

下面这些已经被移除，不再作为基线：

- `strategy_family / strategy_subtype` 作为主分类
- 把分类、研究标签、监控结论混在一张 fund tag 表里
- 旧 `/api/funds/...` 兼容路由
- Fund Detail 中“Import NAV / Save NAV / Update Now”这类已经退役的写入口

## 6. 下一步扩展原则

继续扩框架时，遵守下面几点：

- 先扩 `Fund Taxonomy`，再扩 `Research Tags`
- fund 的分类树优先于其它 asset class，而且允许不同分支不同深度
- 每个标签都应有适用范围和判定口径，不接受只给 option 不给规则
- “管理人评价”和“产品风格标签”不要混在一个字段组里
- Monitoring 只放需要持续复核的观察结论，不回退成另一套身份标签
