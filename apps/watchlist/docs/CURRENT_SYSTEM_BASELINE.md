# 当前系统基线

状态：Current baseline  
日期：2026-04-22  
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
- `/watchlists/:watchlistId/instruments/:assetId`
  当前详情页入口，先做 instrument dispatch，再进入 fund overlay。
- `/instruments`
  当前共享资产库入口，已经可以按关键词和资产类型浏览 shared registry。
- `/monitoring`
  当前可用工作面，覆盖 freshness、缺失 label、open recalc job。

### 2.2 仍是轻量页的一级路由

- `/research`
- `/documents`

它们现在只是轻量入口，不是假装完整 workspace 的壳页。

## 3. 当前产品框架边界

watchlist 和 fund detail 已经不再使用“平铺 fund tags”模型，而是三层框架：

1. `Classification`
2. `Research Tags`
3. `Monitoring Assessment`

这个边界已经落实到：

- 数据定义：`instrument_attribute_definition`
- 资产赋值：`instrument_attribute_value`
- watchlist field registry 分类：`product_classification / research_framework / monitoring_assessment`
- Fund Detail UI 分区
- Monitoring 缺失项检查

当前产品可用范围明确是 `fund` 资产类型；shared registry 可继续覆盖更广资产，但 watchlist detail 只对 fund 开放。

## 4. 当前最该相信的行为边界

下面这些已经在代码里成立：

- watchlist 主表按页加载，默认每页 `50` 行；页面支持翻页
- watchlist `Download` 会导出当前筛选/排序结果的全量行，而不是只导当前页
- watchlist filter 选项按当前名单的全量结果计算，而不是固定采样前几页
- 当前页 add / delete / move 后，filter 选项会重新拉取
- watchlist `move` / `copy` 必须先命中 source watchlist membership，不能绕过 source 直接向 target 加资产
- 自定义 view 的 `view_id` 会做 path-safe slug 化；创建冲突会重试；复制 watchlist 时也会清洗 legacy custom view id
- canonical NAV history 在 watchlist detail 是只读视图；导入、刷新、编辑共享净值要回到 `Platform / Instruments`
- monitoring 的缺失项检查是 taxonomy-aware，只检查当前分类下适用且 `required_for_monitoring` 的字段
- 后端 API 已统一到 `instrument` 主语；旧 `/api/funds/...` 兼容路由已删除
- 前端路由也只保留 `/watchlists/.../instruments/...` 和 `/instruments`
- stale read repair 现在写 durable recalc job，并由后台 worker 自动消费；worker 会回收超时 `running` job，Web 请求只负责发现 stale，不直接补算
- 产品框架的示例标签值不再在 migration 或运行时自动注入；watchlist 数据只保留显式录入和值得追溯的派生结果

## 5. 当前后端状态

### 5.1 已落地的 API 分组

- `health`
- `watchlists`
- `instruments`
- `instrument-attributes`
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
- NAV facts ingest
- holdings ingest
- performance / risk / exposure / ratings materialization
- instrument manual profiles（当前 fund overlay 最完整）
- instrument product framework definition / assignment
- recalc job persistence
- queued recalc worker
- copilot context assembly（后端保留，UI 默认隐藏）

## 6. 当前默认存储

- 运行时数据库连接：`FTV2_DATABASE_URL`
- 迁移连接：`FTV2_ALEMBIC_DATABASE_URL`
- schema：`FTV2_DATABASE_SCHEMA`
- 迁移目录：`backend/alembic`
- 初始化方式：只执行 `alembic upgrade head`

## 7. 当前还没完成的模块

下面这些现在不应被文档假装成“已经落地”：

- non-fund instrument detail overlay
- Documents workspace 的完整工作面
- Email sync / OCR / extraction pipeline 的完整闭环
- 真实 OpenAI provider 已启用且对用户开放的 Copilot

## 8. 继续推进时建议按什么顺序做

1. 如果要扩展产品范围，再补 non-fund instrument detail overlay；否则继续把 fund-only 路径打磨完整
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
- 与当前仓库状态脱节的历史规划稿
