# 基金终端 V2 数据模型与 API 基线

状态：Current baseline  
日期：2026-04-15  
适用范围：当前 `/home/shaw/fof` 仓库已经实现的数据分层、核心写入链路和 API 分组

## 1. 这份文档解决什么问题

这里只回答当前工程的三个问题：

1. 数据现在按什么层次组织。
2. 事实是怎么写入、重算并 materialize 到前台的。
3. 当前后端已经暴露了哪些 API。

它不是新的宏观设计稿，也不再讨论“是否要重建”。

## 2. 当前数据分层

当前实现可以概括成 6 层：

1. `Master`
2. `Canonical Facts`
3. `Manual Profiles`
4. `Analytics / Scoring Snapshots`
5. `Read Models`
6. `Application API`

主链路如下：

```text
manual ingest / facts ingest / bootstrap
  -> canonical facts
  -> recalc service
  -> analytics + scoring snapshots
  -> watchlist/detail read models
  -> frontend routes + copilot context
```

## 3. 当前持久化边界

## 3.1 Master

主档由 `backend/app/db/models/master.py` 承载，负责：

- 基金主档身份
- 标识符
- 产品类型 / fund type
- 基础组织关系

这一层只放稳定身份，不直接承载 UI 聚合值。

## 3.2 Canonical Facts

当前核心事实主要集中在：

- `nav_fact`
- `holding_snapshot` / `holding_position`
- 若干分析与评分依赖的事实基础

其中净值口径已经统一为两种：

- `nav`
- `nav_with_dividend`

旧的三口径兼容层已经移除，不再继续维护。

## 3.3 Manual Profiles

当前详情页中一批“可编辑但不是 canonical fact”的内容，走 `manual_profiles`：

- `people`
- `strategy`
- `price`
- `documents`
- `research`

这部分的特点是：

- 直接服务详情页
- 由人工维护
- 不等价于原始事实

共享 market data 的 `source settings / refresh status / manual import` 已经迁到 platform 的 shared data ops，不再属于 watchlist manual profile。

## 3.4 Analytics / Scoring

当前 read model 背后的计算服务主要在：

- `backend/app/services/canonical_recalc.py`
- `backend/app/services/read_models.py`

主要负责：

- performance
- risk
- portfolio summary
- ratings
- watchlist row materialization

## 3.5 Read Models

前台主要读取 read model，而不是直接扫事实表。

当前已经落地的 read model 主要包括：

- watchlist row
- fund summary
- quote/chart
- performance
- risk
- portfolio summary
- portfolio holdings
- ratings

## 4. 当前写入与重算链路

## 4.1 NAV / holdings ingest

watchlist 自身保留的事实写入入口目前只有：

- `POST /api/facts/funds/{fund_id}/holdings`

它会进入 `CanonicalRecalcService`，然后按需要触发：

- 事实入库
- snapshot 更新
- read model 更新

canonical NAV 写入、source settings 和 refresh 已迁到 platform shared data ops：

- `POST /api/instruments/{asset_id}/market-data`
- `POST /api/instruments/{asset_id}/nav-import`
- `PUT /api/instruments/{asset_id}/source-settings`
- `POST /api/instruments/{asset_id}/refresh`

watchlist 的 `GET /api/funds/{fund_id}/nav-series` 会优先通过 shared instrument registry 解析并读取共享 market data；只有共享资产未解析成功或无 NAV 数据时，才回退到本地 facts。

## 4.2 Manual profile upsert

详情页的人工维护内容不经过 canonical fact。

当前更新入口在：

- `PUT /api/funds/{fund_id}/people`
- `PUT /api/funds/{fund_id}/strategy`
- `PUT /api/funds/{fund_id}/price`
- `PUT /api/funds/{fund_id}/documents`
- `PUT /api/funds/{fund_id}/research`

## 4.3 Recalc

当前支持两种重算方式：

- 入队：
  - `POST /api/recalc/funds/{fund_id}/performance`
  - `POST /api/recalc/funds/{fund_id}/portfolio`
  - `POST /api/recalc/funds/{fund_id}/ratings`
  - `POST /api/recalc/funds/{fund_id}/all`
- 直接执行：
  - `POST /api/recalc/funds/{fund_id}/execute`

并且有：

