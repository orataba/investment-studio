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
  当前详情页 canonical 入口，先做 instrument dispatch，再进入 fund overlay；来源 watchlist 只作为 query context 传递，不再进入主路径。Fund Detail 当前以 `Overview` 为首屏，先展示基础情况和基金分类树。
- `/monitoring`
  当前可用工作面，覆盖 freshness、缺失 label、open recalc job。

### 2.2 仍是轻量页的一级路由

- `/research`
- `/documents` 轻量入口保留；fund detail 已接入 instrument-level documents profile，后端支持手工文件上传与下载

它们现在只是轻量入口，不是假装完整 workspace 的壳页。

## 3. 当前产品框架边界

watchlist 和 fund detail 已经不再使用“平铺 fund tags”模型，而是三层框架：

1. `Fund Taxonomy`
2. `Research Tags`
3. `Monitoring Assessment`

这个边界已经落实到：

- 数据定义：`instrument_attribute_definition`
- 资产赋值：`instrument_attribute_value`
- watchlist field registry 分类：`product_taxonomy / research_framework / monitoring_assessment`
- Fund Detail UI 分区
- Monitoring 缺失项检查

当前产品可用范围是 `fund / etf / equity / index`：fund 使用完整三层产品框架和详情工作面，ETF、股票和指数使用聚焦 Overview / Performance / Risk / Price 的轻量详情。shared registry 可继续覆盖更广资产，但其他资产类型不进入 watchlist detail 主链路。

## 4. 当前最该相信的行为边界

下面这些已经在代码里成立：

- watchlist 主表一次取得当前筛选/排序下的完整服务端快照，前端初始渲染 `80` 行并支持继续显示或显示全部；全选只作用于当前已渲染行
- watchlist 顶部操作控件与 Portfolio toolbar 视觉一致，但仍使用 watchlist 本 app 的局部样式和页面实现，不建立跨 app 组件依赖
- watchlist `Download` 会导出当前筛选/排序结果的全量行，而不是只导当前页
- watchlist filter 选项按当前名单的全量结果计算，而不是固定采样前几页
- add / delete / move 后，filter 选项会重新拉取
- watchlist 的 `Peer Category` 仍然代表外部 peer group；内部基金分类已经改成独立 taxonomy tree，并派生出 `fund_regime + 一级/二级/三级分类...`
- fund taxonomy 默认允许 `Unassigned`；系统不再基于旧分类字段或 migration 自动猜测分类，必须由人在详情页明确选择
- watchlist `move` / `copy` 必须先命中 source watchlist membership，不能绕过 source 直接向 target 加资产
- 自定义 view 的 `view_id` 会做 path-safe slug 化；创建冲突会重试；复制 watchlist 时会按相同规则规范化源 view id
- canonical quote/NAV history 在 watchlist detail 是只读视图；导入、刷新、编辑共享行情/净值要回到 `Database Dashboard`。watchlist 派生层必须保留真实 `metric_family / quote_basis / role`，例如 `price/close/chart` 和 `nav/total_return_nav/total_return` 不能再只压成 NAV 文案。
- 主图和 Sparkline 统一展示区间累计收益并返回真实锚点；Overview 主图只使用双端拖动范围条。标量 performance 字段已覆盖 `1W / 1M / 3M / 6M / MTD / YTD / 1Y` 并投影到 watchlist row read model；MTD/YTD 按期初前一有效收盘，滚动窗口按请求 `as_of_date` 回看，不随 stale 实际终点平移。指数的 `close/last` 只表示字段身份，收益口径必须来自 Registry 的显式 `return_semantics`；未确认时保持 Unknown 并停止相对指标。完整规则见 [RETURN_SERIES_CONTRACT.md](./RETURN_SERIES_CONTRACT.md)。
- Watchlist 每个 instrument 使用自己的最新 calculation-series observation 作为 as-of；名单级 metadata 只报告行终点范围，不代表共同计算日。不同终点不能直接做收益/风险分组平均或 peer 排名；benchmark 比较只使用双方完全相同的共同观测收盘点构造共同起止和中间 period。
- 年化收益至少需要一个完整日历年。Registry expected frequency 是计算频率主口径；daily 序列按配置的 market calendar 检查真实 session，内部缺点时回撤、波动率、Sharpe 等路径指标明确 withheld，节假日不误报。
- monitoring 的缺失项检查是 taxonomy-aware；它既检查当前分类下适用且 `required_for_monitoring` 的字段，也检查 `fund_regime` 和分类叶子是否完整
- 后端 API 已统一到 `instrument` 主语；旧 `/api/funds/...` 兼容路由已删除
- 前端详情 canonical 路由是 `/instruments/:instrumentId`
- watchlist 不再单独提供共享资产库页面；共享资产浏览和维护统一回到 `Database Dashboard`
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
- `copilot`

### 5.2 已落地的数据能力

- watchlist / view / row read model
- shared-registry-backed instrument add into watchlist
- instrument library search / resolve
- fund taxonomy tree / taxonomy assignment / derived taxonomy levels
- NAV facts ingest
- holdings ingest
- performance / risk / exposure materialization；研究星级只来自人工 research profile
- instrument manual profiles（当前 fund overlay 最完整）
- instrument product framework definition / assignment
- recalc job persistence
- queued recalc worker
- copilot context assembly（后端保留，UI 默认隐藏）

## 6. 当前默认存储

- 运行时数据库连接：`PORTFOLIO_OPS_WATCHLIST_DATABASE_URL`
- 迁移连接：`PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL`
- schema：`PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA`
- 迁移目录：`backend/alembic`
- 初始化方式：只执行 `alembic upgrade head`

## 7. 当前还没完成的模块

下面这些现在不应被文档假装成“已经落地”：

- bond / cash / FX / other 的 instrument detail overlay
- Documents workspace 的完整工作面
- Database Dashboard 侧 Email sync / OCR / extraction pipeline 的完整闭环
- 真实 OpenAI provider 已启用且对用户开放的 Copilot

## 8. 继续推进时建议按什么顺序做

1. 先保持当前 fund / ETF / equity / index 边界；只有出现明确使用需求时，再为 bond / cash / FX / other 增加适合其语义的详情工作面
2. 继续补强 monitoring 和 recalc worker 的观测/告警
3. 扩展 fund taxonomy 与 taxonomy-aware research packs
4. 把导入链路从“手工写入 facts”补到更完整的 source / document 流
5. 把 Copilot 从 stub 切到真实 provider，并在具备真实能力后重新开放 UI

## 9. 文档维护规则

从现在开始，文档只做三类事情：

- 记录当前实现基线
- 记录当前可执行 API 与数据边界
- 记录明确的下一步推进事项

不再保留：

- 旧兼容层说明
- 已废弃的分类/tag 方案稿
- 与当前仓库状态脱节的旧方案稿
