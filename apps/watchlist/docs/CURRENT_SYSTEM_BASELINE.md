# 当前系统基线

目的：给后续继续开发的人一个和当前代码一致、没有兼容层包袱的起点

## 1. 当前仓库已经是什么

这不是“准备重建”的目录，而是一套已经运行中的 watchlist v2 基线：

- 后端：FastAPI + SQLAlchemy + Alembic
- 前端：React + Vite + TypeScript
- 主工作面：`Watchlists`、`Instrument Detail`、`Monitoring`
- 主数据链路：shared instruments / facts / manual profiles / read models / recalc

当前后端顶层包是 `backend/watchlist_app`，旧 `backend/app` 已移除。

## 2. 当前前端状态

### 2.1 已形成主工作面的页面

- `/watchlists`
  当前主入口，已经接上 watchlist、views、screener。
- `/instruments/:instrumentId`
  当前详情页 canonical 入口，先按 `public_fund / private_fund / etf / equity / index` dispatch；公募和私募分别进入独立详情入口，ETF、股票和指数进入 listed instrument 轻量页。来源 watchlist 只作为 query context 传递，不再进入主路径。
- `/monitoring`
  当前可用工作面，覆盖 freshness、缺失 label、open recalc job。

## 3. 当前产品框架边界

watchlist 和公募/私募详情已经不再使用“平铺 fund tags”模型，而是三层框架：

1. `Instrument Taxonomy`
2. `Research Tags`
3. `Monitoring Assessment`

这个边界已经落实到：

- 数据定义：`instrument_attribute_definition`
- 资产赋值：`instrument_attribute_value`
- watchlist field registry 分类：`product_taxonomy / research_framework / monitoring_assessment`
- Fund Detail UI 分区
- Monitoring 缺失项检查

当前产品可用范围是 `public_fund / private_fund / etf / equity / index`：公募与私募拥有独立 instrument type、详情入口和分类树，并共享稳定的基金详情基础结构；ETF、股票和指数使用聚焦 Overview / Performance / Risk / Price 的轻量详情。shared registry 可继续覆盖更广资产，但其他资产类型不进入 watchlist detail 主链路。

## 4. 当前最该相信的行为边界

下面这些已经在代码里成立：

