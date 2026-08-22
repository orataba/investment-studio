# Watchlist 数据模型与 API 基线

适用范围：当前 `apps/watchlist` 已经实现的数据分层、关键表语义和后端 API 边界

## 1. 这份文档解决什么问题

这里只回答当前工程的四个问题：

1. 数据现在按什么层次组织
2. type-specific 产品框架如何入库和读取
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
- 复制 watchlist 时会按相同规则规范化源 custom view id

### 4.2 Instrument Product Framework

这一层负责 `public_fund / private_fund / etf / equity / index` 各自的分类，以及适用的研究标签和监控评估：

- `instrument_taxonomy_node`
- `instrument_taxonomy_assignment`
- `instrument_attribute_definition`
- `instrument_attribute_value`
- `field_category`
- `field_registry_record`

其中：

- taxonomy tables
  管 Watchlist-local、按 instrument type 隔离的分类树和当前叶子赋值；Registry 不保存 taxonomy
- attribute tables
  管 `fund_vehicle`、研究标签、监控评估等非树形字段
- 基金 peer 口径
  由 taxonomy assignment / peer path 推导，不再通过 legacy category 字段维护

`instrument_attribute_definition` 当前关键字段：

- `attribute_key`
- `domain_code`
- `group_code`
- `display_order`
- `options_json`
- `instrument_scope_json`
- `applicability_json`
- `rubric_json`
- `required_for_monitoring`

它已经不再只是“tag options 表”，而是完整的产品框架定义表。

### 4.3 Canonical Facts

当前 watchlist app 内仍然直接维护的 canonical facts 主要包括：

- NAV facts
- holdings snapshots / positions

facts 路由已经统一到 instrument 主语：

- `GET /api/facts/instruments/{instrument_id}/nav`
- `POST /api/facts/instruments/{instrument_id}/nav`
- `GET /api/facts/instruments/{instrument_id}/holdings/current`
- `POST /api/facts/instruments/{instrument_id}/holdings`

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
- summary / chart / performance / risk / exposure read models；人工 rating 保存在 research profile，不是计算 read model
- instrument summary payload

Performance snapshot 的 `return_1w / return_1m / return_3m / return_6m / return_mtd / return_ytd / return_1y` 必须完整投影到 `watchlist_row_read_model`、field registry、筛选/排序和导出。新增窗口时不能只改计算 snapshot 而遗漏 migration、repository 或 serializer。窗口边界及 return semantics 见 [RETURN_SERIES_CONTRACT.md](./RETURN_SERIES_CONTRACT.md)。

每个 `watchlist_row_read_model.last_nav_date` 是该 instrument 的 metric as-of，不是名单共用日期。Screener 即使未选日期列也必须在每行返回 `metric_as_of_date`，并在 `snapshot_metadata` 返回行终点的 min/max、是否混合及缺失数量。Peer snapshot 只比较相同 as-of；路径风险还必须记录 Registry frequency/calendar 缺点状态。

### 4.6 Recalc Jobs

重算已经 durable 化，不再依赖临时线程：

- `recalc_job`

stale read repair 也只会写 job，不会直接在 Web 请求里补算；后台 worker 会异步消费这些 queued jobs。
Registry 通知只是低延迟提示，不是正确性边界；worker 会分页对账源版本与本地 materialization cutoff，并通过同一条 durable、per-instrument 串行队列修复漏通知。

## 5. 产品框架如何落地

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

Monitoring 页面不再硬编码一张“所有资产或所有基金必填 tags”清单。

当前逻辑是：

