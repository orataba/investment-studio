# Watchlist 数据模型与 API 基线

适用范围：当前 `apps/watchlist` 已经实现的数据分层、关键表语义和后端 API 边界

## 1. 这份文档解决什么问题

这里只回答当前工程的四个问题：

1. 数据现在按什么层次组织
2. type-specific 产品框架如何入库和读取
3. facts / manual profile / read model / recalc 怎样串起来
4. 后端当前到底暴露了哪些 API

本文只定义当前 `instrument` API 和数据合同。

研究工作台在 `watchlist` schema 内维护 `research_topic`、`research_entry`、`risk_review_rule` 和 `risk_case`。专题关联标的或组合；entry 保存人工证据、对话、输入快照、工具证据与回答。`risk_review_rule` 保存回撤和各周期跌幅复核线及初始校准；`risk_case` 分别记录客观触发状态、人工跟进、复核日期和历史。

- `/api/research/catalogue`、`/connections` 提供登记标的与外部证据连接状态。
- `/api/research/topics` 管理持续专题；专题下的 `/entries`、`/files`、`/analysis` 保存材料或发起助手运行。
- `/api/research/runs/{run_id}/context`、`/tools` 只向绑定运行的受限工具提供证据；运行失败保留输入，服务重启将未完成运行标为失败。
- `/api/risk` 只读当前事项；`/api/risk/rules/{instrument_id}`、`/api/risk/cases` 和单事项更新接口管理复核与跟进。

原始上传文件位于外部 document storage，数据库保存引用和提取文字。助手没有行情或交易写入工具；其回答是待复核研究内容。Portfolio 的标的风险路由代理同一组 Watchlist 事项 API，组合账本与风险模型保持独立。完整路由见 [workbench.py](../backend/watchlist_app/api/routes/workbench.py)，数值口径见 [Return Series Contract](./RETURN_SERIES_CONTRACT.md)。

## 2. 当前代码入口

- 后端包：`apps/watchlist/backend/watchlist_app`
- API router：`apps/watchlist/backend/watchlist_app/api/router.py`
- 数据模型：`apps/watchlist/backend/watchlist_app/db/models`
- 迁移目录：`apps/watchlist/backend/alembic`

后端 API 统一以 `instrument` 为主语。

## 3. 当前数据分层

当前实现可以概括成 7 层：

1. `Shared Instruments`
2. `Watchlist Ownership`
3. `Instrument Product Framework`
4. `Local Evidence and Holdings`
5. `Manual Profiles`
6. `Read Models`
7. `Application API / backend extensions`

主链路如下：