- `GET /api/recalc/jobs`
- `GET /api/recalc/jobs/{job_id}`

## 4.4 Watchlist materialization

Watchlist 的最终列表不直接由前端拼。

当前链路是：

1. watchlist / items / view 落库
2. watchlist row read model materialize
3. screener query 按 selected fields / sort / group by 返回前台表格

## 5. 当前 API 分组

## 5.1 Health

- `GET /api/health`

## 5.2 Watchlists

- `GET /api/watchlists`
- `POST /api/watchlists`
- `GET /api/watchlists/{watchlist_id}`
- `GET /api/watchlists/{watchlist_id}/views`
- `POST /api/watchlists/{watchlist_id}/items`
- `POST /api/watchlists/{watchlist_id}/items/delete`
- `POST /api/watchlists/{watchlist_id}/views`
- `PUT /api/watchlists/{watchlist_id}/views/{view_id}`

## 5.3 Screener

- `POST /api/screener/query`

## 5.4 Field Registry / Instrument Attributes

- `GET /api/field-registry`
- `GET /api/instrument-attributes/definitions`
- `POST /api/instrument-attributes/definitions`
- `GET /api/instrument-attributes/funds/{fund_id}`
- `POST /api/instrument-attributes/funds/{fund_id}`

## 5.5 Funds

- `POST /api/funds/manual` 已废弃，固定返回 `410`；资产主档需先在 Platform `Instruments` 创建
- `GET /api/funds/library`
- `GET /api/funds/{fund_id}/summary`
- `GET /api/funds/{fund_id}/chart`
- `GET /api/funds/{fund_id}/performance`
- `GET /api/funds/{fund_id}/risk`
- `GET /api/funds/{fund_id}/portfolio/summary`
- `GET /api/funds/{fund_id}/portfolio/holdings`
- `GET /api/funds/{fund_id}/ratings`
- `GET /api/funds/{fund_id}/people`
- `PUT /api/funds/{fund_id}/people`
- `GET /api/funds/{fund_id}/strategy`
- `PUT /api/funds/{fund_id}/strategy`
- `GET /api/funds/{fund_id}/price`
- `PUT /api/funds/{fund_id}/price`
- `GET /api/funds/{fund_id}/documents`
- `PUT /api/funds/{fund_id}/documents`
- `GET /api/funds/{fund_id}/research`
- `PUT /api/funds/{fund_id}/research`
- `GET /api/funds/{fund_id}/nav-series`

## 5.6 Facts

- `GET /api/facts/funds/{fund_id}/nav`
- `GET /api/facts/funds/{fund_id}/holdings/current`
- `POST /api/facts/funds/{fund_id}/holdings`

## 5.7 Copilot

- `POST /api/copilot/watchlists/{watchlist_id}/chat`
- `POST /api/copilot/funds/{fund_id}/chat`

## 6. 当前前端消费方式

前端路由现在主要包括：

- `/watchlists`
- `/funds/:fundId`
- `/funds`
- `/research`
- `/documents`
- `/monitoring`

其中真正已经形成主工作面的，是：

- Watchlists
- Fund Detail

其余几个一级路由当前仍是占位页或待继续展开。

## 7. 已知边界

当前文档不再假设下面这些能力已经完整落地：

- email sync
- OCR / extraction workflow
- documents workspace
- monitoring cockpit
- funds library page
- 异步 worker 消费队列

这些是下一阶段推进项，不是当前基线。

#### `fee_schedule_fact`

- `fee_schedule_fact_id`
- `fund_id`
- `as_of_date`
- `expense_ratio`
- `management_fee`
- `performance_fee`
- `subscription_fee`
- `redemption_fee`
- `fee_text`
- `source_record_id`

#### `terms_fact`

- `terms_fact_id`
- `fund_id`
- `as_of_date`
- `dealing_frequency`
- `subscription_window`
- `redemption_notice`
- `lockup_period`
- `gate_terms`
- `liquidity_terms_text`

### 4.3.3 Exposure 事实

#### `portfolio_statement`

原始估值表或持仓表的采用对象：

- `portfolio_statement_id`
- `fund_id`
- `statement_type`
  - `valuation_sheet`
  - `holdings_report`
  - `monthly_portfolio`
- `as_of_date`
- `source_record_id`
- `document_asset_id`
- `coverage_status`
- `parsing_status`
- `adopted_at`

