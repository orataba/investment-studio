# Yungu 使用手册

本文面向公司同事，说明当前 `Yungu` 的日常使用方式。系统分为三个入口：`Platform` 维护共享资产和行情主数据，`Watchlist` 管理基金/指数观察列表，`Portfolio` 管理组合、交易、绩效、风险和研究工作台。请使用公司内网发布地址访问；本地开发地址和账号密码不写入文档。

## 1. Platform：共享资产库

`Platform / Database Dashboard` 是资产主数据入口。新增基金、指数或其他可投资标的时，先在这里建立 instrument，维护名称、类型、币种、ticker/ISIN 等 identifiers，再录入或导入 NAV、close price、FX 等 market data。Watchlist 和 Portfolio 都读取这套共享资产事实，不在各自应用里重复创建资产主档。

日常操作顺序：

1. 搜索资产，确认是否已存在。
2. 不存在时新建 instrument，并填写主要 identifier。
3. 在行情区录入或导入市场数据；基金优先使用 NAV / NAV with dividend，指数可使用 close。
4. 保存后回到 Watchlist 或 Portfolio 引用该 instrument。

如果某个资产在下游页面没有数据，先检查 Platform 中是否有正确的 instrument type、identifier、币种和可用行情日期。

## 2. Watchlist：基金和指数观察列表

`Watchlist` 用于维护基金/指数池、查看单资产详情、筛选字段和导出结果。新增资产时，从共享资产库搜索并添加到 watchlist；不能在 Watchlist 内创建资产主档。

主表支持分页、筛选、排序、分组、列配置和下载。`Data & Columns` 可选择展示字段；`Name` 固定为第一列。`Download` 导出当前筛选/排序后的全量结果，不只导出当前页。`Group By` 支持可写的 taxonomy 或离散属性分组；拖动资产到分组会同步写回后端，只读指标分组不能拖拽。

单资产详情页包含 `Overview / Quote / Performance / Risk / Monitoring` 等工作面。基金详情保留产品 taxonomy、研究标签和监控判断；指数详情是轻量工作面，重点展示 Overview、Performance、Risk。指数已支持基于 close 序列计算 YTD、MTD、1M、年化收益、最大回撤、波动率、Sharpe 和当前回撤等字段。

分类和标签都应由使用者显式维护。系统不会自动推断 fund/index taxonomy，也不会注入示例标签。若发现字段为空，优先确认资产是否有足够行情、是否完成 recalc，以及该字段是否适用于当前 instrument type。

## 3. Portfolio：组合工作台

`Portfolio` 用于组合事实、绩效复盘、风险监控和研究调仓。进入组合后，常用页面包括 `Overview`、`Holdings`、`Accounts`、`Transactions`、`Performance`、`Risk`、`Taxonomies` 和 `Research`。

`Transactions` 是交易事实入口，支持创建、更新、删除交易。账户成本法支持 `FIFO` 和 `moving_average`；成本法影响成本、已实现/未实现盈亏和 lot 展示，不影响 TWR 绩效口径。行情、交易或账户变化后，系统会把组合标记为 stale 并刷新物化快照。

`Overview` 是组合默认首页，展示组合市值、TWR index、回撤、sleeve 结构和 top holdings。没有完整 fresh snapshot 时，页面不会用部分价格或过期数据硬算结果。

`Performance` 用于区间复盘。`Calculation` 以日频 TWR、期间 P&L、资金流和贡献拆分为核心；分组支持 instrument type、currency、account、taxonomy 等。表格可保存自定义 view，并导出 CSV。

`Risk` 是当前权重口径的风险工作台，展示 rolling volatility / Sharpe、相关性矩阵、current drift 和风险贡献。它使用当前非现金持仓权重和资产历史收益窗口，不是持仓期间归因；真实历史归因请看 Performance。

`Taxonomies` 维护组合 planning taxonomy、sleeve tree、assignment、TargetSet 和 default planning taxonomy。Research 使用这里的结构求解目标权重，不单独维护另一套目标体系。

`Research` 用于当前 target solve 和回测。先选择 planning taxonomy、scope、目标维度、资本模式、风险窗口、缺失收益政策、rebalance frequency 和 benchmark，再点击运行。当前支持 `1W / 1M / 3M` 回测频率，结果包含目标权重、top sleeve bounds 状态、风险预算诊断、调仓缺口、回测曲线、回撤、YTD、Calmar、基准比较和 relative metrics。运行失败不会覆盖上一轮成功结果；失败原因会保留给用户判断。

## 4. 常见问题

- 找不到资产：先到 Platform 创建或修正共享资产，再回到 Watchlist/Portfolio 引用。
- 指标为空：通常是行情不足、日期不重叠、分类不适用或刷新尚未完成。
- 组合 as-of 没更新：检查是否存在 stale / incomplete snapshot，等待刷新完成或补齐行情。
- Research 失败：重点检查 TargetSet、scope assignment、缺失收益政策、risk budget 目标和 top sleeve bounds。
- 数据看起来不一致：优先确认页面口径。Watchlist 是资产池视角；Portfolio Performance 是真实组合复盘；Portfolio Risk 是当前权重风险视角；Research 是规划和假设回测视角。

## 5. 使用纪律

共享资产、行情、交易和 taxonomy 都是业务事实，请先确认再保存。不要用临时测试数据污染正式 watchlist 或 portfolio。导出结果可用于沟通和复核，但投资判断仍需结合数据来源、覆盖期、缺失提示和人工研究结论。
