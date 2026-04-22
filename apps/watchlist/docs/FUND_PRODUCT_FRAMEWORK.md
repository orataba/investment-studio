# Fund 产品框架

状态：Current baseline  
日期：2026-04-22  
适用范围：当前 watchlist app 里 `fund` 资产的分类、研究标签和监控评估框架

## 1. 目标

watchlist 的未来使用场景不是“在一堆平铺标签里找产品”，而是：

1. 先按分类定义产品池
2. 再用定量数据横向筛选
3. 最后用定性标签和监控结论做判断

因此当前 fund 框架明确拆成三层：

- `Classification`
- `Research Tags`
- `Monitoring Assessment`

这三层不会再混成一套“通用 fund tags”。

## 2. 三层模型

### 2.1 Classification

回答的问题是：`这到底是什么产品`

当前 fund 分类树的主字段是：

- `fund_regime`
  公募 / 私募
- `fund_vehicle`
  ETF、场外开放式、契约型私募基金等载体
- `fund_category_l1`
  一级分类，大池子
- `fund_category_l2`
  二级分类，主方法论
- `fund_category_l3`
  三级分类，叶子分支

当前已经内置的方向包括：

- 公募：
  `主动权益 / 指数工具 / 固定收益 / 固收+ / 多资产 / 商品 / REITs / QDII / FOF/MOM / 货币`
- 私募：
  `股票 / 债券 / 宏观 / 相对价值 / 期权/波动率`

示例路径：

- `公募 -> ETF -> 指数工具 -> 债券指数 -> 综合债指数`
- `私募 -> 契约型私募基金 -> 多资产 -> 多策略 -> 复合多策略`
- `私募 -> 契约型私募基金 -> 股票 -> 量化选股 -> 500指增`

分类是入口层，不是标签层。

### 2.2 Research Tags

回答的问题是：`这类产品在研究上有什么特征`

它承载 FOF 研究里需要标准化的定性结论，但不是产品身份字段。

当前主要分组包括：

- `research_coverage`
  `coverage_status`、`focus_bucket`
- `research_process`
  `implementation_style`、`trading_universe`、`alpha_source`
- `research_style`
  `style_profile`、`portfolio_construction`
- `research_manager`
  `manager_assessment`、`team_stability_assessment`、`historical_delivery`、`capacity_bucket`

这里允许按分类做适用范围控制：

- 某些标签只适用于 `公募`
- 某些标签只适用于 `私募`
- 某些标签只适用于特定 `fund_category_l1/l2/l3`

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

1. `Classification`
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
- `asset_scope_json`
- `applicability_json`
- `rubric_json`
- `required_for_monitoring`

### 4.2 前端

Fund Detail 的产品框架区已经拆成三个 section：

- `Classification`
- `Research Tags`
- `Monitoring Assessment`

Research / Monitoring 字段会按当前分类自动裁剪展示范围；分类未完成时，不再显示一整屏无意义的通用标签。

### 4.3 Watchlist

watchlist field registry 也已经按三层产品框架分类：

- `product_classification`
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

- 先扩 `Classification`，再扩 `Research Tags`
- fund 的分类树优先于其它 asset class
- 每个标签都应有适用范围和判定口径，不接受只给 option 不给规则
- “管理人评价”和“产品风格标签”不要混在一个字段组里
- Monitoring 只放需要持续复核的观察结论，不回退成另一套身份标签
