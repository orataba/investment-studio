# Fund Terminal V2 数据模型与 API 基线

状态：Current baseline  
日期：2026-04-23  
适用范围：当前 `/home/shaw/yungu/apps/watchlist` 仓库已经实现的数据分层、关键表语义和后端 API 边界

## 1. 这份文档解决什么问题

这里只回答当前工程的四个问题：

1. 数据现在按什么层次组织
2. fund 产品框架如何入库和读取
3. facts / manual profile / read model / recalc 怎样串起来
4. 后端当前到底暴露了哪些 API

它不是旧系统兼容说明，也不再描述 `/api/funds/...` 别名。

## 2. 当前代码入口

- 后端包：`backend/watchlist_app`
- API router：`backend/watchlist_app/api/router.py`
- 数据模型：`backend/watchlist_app/db/models`
- 迁移目录：`backend/alembic`

后端 API 现在统一以 `instrument` 为主语；旧 `funds-compat` router 已删除。

## 3. 当前数据分层

当前实现可以概括成 7 层：

1. `Shared Instruments`
2. `Watchlist Ownership`
3. `Instrument Product Framework`
4. `Canonical Facts`
5. `Manual Profiles`
6. `Read Models`
7. `Application API / backend extensions`

主链路如下：

```text
shared instruments / manual ingest / facts ingest
  -> canonical facts + manual profiles
  -> recalc
  -> read models
  -> watchlists / instrument detail / monitoring / optional backend extensions
```

## 4. 当前关键表语义

### 4.1 Watchlist Ownership

这一层负责“谁在看哪些资产、怎么展示”：

- `watchlist`
- `watchlist_item`
- `watchlist_view`
- `watchlist_view_column`

注意：

- `move` / `copy` 现在会严格校验 source membership
- custom view id 会做 path-safe slug 化
- 复制 watchlist 时也会清洗 legacy custom view id

### 4.2 Instrument Product Framework

这一层负责 fund 的分类、研究标签和监控评估：

- `instrument_taxonomy_node`
- `instrument_taxonomy_assignment`
- `instrument_attribute_definition`
- `instrument_attribute_value`
- `field_category`
- `field_registry_record`

其中：

- taxonomy tables
  管 fund 分类树本身和当前叶子赋值
- attribute tables
  管 `fund_vehicle`、研究标签、监控评估等非树形字段
- `category_name`
  仍然保留为外部 `peer category` 比较口径，不承担内部基金分类职责

`instrument_attribute_definition` 当前关键字段：

- `attribute_key`
- `domain_code`
- `group_code`
- `display_order`
- `options_json`
- `asset_scope_json`
- `applicability_json`
- `rubric_json`
- `required_for_monitoring`

它已经不再只是“tag options 表”，而是完整的产品框架定义表。

### 4.3 Canonical Facts

当前 watchlist app 内仍然直接维护的 canonical facts 主要包括：

- NAV facts
- holdings snapshots / positions

facts 路由已经统一到 asset 主语：

- `GET /api/facts/assets/{asset_id}/nav`
- `POST /api/facts/assets/{asset_id}/nav`
- `GET /api/facts/assets/{asset_id}/holdings/current`
- `POST /api/facts/assets/{asset_id}/holdings`

### 4.4 Manual Profiles

详情页里那些“可编辑但不是 canonical fact”的内容，走 manual profiles：

- `people`
- `strategy`
- `price`
- `documents`
- `research`

这些内容服务于 detail overlay，不等价于原始事实。

### 4.5 Read Models

前台主要读取 read models，而不是直接扫事实表。

当前已经落地的 read model 主要包括：

- `watchlist_row_read_model`
- summary / chart / performance / risk / exposure / ratings read models
- asset summary payload

### 4.6 Recalc Jobs

重算已经 durable 化，不再依赖临时线程：

- `recalc_job`

stale read repair 也只会写 job，不会直接在 Web 请求里补算；后台 worker 会异步消费这些 queued jobs。

## 5. fund 产品框架如何落地

### 5.1 三层域

当前产品框架按两套结构协作：

1. taxonomy tree
2. attribute domains

attribute domain 只分三层：

- `overview`
- `research`
- `monitoring`

### 5.2 Watchlist 字段注册分组

field registry 对应的 category 现在是：

- `product_taxonomy`
- `research_framework`
- `monitoring_assessment`

这保证了 watchlist 的列选择、filter、group by 都能直接围绕产品框架工作。

### 5.3 Monitoring 缺失项检查

Monitoring 页面不再硬编码一张“所有 fund 必填 tags”清单。

当前逻辑是：

1. 读取 `instrument_attribute_definition`
2. 只看 `required_for_monitoring = true`
3. 再按 `asset_scope_json` 与 `applicability_json` 判断当前资产是否适用
4. 同时检查 taxonomy 派生出来的必填分类上下文
5. 只对适用字段做缺失检查

## 6. 当前 API 分组

### 6.1 Health

- `GET /api/health`

### 6.2 Watchlists

- `GET /api/watchlists`
- `POST /api/watchlists`
- `POST /api/watchlists/reorder`
- `POST /api/watchlists/{watchlist_id}/copy`
- `DELETE /api/watchlists/{watchlist_id}`
- `GET /api/watchlists/{watchlist_id}`
- `GET /api/watchlists/{watchlist_id}/views`
- `POST /api/watchlists/{watchlist_id}/items`
- `POST /api/watchlists/{watchlist_id}/items/delete`
- `POST /api/watchlists/{watchlist_id}/items/move`
- `POST /api/watchlists/{watchlist_id}/items/copy`
- `POST /api/watchlists/{watchlist_id}/views`
- `PUT /api/watchlists/{watchlist_id}/views/{view_id}`