- watchlist 主表一次取得当前筛选/排序下的完整服务端快照，前端初始渲染 `80` 行并支持继续显示或显示全部；全选只作用于当前已渲染行
- watchlist 顶部操作控件与 Portfolio toolbar 视觉一致，但仍使用 watchlist 本 app 的局部样式和页面实现，不建立跨 app 组件依赖
- watchlist `Download` 会导出当前筛选/排序结果的全量行，而不是只导当前页
- watchlist filter 选项按当前名单的全量结果计算，而不是固定采样前几页
- add / delete / move 后，filter 选项会重新拉取
- 系统名单固定为 `Index / All 公募 / All 私募`，分别同步全部 active 指数、公募与私募；股票、ETF 和自定义名单只保留人工加入的成员
- watchlist 的 `Peer Category` 仍然代表外部 peer group；内部分类属于 Watchlist 私有 taxonomy，不写回 Registry，并派生出 `instrument_taxonomy_level_1..7`
- 公募、私募、ETF 和指数 taxonomy 默认允许 `Unassigned`，必须由人在详情页明确选择；股票 taxonomy 由 Registry 的交易所身份映射到市场节点，不能手工改写
- watchlist `move` / `copy` 必须先命中 source watchlist membership，不能绕过 source 直接向 target 加资产
- 自定义 view 的 `view_id` 会做 path-safe slug 化；创建冲突会重试；复制 watchlist 时会按相同规则规范化源 view id
- canonical quote/NAV history 在 watchlist detail 是只读视图；导入、刷新、编辑共享行情/净值要回到 `Database Dashboard`。watchlist 派生层必须保留真实 `metric_family / quote_basis / role`，例如 `price/close/chart` 和 `nav/total_return_nav/total_return` 不能再只压成 NAV 文案。
- 主图和 Sparkline 统一展示区间累计收益并返回真实锚点；Overview 主图只使用双端拖动范围条。标量 performance 字段已覆盖 `1W / 1M / 3M / 6M / MTD / YTD / 1Y` 并投影到 watchlist row read model；MTD/YTD 按期初前一有效收盘，滚动窗口按请求 `as_of_date` 回看，不随 stale 实际终点平移。指数的 `close/last` 只表示字段身份，收益口径必须来自 Registry 的显式 `return_semantics`；未确认时保持 Unknown 并停止相对指标。完整规则见 [RETURN_SERIES_CONTRACT.md](./RETURN_SERIES_CONTRACT.md)。
- Watchlist 每个 instrument 使用自己的最新 calculation-series observation 作为 as-of；名单级 metadata 只报告行终点范围，不代表共同计算日。不同终点不能直接做收益/风险分组平均或 peer 排名；benchmark 比较只使用双方完全相同的共同观测收盘点构造共同起止和中间 period。
- 年化收益至少需要一个完整日历年。Registry expected frequency 是计算频率主口径；daily 序列按配置的 market calendar 检查真实 session，内部缺点时回撤、波动率、Sharpe 等路径指标明确 withheld，节假日不误报。
- monitoring 的缺失项检查是 taxonomy-aware；它既检查当前 instrument type / 分类下适用且 `required_for_monitoring` 的字段，也检查对应 taxonomy assignment 是否完整
- 后端 API 已统一到 `instrument` 主语；旧 `/api/funds/...` 兼容路由已删除
- 前端详情 canonical 路由是 `/instruments/:instrumentId`
- watchlist 不再单独提供共享资产库页面；公募、私募、ETF 和指数的共享资产浏览与维护统一回到 `Database Dashboard`，股票则在添加弹窗搜索本地 FMP 目录并按需 materialize
- stale read repair 现在写 durable recalc job，并由后台 worker 自动消费；worker 会回收超时 `running` job，Web 请求只负责发现 stale，不直接补算
- worker 还会按 instrument id 做有界 keyset 分页，对账 Registry `market_data_updated_at` 与所有本地 read model 的最旧 `source_cutoff_at`；通知丢失或进程重启后会自动补建同一条 per-instrument 去重队列任务
- 产品框架的示例标签值不再在 migration 或运行时自动注入；watchlist 数据只保留显式录入和值得追溯的派生结果

## 5. 当前后端状态

### 5.1 已落地的 API 分组

- `health`
- `watchlists`
- `instruments`
- `instrument-attributes`
- `taxonomies`
- `field-registry`
- `screener`
- `facts`
- `monitoring`
- `recalc`

### 5.2 已落地的数据能力

- watchlist / view / row read model
- shared-registry-backed instrument add into watchlist
- instrument library search / resolve
- type-specific instrument taxonomy tree / taxonomy assignment / derived taxonomy levels
- NAV facts ingest
- holdings ingest
- performance / risk / exposure materialization；研究星级只来自人工 research profile
- instrument manual profiles（当前公募与私募详情最完整）
- instrument product framework definition / assignment
- recalc job persistence
- queued recalc worker

## 6. 当前默认存储

- 运行时数据库连接：`PORTFOLIO_OPS_WATCHLIST_DATABASE_URL`
- 迁移连接：`PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL`
- schema：`PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA`
- 迁移目录：`backend/alembic`
- 初始化方式：只执行 `alembic upgrade head`

## 7. 当前还没完成的模块

下面这些现在不应被文档假装成“已经落地”：

- cash / FX / other 的 instrument detail overlay
- Documents workspace 的完整工作面
- Database Dashboard 侧 Email sync / OCR / extraction pipeline 的完整闭环

## 8. 继续推进时建议按什么顺序做

1. 保持当前公募 / 私募 / ETF / 股票 / 指数边界；直接债券不进入 Registry 或 Watchlist。只有出现明确使用需求时，再为 cash / FX / other 增加适合其语义的详情工作面
2. 继续补强 monitoring 和 recalc worker 的观测/告警
3. 分别扩展公募、私募及其他资产类型的 taxonomy 与 taxonomy-aware research packs
4. 把导入链路从“手工写入 facts”补到更完整的 source / document 流

## 9. 文档维护规则

从现在开始，文档只做三类事情：

- 记录当前实现基线
- 记录当前可执行 API 与数据边界
- 记录明确的下一步推进事项

不再保留：

- 旧兼容层说明
- 已废弃的分类/tag 方案稿
- 与当前仓库状态脱节的旧方案稿