1. 读取 `instrument_attribute_definition`
2. 只看 `required_for_monitoring = true`
3. 再按 `instrument_scope_json` 与 `applicability_json` 判断当前资产是否适用
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
- `GET /api/instruments/{instrument_id}/resolve`
- `GET /api/instruments/library`
- `GET /api/instruments/{instrument_id}/summary`
- `GET /api/instruments/{instrument_id}/chart`
- `GET /api/instruments/{instrument_id}/performance`
- `GET /api/instruments/{instrument_id}/risk`
- `GET /api/instruments/{instrument_id}/exposure/summary`
- `GET /api/instruments/{instrument_id}/exposure/holdings`
- `GET /api/instruments/{instrument_id}/people`
- `PUT /api/instruments/{instrument_id}/people`
- `GET /api/instruments/{instrument_id}/strategy`
- `PUT /api/instruments/{instrument_id}/strategy`
- `GET /api/instruments/{instrument_id}/price`
- `PUT /api/instruments/{instrument_id}/price`
- `GET /api/instruments/{instrument_id}/documents`
- `PUT /api/instruments/{instrument_id}/documents`
- `POST /api/instruments/{instrument_id}/documents/upload`
- `GET /api/instruments/{instrument_id}/documents/files/{stored_file_name}`
- `GET /api/instruments/{instrument_id}/research`
- `PUT /api/instruments/{instrument_id}/research`
- `GET /api/instruments/{instrument_id}/nav-series`
- `PUT /api/instruments/{instrument_id}/nav-series`
- `GET /api/instruments/{instrument_id}/nav-settings`
- `PUT /api/instruments/{instrument_id}/nav-settings`
- `POST /api/instruments/{instrument_id}/nav-refresh`
- `POST /api/instruments/manual`

注意：

- `POST /api/instruments/manual` 已废弃，固定返回 `410`
- `PUT /api/instruments/{instrument_id}/nav-series` 固定返回 `409`
- `POST /api/instruments/{instrument_id}/nav-refresh` 固定返回 `409`

也就是说，watchlist detail 里 canonical quote/NAV history 是只读视图；派生 payload 需要保留实际 `metric_family / quote_basis / role`，避免场内 ETF 或指数的 `close` 被误标成 NAV。

### 6.4 Instrument Attributes

- `GET /api/instrument-attributes/definitions`
- `POST /api/instrument-attributes/definitions`
- `GET /api/instrument-attributes/instruments/{instrument_id}`
- `POST /api/instrument-attributes/instruments/{instrument_id}`

### 6.5 Field Registry

- `GET /api/field-registry`

### 6.6 Taxonomies

- `GET /api/taxonomies/instrument-taxonomy`
- `GET /api/taxonomies/instrument-taxonomy/instruments/{instrument_id}`
- `PUT /api/taxonomies/instrument-taxonomy/instruments/{instrument_id}`

说明：

- 这组接口服务 `public_fund / private_fund / etf / equity / index` 的 Watchlist-local 分类，不把 taxonomy 写回共享 Registry
- 节点带 `instrument_type`，assignment 必须与资产类型一致；不同类型之间不能交叉赋值
- 公募、私募、ETF 和指数 assignment 默认允许为空，正式分类由详情页人工 `PUT` 确认
- 股票分类由 Registry 的 canonical `exchange_code` 映射到市场/交易所节点，接口拒绝人工改写
- 派生字段统一为 `instrument_taxonomy_level_1..7 / instrument_taxonomy_leaf / instrument_taxonomy_path`

### 6.7 Screener

- `POST /api/screener/query`

### 6.8 Facts

- `GET /api/facts/instruments/{instrument_id}/nav`
- `POST /api/facts/instruments/{instrument_id}/nav`
- `GET /api/facts/instruments/{instrument_id}/holdings/current`
- `POST /api/facts/instruments/{instrument_id}/holdings`

### 6.9 Monitoring

- `GET /api/monitoring/dashboard`

返回结构当前重点包括：

- `overview`
- `watchlists`
- `needs_attention_instruments`
- `missing_label_instruments`
- `open_recalc_jobs`

### 6.10 Recalc

- `POST /api/recalc/instruments/{instrument_id}/performance`
- `POST /api/recalc/instruments/{instrument_id}/exposure`
- `POST /api/recalc/instruments/{instrument_id}/all`
- `POST /api/recalc/instruments/{instrument_id}/execute`
- `GET /api/recalc/jobs`
- `GET /api/recalc/jobs/{job_id}`

## 7. 当前前端路由

当前主路由是：

- `/watchlists`
- `/watchlists/:watchlistId`
- `/instruments/:instrumentId`
- `/monitoring`

说明：

- 详情页 canonical 路由已经收口到 `/instruments/:instrumentId`
- `watchlist` 来源只作为 query context 透传，不再进入主路径
- watchlist 不再提供独立的 shared registry 页面；公募、私募、ETF 和指数的共享资产浏览与维护统一放在 `Database Dashboard`，股票从添加弹窗搜索本地 FMP 目录并按需 materialize
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
- 只在没有更好归属时，才把内容继续放进公募/私募详情共享的 manual profiles