```text
shared instruments / local evidence / holdings ingest
  -> Instrument Data facts + Watchlist-local profiles and holdings
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
  管 Watchlist-local、按 instrument type 隔离的分类树和当前叶子赋值；Instrument Data 不保存 taxonomy
- attribute tables
  管 `fund_vehicle`、研究标签、监控评估等非树形字段
- 基金 peer 口径
  由 taxonomy assignment / peer path 推导，不作为独立 category 事实维护

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

它是完整的产品框架定义表，不是普通 tag options 表。

### 4.3 Local Evidence and Holdings

Watchlist 只直接维护自己的持仓快照。历史 `nav_fact` 只保留为只读审计证据，不再参与行情、收益、风险或图表计算；canonical NAV 的写入和修订属于 后台 CLI。

- historical NAV audit rows
- holdings snapshots / positions

相关路由统一到 instrument 主语：

- `GET /api/facts/instruments/{instrument_id}/nav`
- `GET /api/facts/instruments/{instrument_id}/holdings/current`
- `POST /api/facts/instruments/{instrument_id}/holdings`

### 4.4 Manual Profiles

详情页里那些“可编辑但不是 canonical fact”的运营资料，走 manual profiles：

- `people`
- `strategy`
- `price`
- `documents`

这些内容服务于 detail overlay，不等价于原始事实。

投资研究已经成为独立领域：

- `instrument_research_profile` 保存当前投资论点、风险/反证、人物分析、组合角色、复核日期与人工 rating；
- `instrument_research_note` 逐条保存证据、访谈、事件、决策与复盘，并记录作者、来源、人物、跟进日期和审计时间；
- `GET / PUT /api/instruments/{instrument_id}/research` 读取或更新当前判断；
- `POST / PUT / DELETE /api/instruments/{instrument_id}/research/notes[...]` 操作单条研究记录。

研究记录只通过独立的 research profile、note 与 revision 合同读写。

### 4.5 Read Models

前台主要读取 read models，而不是直接扫事实表。

当前已经落地的 read model 主要包括：

- `watchlist_row_read_model`
- summary / chart / performance / risk / exposure read models；人工 rating 保存在 research profile，不是计算 read model
- instrument summary payload

Performance snapshot 的 `return_1w / return_1m / return_3m / return_6m / return_mtd / return_ytd / return_1y` 必须完整投影到 `watchlist_row_read_model`、field registry、筛选/排序和导出。新增窗口时不能只改计算 snapshot 而遗漏 migration、repository 或 serializer。窗口边界及 return semantics 见 [RETURN_SERIES_CONTRACT.md](./RETURN_SERIES_CONTRACT.md)。

每个 `watchlist_row_read_model.last_nav_date` 是该 instrument 的 metric as-of，不是名单共用日期。Screener 即使未选日期列也必须在每行返回 `metric_as_of_date`，并在 `snapshot_metadata` 返回行终点的 min/max、是否混合及缺失数量。Peer snapshot 只比较相同 as-of；路径风险还必须记录 Instrument Data frequency/calendar 缺点状态。

### 4.6 Recalc Jobs

重算已经 durable 化，不再依赖临时线程：

- `recalc_job`

stale read repair 也只会写 job，不会直接在 Web 请求里补算；后台 worker 会异步消费这些 queued jobs。
Instrument Data 通知只是低延迟提示，不是正确性边界；worker 会分页对账源版本与本地 materialization cutoff，并通过同一条 durable、per-instrument 串行队列修复漏通知。

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

- `instrument_taxonomy`
- `research_framework`
- `monitoring_assessment`

这保证了 watchlist 的列选择和 filter 能按当前名单全部资产都适用的字段工作；`Group By` 只使用结构化分类与工作流字段，不暴露基金专属定性判断。

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
- `POST /api/instruments/{instrument_id}/resolve`
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
- `GET /api/instruments/{instrument_id}/nav-settings`
- `PUT /api/instruments/{instrument_id}/nav-settings`

注意：

- Watchlist 不提供 instrument 创建、canonical NAV 写入或行情刷新接口；这些操作通过 `investment-studio data` CLI 维护

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

- 这组接口服务 `public_fund / private_fund / etf / equity / index` 的 Watchlist-local 分类，不把 taxonomy 写回共享 Instrument Data
- 节点带 `instrument_type`，assignment 必须与资产类型一致；不同类型之间不能交叉赋值
- 公募、私募、ETF 和指数 assignment 默认允许为空，正式分类由详情页人工 `PUT` 确认
- 股票分类由 Instrument Data 的 canonical `exchange_code` 映射到市场/交易所节点，接口拒绝人工改写
- 派生字段统一为 `instrument_taxonomy_level_1..7 / instrument_taxonomy_leaf / instrument_taxonomy_path`

### 6.7 Screener

- `POST /api/screener/query`

### 6.8 Facts

- `GET /api/facts/instruments/{instrument_id}/nav`
- `GET /api/facts/instruments/{instrument_id}/holdings/current`
- `POST /api/facts/instruments/{instrument_id}/holdings`

### 6.9 Monitoring

- `GET /api/monitoring/dashboard`

返回结构当前重点包括：

- `overview`
- `watchlists`
- `needs_attention_instruments`
- `missing_required_metadata_instruments`
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
- `watchlist` 来源只作为 query context 透传；canonical 详情路径不依赖来源名单
- 添加弹窗只选择已登记的共享资产；供应商目录搜索、新资产登记及数据维护通过后台 CLI 执行

## 8. 开发时的默认判断

如果后续要继续扩这个 app，默认按下面原则判断：

- 新接口一律走 `instrument` 主语
- 产品分类先落 taxonomy，再考虑 research/monitoring labels
- watchlist 前台优先消费 read models，而不是直接扫 facts
- 只在没有更好归属时，才把内容继续放进公募/私募详情共享的 manual profiles