### 6.3 Instruments

- `GET /api/instruments`
- `GET /api/instruments/resolve`
- `GET /api/instruments/{asset_id}/resolve`
- `GET /api/instruments/library`
- `GET /api/instruments/{asset_id}/summary`
- `GET /api/instruments/{asset_id}/chart`
- `GET /api/instruments/{asset_id}/performance`
- `GET /api/instruments/{asset_id}/risk`
- `GET /api/instruments/{asset_id}/exposure/summary`
- `GET /api/instruments/{asset_id}/exposure/holdings`
- `GET /api/instruments/{asset_id}/ratings`
- `GET /api/instruments/{asset_id}/people`
- `PUT /api/instruments/{asset_id}/people`
- `GET /api/instruments/{asset_id}/strategy`
- `PUT /api/instruments/{asset_id}/strategy`
- `GET /api/instruments/{asset_id}/price`
- `PUT /api/instruments/{asset_id}/price`
- `GET /api/instruments/{asset_id}/documents`
- `PUT /api/instruments/{asset_id}/documents`
- `GET /api/instruments/{asset_id}/research`
- `PUT /api/instruments/{asset_id}/research`
- `GET /api/instruments/{asset_id}/nav-series`
- `PUT /api/instruments/{asset_id}/nav-series`
- `GET /api/instruments/{asset_id}/nav-settings`
- `PUT /api/instruments/{asset_id}/nav-settings`
- `POST /api/instruments/{asset_id}/nav-refresh`
- `POST /api/instruments/manual`

注意：

- `POST /api/instruments/manual` 已废弃，固定返回 `410`
- `PUT /api/instruments/{asset_id}/nav-series` 固定返回 `409`
- `POST /api/instruments/{asset_id}/nav-refresh` 固定返回 `409`

也就是说，watchlist detail 里 canonical NAV history 是只读视图。

### 6.4 Instrument Attributes

- `GET /api/instrument-attributes/definitions`
- `POST /api/instrument-attributes/definitions`
- `GET /api/instrument-attributes/assets/{asset_id}`
- `POST /api/instrument-attributes/assets/{asset_id}`

### 6.5 Field Registry

- `GET /api/field-registry`

### 6.6 Taxonomies

- `GET /api/taxonomies/fund-taxonomy`
- `GET /api/taxonomies/fund-taxonomy/assets/{asset_id}`
- `PUT /api/taxonomies/fund-taxonomy/assets/{asset_id}`

说明：

- 这组接口只服务于 `fund` 的内部分类树
- taxonomy assignment 默认允许为空；系统不会自动推断，正式分类由详情页人工 `PUT` 确认
- `fund_regime` 代表根节点 `公募 / 私募`
- `fund_taxonomy_level_1..6` 从根节点内部的一级分类开始编号

### 6.7 Screener

- `POST /api/screener/query`

### 6.8 Facts

- `GET /api/facts/assets/{asset_id}/nav`
- `POST /api/facts/assets/{asset_id}/nav`
- `GET /api/facts/assets/{asset_id}/holdings/current`
- `POST /api/facts/assets/{asset_id}/holdings`

### 6.9 Monitoring

- `GET /api/monitoring/dashboard`

返回结构当前重点包括：

- `overview`
- `watchlists`
- `needs_attention_assets`
- `missing_label_assets`
- `open_recalc_jobs`

### 6.10 Recalc

- `POST /api/recalc/assets/{asset_id}/performance`
- `POST /api/recalc/assets/{asset_id}/exposure`
- `POST /api/recalc/assets/{asset_id}/ratings`
- `POST /api/recalc/assets/{asset_id}/all`
- `POST /api/recalc/assets/{asset_id}/execute`
- `GET /api/recalc/jobs`
- `GET /api/recalc/jobs/{job_id}`

### 6.11 Copilot Backend Extension

- `POST /api/copilot/watchlists/{watchlist_id}/chat`
- `POST /api/copilot/assets/{asset_id}/chat`

说明：

- 这两个接口当前作为 backend-only extension 保留
- watchlist 前端 UI 默认关闭，不把 Copilot 当成已发布能力

## 7. 当前前端路由

当前主路由是：

- `/watchlists`
- `/watchlists/:watchlistId`
- `/instruments/:assetId`
- `/research`
- `/documents`
- `/monitoring`

说明：

- 详情页 canonical 路由已经收口到 `/instruments/:assetId`
- `watchlist` 来源只作为 query context 透传，不再进入主路径
- watchlist 不再提供独立的 shared registry 页面；共享资产浏览与维护统一放在 `Database Dashboard`
- 旧 `/funds/*` 前端路由已删除

## 8. 当前明确不再维护的东西

下面这些都不再是基线：

- `/api/funds/...` 兼容接口
- `strategy_family / strategy_subtype` 作为 fund 主分类
- Fund Detail 里的 canonical NAV 写入口
- 旧 `backend/app` 代码树

## 9. 开发时的默认判断

如果后续要继续扩这个 app，默认按下面原则判断：

- 新接口一律走 `instrument` 主语
- 产品分类先落 taxonomy，再考虑 research/monitoring labels
- watchlist 前台优先消费 read models，而不是直接扫 facts
- 只在没有更好归属时，才把内容继续放进 fund overlay 的 manual profiles