#### `holding_snapshot`

- `holding_snapshot_id`
- `fund_id`
- `portfolio_statement_id`
- `as_of_date`
- `currency`
- `gross_market_value`
- `net_market_value`
- `position_count`
- `coverage_ratio`
- `is_current`

#### `holding_position`

- `holding_position_id`
- `holding_snapshot_id`
- `security_id`
- `security_name`
- `security_type`
- `asset_class`
- `sector`
- `country`
- `currency`
- `credit_rating`
- `duration_bucket`
- `maturity_bucket`
- `coupon_rate`
- `yield_to_maturity`
- `market_value`
- `portfolio_weight`
- `quantity`
- `price`
- `maturity_date`

### 4.3.4 事件类事实

- `people_event`
- `firm_event`
- `operational_event`

这些事件为 people / strategy / monitoring / score 提供输入。

## 4.4 Analytics Snapshot Domain

### 4.4.1 通用字段

所有 snapshot 建议统一拥有：

- `snapshot_id`
- `fund_id`
- `as_of_date`
- `source_cutoff_at`
- `methodology_version`
- `input_hash`
- `calculated_at`
- `superseded_at`
- `is_current`

### 4.4.2 `performance_snapshot`

主要字段：

- `return_ytd`
- `return_1w`
- `return_1m`
- `return_3m`
- `return_6m`
- `return_1y`
- `return_3y_annualized`
- `return_5y_annualized`
- `return_10y_annualized`
- `max_drawdown`
- `calmar`
- `annualized_return`

### 4.4.3 `risk_snapshot`

- `volatility`
- `downside_volatility`
- `sharpe_ratio`
- `sortino_ratio`
- `alpha`
- `beta`
- `r_squared`
- `up_capture`
- `down_capture`
- `tracking_error`
- `information_ratio`

### 4.4.4 `ranking_snapshot`

- `ranking_group_code`
- `window_code`
- `metric_code`
- `rank_value`
- `percentile`
- `sample_count`
- `quartile`

### 4.4.5 `factor_snapshot`

保留当前系统已有的行为分析思路：

- `style_bucket`
- `drift_score`
- `drift_state`
- `beta_to_peer_group`
- `correlation_to_peer_group`
- `up_capture_to_peer_group`
- `down_capture_to_peer_group`

### 4.4.6 `exposure_analytics_snapshot`

这是 Exposure 页面主供数对象。

聚合字段建议：

- `asset_allocation_json`
- `sector_allocation_json`
- `country_allocation_json`
- `currency_allocation_json`
- `credit_rating_allocation_json`
- `duration_bucket_json`
- `maturity_bucket_json`
- `yield_bucket_json`
- `top10_concentration`
- `holding_count`
- `bond_count`
- `equity_count`
- `other_count`
- `cash_ratio`
- `leverage_ratio`
- `weighted_duration`
- `weighted_maturity`
- `avg_credit_rating`
- `reported_turnover`
- `style_box_code`

### 4.4.7 `style_drift_event`

保留事件化对象：

- `style_drift_event_id`
- `fund_id`
- `factor_snapshot_id`
- `event_date`
- `severity_level`
- `drift_state`
- `summary`
- `detail`
- `event_status`

## 4.5 Scoring Domain

### 4.5.1 `score_methodology`

- `score_methodology_id`
- `name`
- `version`
- `status`
- `definition_json`
- `effective_from`

### 4.5.2 `score_run`

- `score_run_id`
- `fund_id`
- `score_methodology_id`
- `as_of_date`
- `trigger_type`
- `trigger_ref_type`
- `trigger_ref_id`
- `run_status`
- `calculated_at`

### 4.5.3 `score_dimension_result`

- `score_dimension_result_id`
- `score_run_id`
- `dimension_code`
- `raw_score`
- `weighted_score`
- `confidence_score`
- `evidence_strength`
- `summary`

### 4.5.4 `score_override`

- `score_override_id`
- `score_run_id`
- `dimension_code`
- `override_score`
- `override_reason`
- `approved_by`
- `approved_at`

### 4.5.5 `fund_score_snapshot`

前台直接消费的评分快照：

- `overall_score`
- `overall_rating`
- `analyst_stance`
- `people_score`
- `process_score`
- `exposure_score`
- `risk_score`
- `price_score`
- `operations_score`
- `fit_score`
- `confidence_score`

