# 当前系统基线

状态：Current baseline  
日期：2026-04-16  
目的：给后续继续推进项目的人一个干净、可执行、和当前代码一致的起点

## 1. 当前仓库已经是什么

这不是“准备重建”的目录，而是一套已经跑起来的 v2 基线工程：

- 后端：FastAPI + SQLAlchemy + Alembic，默认目标数据库为 PostgreSQL
- 前端：React + Vite + TypeScript
- 主工作面：`Watchlists` + `Instrument Detail`
- 主数据链路：shared instruments / facts / manual profiles / read models / recalc / copilot

## 2. 当前前端状态

## 2.1 已经形成主工作面的页面

- `/watchlists`
  当前主入口，已经接上 watchlist、view、screener、copilot
- `/watchlists/:watchlistId/instruments/:assetId`
  当前详情页入口，fund overlay 最完整；其它 asset type 仍会落到 instrument stub
- `/instruments`
  当前共享资产库入口，已经可以按关键词和资产类型浏览 shared registry

## 2.2 仍是占位页的一级路由

- `/research`
- `/documents`
- `/monitoring`

这些路由已经有位置，但还不是完整工作面。

## 2.3 当前前端重点

当前已经沉淀下来的产品重点是：

- Morningstar 风格的 watchlist / detail 骨架
- Quote 图表与 drawdown 联动
- 详情页多 tab 数据读取与人工维护
- Copilot drawer 与上下文摘要
- 多资产命名和入口已经收口到 `instrument`，但非 fund 详情 overlay 仍未补全

## 3. 当前后端状态

## 3.1 已落地的 API 分组

- `health`
- `watchlists`
- `screener`
- `field-registry`
- `instrument-attributes`
- `instruments`
- `facts`
- `copilot`
- `recalc`
- `funds-compat`

## 3.2 已落地的数据能力

- watchlist / view / row read model
- shared-registry-backed instrument add into watchlist
- shared instrument library search / resolve
- NAV facts ingest
- holding snapshot ingest
- performance / risk / portfolio / ratings materialization
- instrument manual profiles（当前 fund overlay 最完整）
- recalc job persistence
- copilot context assembly

## 3.3 当前默认存储

- 数据库：`postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu`
- schema：`watchlist`
- 迁移：`backend/alembic`
- 初始化方式：只执行 `alembic upgrade head`

## 4. 当前最该相信的工程边界

下面这些是已经在代码里成立的：

- 净值口径只有 `nav` 和 `nav_with_dividend`
- 前台主要读取 read models，而不是直接读事实表
- 手工维护内容走 manual profiles，不等价于 canonical facts
- 资产主档新增与基础数据维护在 Platform `Instruments`；Watchlist 只搜索并引用共享资产库
- 详情页入口已经是 instrument-based，但 fund 之外的 detail overlay 还没补齐
- Copilot 接口已稳定，但 provider 默认仍是 stub
- recalc 既支持排队记录，也支持直接执行

## 5. 当前还没有完成的模块

下面这些现在不应被文档假装成“已经落地”：

- 非 fund 资产的 detail overlay
- Documents workspace
- Monitoring cockpit
- Email sync / OCR / extraction pipeline 的完整工作面
- 真正异步消费的 recalc worker
- OpenAI provider 已启用的 Copilot

## 6. 继续推进时建议按什么顺序做

建议优先级：

1. 补齐 non-fund instrument detail overlay，让 instrument detail 真正配得上多资产入口
2. 把 `Monitoring` 变成真实页面，并补上真正消费 queued recalc 的 worker
3. 继续补全 read models，减少详情页 fallback payload
4. 把导入链路从“手工写入 facts”扩到更完整的 source / document 流
5. 把 Copilot 从 stub 切到真实 provider

## 7. 文档维护规则

从现在开始，文档只做三类事情：

- 记录当前实现基线
- 记录当前可用 API 与数据边界
- 记录明确的下一步推进事项

不再保留：

- 纯历史背景
- 已经过时的“是否重建”讨论
- 与当前仓库状态脱节的大篇幅规划稿