## 4.6 Read Models

## 4.6.1 `watchlist_row_read_model`

主页面最重要的读取模型。

建议字段：

- `watchlist_id`
- `fund_id`
- `fund_name`
- `share_class`
- `ticker_or_isin`
- `management_firm_name`
- `category_name`
- `overall_rating`
- `analyst_stance`
- `aum`
- `return_ytd`
- `return_1y`
- `return_3y`
- `return_5y`
- `max_drawdown`
- `volatility`
- `sharpe_ratio`
- `duration`
- `avg_credit_rating`
- `attributes_json`
- `exposure_updated_at`
- `last_nav_date`
- `last_recalculated_at`
- `data_freshness_status`
- `staleness_reason`

### 4.6.2 `fund_summary_read_model`

给 `Summary` tab 用。

### 4.6.3 `fund_performance_read_model`

给 `Performance` tab 用。

### 4.6.4 `fund_risk_read_model`

给 `Risk` tab 用。

### 4.6.5 `fund_exposure_read_model`

给 `Exposure` tab 用。

### 4.6.6 `fund_rating_read_model`

给 `Ratings / Summary` 用。

## 5. 重算链设计

## 5.1 基本原则

- 导入不直接改前台展示值
- 采用 canonical fact 后自动触发重算
- 重算结果落 snapshot
- snapshot 更新后触发 read model materialize

## 5.2 NAV 更新链

```text
nav_fact adopted
  -> performance_recalc
  -> risk_recalc
  -> ranking_recalc
  -> factor_recalc
  -> score_recalc
  -> watchlist_row_materialize
  -> fund_summary/performance/risk materialize
```

### 5.2.1 触发结果

NAV 更新后，以下内容必须同步变化：

- watchlist returns columns
- performance tab
- risk tab
- category rank / percentile
- style drift / factor metrics
- 依赖业绩和风险的评分

## 5.3 估值表 / 持仓更新链

```text
portfolio_statement adopted
  -> holding_snapshot build
  -> holdings normalization
  -> exposure_recalc
  -> exposure analytics snapshot
  -> risk partial recalc
  -> score partial recalc
  -> watchlist_row_materialize
  -> fund_exposure materialize
```

### 5.3.1 触发结果

必须同步变化：

- portfolio tab 图表和表格
- watchlist 中 duration / credit / concentration 等衍生列
- 风险和 portfolio 相关评分
- monitoring 中的 portfolio drift / concentration warning

## 5.4 费率 / 条款更新链

```text
fee_schedule_fact or terms_fact adopted
  -> price_recalc
  -> score_recalc
  -> watchlist materialize
  -> fund_price materialize
```

## 5.5 People / Firm 事件更新链

```text
people_event or firm_event adopted
  -> people summary recalc
  -> score_recalc
  -> monitoring evaluation
  -> summary materialize
```

## 5.6 重算任务表

建议新增统一任务表：

### `recalc_job`

- `recalc_job_id`
- `job_type`
- `fund_id`
- `trigger_type`
- `trigger_ref_type`
- `trigger_ref_id`
- `job_status`
- `priority`
- `dedupe_key`
- `payload_json`
- `enqueued_at`
- `started_at`
- `finished_at`
- `error_message`

### 5.6.1 dedupe_key 规则

例如：

- `performance:{fund_id}:{as_of_date}`
- `portfolio:{fund_id}:{holding_snapshot_id}`
- `score:{fund_id}:{methodology_version}:{as_of_date}`

用于避免重复重算。

## 5.7 idempotency 规则

每个重算任务都必须做到：

- 相同输入重复执行，结果一致
- 新 snapshot 生成后，旧 snapshot 自动 supersede
- read model materialization 可以重复运行，不产生脏状态

## 6. 数据新鲜度与失败可见性

必须把 freshness 做成系统级能力。

## 6.1 统一 freshness 字段

建议所有 read model 都带：

- `data_freshness_status`
  - `fresh`
  - `stale`
  - `pending_recalc`
  - `partial`
  - `unavailable`
- `last_fact_update_at`
- `last_recalculated_at`
- `last_successful_snapshot_at`
- `staleness_reason`

## 6.2 页面表现

Watchlist 和 detail 需要显式展示：

- 哪些字段已更新
- 哪些字段待重算
- 哪些字段因为上游缺失不可用

## 7. API 草案

## 7.1 Watchlists

### `GET /api/watchlists`

返回：

- watchlist id
- name
- item count
- owner
- default view id

### `POST /api/watchlists`

请求：

```json
{
  "name": "EM Bond Coverage",
  "description": "Emerging market bond funds under active coverage"
}
```

### `GET /api/watchlists/{watchlistId}`

返回：

- metadata
- available views
- default filters summary

### `POST /api/watchlists/{watchlistId}/items`

请求：

```json
{
  "fund_ids": ["fund_1", "fund_2"]
}
```

## 7.2 Views

### `GET /api/watchlists/{watchlistId}/views`

返回当前 watchlist 下的系统 view 和自定义 view。

### `POST /api/watchlists/{watchlistId}/views`

请求：

```json
{
  "name": "Risk Focus",
  "description": "High-risk bond funds review view",
  "default_group_by": "morningstar_category",
  "default_sort": [{"field": "overall_score", "direction": "desc"}],
  "default_filters": {"asset_class": ["fixed_income"]},
  "default_advanced_filters": {
    "type": "group",
    "logic": "and",
    "conditions": [
      {"type": "rule", "field": "attr.coverage_status", "operator": "in", "value": ["Invested", "Focus"]},
      {"type": "rule", "field": "overall_rating", "operator": "gte", "value": 4}
    ]
  },
  "columns": [
    {"field_key": "fund_name", "display_order": 1, "width": 260},
    {"field_key": "attr.coverage_status", "display_order": 2, "width": 140},
    {"field_key": "overall_rating", "display_order": 3, "width": 120}
  ]
}
```

## 7.3 Field Registry

### `GET /api/field-registry`

支持参数：

- `category`
- `product_type`
- `search`

返回字段定义、可用性、说明、默认宽度。

### `GET /api/instrument-attributes/definitions`

返回当前系统支持的自定义 instrument 属性定义。

### `POST /api/instrument-attributes/definitions`

允许动态新增 instrument 属性定义，并自动进入 field registry。

## 7.4 Screener Query

### `POST /api/screener/query`

这是 watchlist 主表格的核心接口。

请求：

```json
{
  "watchlist_id": "wl_1",
  "view_id": "view_overview",
  "selected_fields": [
    "fund_name",
    "overall_rating",
    "category_name",
    "aum",
    "return_1y",
    "max_drawdown"
  ],
  "filters": {
    "asset_class": ["fixed_income"],
    "data_freshness_status": ["fresh"]
  },
  "advanced_filters": {
    "type": "group",
    "logic": "and",
    "conditions": [
      {"type": "rule", "field": "attr.coverage_status", "operator": "in", "value": ["Invested", "Focus"]},
      {
        "type": "group",
        "logic": "or",
        "conditions": [
          {"type": "rule", "field": "overall_rating", "operator": "gte", "value": 4},
          {"type": "rule", "field": "analyst_stance", "operator": "eq", "value": "High Conviction"}
        ]
      }
    ]
  },
  "sort": [
    {"field": "overall_score", "direction": "desc"}
  ],
  "group_by": "attr.coverage_status",
  "pagination": {
    "page": 1,
    "page_size": 50
  }
}
```

返回：

- rows
- groups
- total rows
- stale row count
- snapshot metadata

其中 `selected_fields / filters / group_by` 必须都能使用：

- 系统字段 key
- `attr.xxx` 形式的 instrument 自定义属性字段 key

## 7.5 Fund Detail

### `GET /api/funds/{fundId}/summary`

返回：

- header
- current rating
- current view summary
- key stats
- freshness
- quick monitoring items

### `GET /api/funds/{fundId}/chart`

参数：

- date range
- frequency
- compare targets
- series type

### `GET /api/funds/{fundId}/performance`

返回：

- growth chart series
- annual returns table
- trailing returns table
- quartile / percentile
- category / benchmark metadata

### `GET /api/funds/{fundId}/risk`

返回：

- risk overview
- scatter points
- risk metrics table
- drawdown series
- volatility/capture data

### `GET /api/funds/{fundId}/portfolio/summary`

返回：

- allocation blocks
- style box
- liquidity / leverage
- valuation statistics
- exposure charts
- holdings summary

### `GET /api/funds/{fundId}/portfolio/holdings`

参数：

- holding type
- page
- page_size
- sort

### `GET /api/funds/{fundId}/ratings`

返回：

- overall rating
- stance
- dimension scores
- methodology version
- override info

## 7.6 Documents / Imports

建议沿用旧系统的强项，但以新接口整理：

- `POST /api/imports/nav`
- `POST /api/imports/valuation-statements`
- `POST /api/imports/factsheets`
- `POST /api/imports/email-sync`
- `GET /api/imports/tasks`
- `POST /api/imports/tasks/{taskId}/retry`
- `GET /api/documents`
- `GET /api/documents/{documentId}`
- `POST /api/documents/{documentId}/extract`
- `POST /api/documents/{documentId}/adopt`

## 7.7 Recalculation / Materialization

- `POST /api/recalc/funds/{fundId}/performance`
- `POST /api/recalc/funds/{fundId}/portfolio`
- `POST /api/recalc/funds/{fundId}/ratings`
- `POST /api/recalc/funds/{fundId}/all`
- `GET /api/recalc/jobs`
- `GET /api/recalc/jobs/{jobId}`

## 8. 与当前项目的迁移映射

## 8.1 可直接参考或迁移

### 8.1.1 几乎可直接迁移

- `source_*`
- `document_asset`
- `document_extraction`
- `import_task`
- email sync
- OCR / extraction
- nav import

对应现有实现参考：

- [backend/app/services/import_task_service.py](/home/shaw/archive/fof-research-os_20260413_003744/project/backend/app/services/import_task_service.py)
- [backend/app/services/email_sync_service.py](/home/shaw/archive/fof-research-os_20260413_003744/project/backend/app/services/email_sync_service.py)
- [backend/app/services/evidence_extraction_service.py](/home/shaw/archive/fof-research-os_20260413_003744/project/backend/app/services/evidence_extraction_service.py)

### 8.1.2 可迁概念，不建议原样迁接口

- `fund`
- `fund_profile`
- `analysis_snapshot`
- `factor_snapshot`
- `style_drift_event`

## 8.2 不建议原样迁移

- 当前 screener API
- 当前 front-end localStorage view 体系
- 当前 fund detail 聚合页接口
- 当前 workflow-first tab 路由

## 8.3 旧研究域如何接回

建议在 Phase F 再接：

- `research_note`
- `research_artifact`
- `current_research_view`
- `DD / ODD`
- `monitor_item`
- `decision journal`

接回方式：

- 保留旧领域模型
- 新前端只在 `Research` tab 内调用
- 不再让其决定主详情页骨架

## 9. Phase 化落地建议

## 9.1 Phase B：Watchlist MVP

最小表：

- `watchlist`
- `watchlist_item`
- `watchlist_view`
- `watchlist_view_column`
- `field_registry`
- `watchlist_row_read_model`
- `performance_snapshot`
- `fund_score_snapshot`

## 9.2 Phase C：Detail V1

新增：

- `fund_summary_read_model`
- `fund_performance_read_model`
- `fund_risk_read_model`

## 9.3 Phase D：Exposure

新增：

- `portfolio_statement`
- `holding_snapshot`
- `holding_position`
- `exposure_analytics_snapshot`
- `fund_exposure_read_model`

## 9.4 Phase E：Scoring

新增：

- `score_methodology`
- `score_run`
- `score_dimension_result`
- `score_override`
- `fund_rating_read_model`

## 10. 开工建议

如果下一步进入正式实施，建议按这个顺序：

1. 先建 PostgreSQL schema baseline
2. 先把 `field_registry + watchlist + view` 跑通
3. 再建 `performance_snapshot + fund_score_snapshot + watchlist_row_read_model`
4. 然后做 watchlist 主表格
5. 再做 fund summary / performance / risk
6. 最后接 portfolio 估值表分析

## 11. 下一步

本文档已经把后端骨架和 API 定死了。

如果继续推进，下一步最合适的是：

1. 直接为 `v2` 生成数据库 ER 草图和首批迁移草案
2. 或者直接开始搭新项目骨架

如果你要我继续，我建议下一步直接做：

`v2 PostgreSQL schema 初稿 + FastAPI routes skeleton + React route skeleton`
