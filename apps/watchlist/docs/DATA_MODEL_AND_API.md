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

迁移 `20260920_0060` 为 PostgreSQL `research_entry` 增加基于留存 `context_json.instrument_ids` 的 GIN 表达式索引。读取先按该次运行的原始标的范围筛选，再解析命中记录的研究字段；不能用后来可变的 `research_topic.instrument_ids` 替代历史范围。索引通过同一不可变数据库函数提取文本数组，由 PostgreSQL 随每次写入维护；它不是第二份研究事实或跨请求缓存。原始 context、来源正文、作者及权限判断保持原样。SQL 工作副本仅为处理 PostgreSQL JSON 中保留的 NUL 转义而转换，选中的原始字段仍精确还原。改变提取函数语义必须通过迁移重建依赖索引；SQLite 使用等价的既有逐行范围谓词。

浏览器 HTTP 投影与完整研究输入分开：`/api/sector-research` 的运行状态只携带当前投资判断及其原始版本，不重复附带整份底稿；`/dossier` 的来源列表保留身份、时钟、归属与展示用的数值／方法，完整正文、快照、公司资料和计算输入由既有 `source_id`、`version_id` 查询按需读取。指定来源或版本的查询不使用展示投影，仍受相同标的及历史版本权限约束。领域服务、风险输入与 agent 的已绑定证据保持完整；展示优化不改写存储内容、研究结论或来源时点。

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
- 自定义列表支持重命名，名称去除首尾空白后为 1–200 个字符；重命名不改变稳定 ID、成员、视图、设置和研究。系统名单由登记目录维护，不能手动重命名。
- 名单名称由团队可写账号维护；显示顺序和视图为个人设置。视图写入先锁定所属列表，系统视图首次保存为个人覆盖时同样校验当前可见视图的名称冲突；并发首次保存只建立一份个人覆盖。
- 管理员和协作成员创建的列表归团队共享，所有成员包括只读成员均可见。`created_by_user_id`、`created_by_display_name` 由创建或复制请求的已认证账号写入；显示名保留创建时的归属，重命名不改变创建人，客户端不能提交或修改这两个字段。历史列表无作者证据时两字段均为 null，系统列表不冒认个人作者。
- custom view id 会做 path-safe slug 化
- 复制 watchlist 时会按相同规则规范化源 custom view id
- 每次打开目录都以一次完整的当前资产身份投影同步所有系统名单；单个系统名单只读取对应类型。身份投影包含名称、类型、交易所与标识，不加载行情、公司行动或净值账本。本地身份与默认分类批量比较，只写真实变化；新增、归档和缺失行立即核对，不靠过期时间缓存。
- 同步只为缺失的股票／加密资产分类补系统默认值，保留用户已有分类与研究。收益、风险与图表继续使用物化读模型及 durable 重算，不在目录请求内重算。

列表显示列、筛选和分组由
[`watchlist_query_contract.py`](../backend/watchlist_app/services/watchlist_query_contract.py)
定义固定产品合同，不按当天数据非空比例动态开放。完整字段注册继续服务详情和内部分类，
与列表菜单分开返回。

- 显示列分为基本信息、收益表现、风险与状态；名称固定，其余按账号保存。
- 筛选支持分类体系、资产类别、币种、投资状态、风险关注和新鲜度；分组支持分类、币种和投资状态。
- Overview 默认显示名称、资产类别、分类、近一月走势图、今年收益、当前回撤、最新值日期和指标截至日，默认不分组、不筛选。
- 列表收益窗口为 1W、1M、3M、YTD、1Y，走势图为 1M；其他窗口、年化收益和历史风险统计在详情结合期间及覆盖解释。
- 最大回撤、波动率和 Sharpe 使用各标的自己的有效历史，不能当作同区间风险排名。当前回撤表示距离可用历史峰值的跌幅，同样保留历史起点差异。
- 最新值日期与指标截至日分别来自实际报价和计算序列；新鲜状态或没有风险触发不代表完成了研究。
- 风险关注只从当前 RiskCase 投影，普通列表查询不依赖旧研究档案和笔记表。
- 旧个人视图中的已停用列、简单筛选和排序在规范化后保存；分类树保留内部层级字段。退出菜单不删除原始属性、研究记录或计算能力。

### 4.2 Instrument Product Framework

这一层负责 `public_fund / private_fund / etf / equity / index / crypto` 各自的分类，以及适用的研究标签和监控评估：

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

手工资料写入使用所属标的行锁，首次建档也遵守同一顺序。文件索引追加与净值设置局部更新先锁定再读取最新内容，避免并发提交丢失另一份上传或覆盖其他设置。

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

`instrument_chart_read_model` 同一行保存详情历史与列表投影：

| 字段 | 合同 |
| --- | --- |
| `payload_json` | 完整 canonical 图表历史，供详情读取；保持原 JSON 类型。 |
| `screener_payload_json` | 紧凑列表投影：`selected_series`、`latest_values`、`date_range` 与 `sparklines`；不包含完整 `series`。SQL NULL 表示待补齐，不能视为已就绪的空结果。 |
| `source_cutoff_at`、`data_freshness_status`、`materialization_version` 及既有计算时间字段 | 完整图表和列表投影共用的来源、版本与新鲜度依据；补齐投影不改变它们。 |

canonical recalc 在同一事务内写入两个 payload。`sparklines` 固定保存现有 `return_chart_1d / return_chart_1w / return_chart_1m / return_chart_1y` 四个窗口，复用完整图表的窗口锚点、采样、精度和覆盖状态算法。当前列表列菜单仍只开放 `1M` 走势，内部四窗口不代表新增用户选项。Screener 仅读紧凑投影并按所选列取窗口，包含无走势列的请求也不读取完整历史；人工研究状态、同类比较及新鲜度判断仍沿用各自实时读取路径。

迁移 `20260920_0059` 只新增可空列，不扫描重写完整图表。升级必须通过根目录统一迁移与发布流程：停止 managed writers、备份、迁移和目录刷新后，执行 `backend/scripts/refresh_release_watchlists.py`。脚本在发布事务中分批从已存图表派生所有缺失投影，包含归档资产，不调用行情提供商；失败会回滚本次补齐。补齐可重复运行，已经就绪的投影不重写。`infra/scripts/audit_live_data.py` 要求 Watchlist head 为 `20260920_0059`，且 `watchlist_screener_projection_ready` 的 NULL 数量为零，方可恢复流量和调度。此次仅改变读取投影，既有金融指标 materialization version 不因此递增。数据库回退此迁移只删除新投影列，原完整图表与来源时点保留；代码、数据库与备份按统一发布回滚流程配套处理。

运行时遇到已有图表但投影为 NULL，Screener 返回 HTTP 503 和 `Retry-After: 1`，先通过既有 durable 队列提交修复任务；worker 的 source-generation 对账也将缺失投影视作待重算。不会把完整历史当作永久兼容读取路径，也不把缺失投影静默呈现成空图。

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
- `PATCH /api/watchlists/{watchlist_id}`（仅更新 `name`）
- `GET /api/watchlists/{watchlist_id}`
- `GET /api/watchlists/{watchlist_id}/views`
- `POST /api/watchlists/{watchlist_id}/items`
- `POST /api/watchlists/{watchlist_id}/items/delete`
- `POST /api/watchlists/{watchlist_id}/items/move`
- `POST /api/watchlists/{watchlist_id}/items/copy`
- `POST /api/watchlists/{watchlist_id}/views`
- `PUT /api/watchlists/{watchlist_id}/views/{view_id}`

创建请求只接受 `name`、`description`；列表摘要、详情以及创建/复制结果均返回创建人字段。复制的创建人为执行复制的成员，源列表的作者不继承。创建、复制、改名和增删成员均要求团队写权限；列表排序和个人显示视图允许只读成员维护。

证券添加使用 `GET /api/securities/search`（查询参数 `q`、`limit`）查询数据维护端的市场目录，不触发登记；部分目录不可用时返回 `catalog_errors`。`POST /api/securities/materialize` 只接受 `instrument_type`（equity/etf）、`catalog_provider`（当前 fmp）及 `catalog_symbol`，要求非只读的直接用户身份，禁止研究服务或委托凭证调用。共享数据维护命令负责登记与行情刷新，返回 canonical instrument 供已有 `/items` 接口添加；Watchlist 不另建证券注册实现。登记超时或下游确认失败明确提示可能已登记，可重新搜索确认。

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

- Watchlist 不直接写入 instrument identity、canonical NAV 或行情。添加入口经共享证券桥接调用 `investment-studio data` 登记外部目录证券；canonical 数据维护仍归数据层负责。

也就是说，watchlist detail 里 canonical quote/NAV history 是只读视图；派生 payload 需要保留实际 `metric_family / quote_basis / role`，避免场内 ETF 或指数的 `close` 被误标成 NAV。

### 6.4 Instrument Attributes

- `GET /api/instrument-attributes/definitions`
- `POST /api/instrument-attributes/definitions`
- `GET /api/instrument-attributes/instruments/{instrument_id}`
- `POST /api/instrument-attributes/instruments/{instrument_id}`

### 6.5 Field Registry

- `GET /api/field-registry`：`fields`保留完整内部字段定义；`column_field_keys`与`filter_field_keys`分别返回经过审查的全局列表显示列、筛选字段目录。列表菜单使用这两个目录，不按成员类型或当日非空比例推导。

### 6.6 Taxonomies

- `GET /api/taxonomies/instrument-taxonomy`
- `GET /api/taxonomies/instrument-taxonomy/instruments/{instrument_id}`
- `PUT /api/taxonomies/instrument-taxonomy/instruments/{instrument_id}`

说明：

- 这组接口服务 `public_fund / private_fund / etf / equity / index / crypto` 的 Watchlist-local 分类，不把 taxonomy 写回共享 Instrument Data
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

计算源代次和物化版本分别决定结果是否过期。详情读取会对旧结果排队修复；后台 worker 启动后也按批扫描全部 active 本地标的，保证没有页面访问时仍会收敛。首个读请求可以在重建完成前返回旧快照，因此发布不能仅依赖此异步路径。

安装器在停止托管写入进程后调用 `backend/scripts/refresh_release_watchlists.py --recover-interrupted`：明确恢复被停写中断的重算租约，再同步系统目录，再使用已存储的 canonical 输入对缺失或旧版本的 chart / watchlist row 执行 `all` 重算，验证版本后整体提交。失败则连同租约恢复一起回滚该事务并阻止发布。不传恢复参数时不会抢占仍标记 running 的任务；常规 worker 的心跳超时规则保持不变。该步骤不抓取新行情、不运行 AI 研究，不改用户自定义名称、名单成员、个人视图或研究观点；自定义名单中失效的计算结果同样被重建。

## 7. 当前前端路由

当前主路由是：

- `/watchlists`
- `/watchlists/:watchlistId`
- `/instruments/:instrumentId`
- `/monitoring`

说明：

- 详情页 canonical 路由已经收口到 `/instruments/:instrumentId`
- `watchlist` 来源只作为 query context 透传；canonical 详情路径不依赖来源名单
- 添加弹窗同时呈现已登记共享资产与市场目录结果；只有明确点击添加才按需登记，成功后用 canonical ID 加入当前列表。未核实币种不显示为已确认币种；目录错误和登记失败保留原因。

## 8. 开发时的默认判断

如果后续要继续扩这个 app，默认按下面原则判断：

- 新接口一律走 `instrument` 主语
- 产品分类先落 taxonomy，再考虑 research/monitoring labels
- watchlist 前台优先消费 read models，而不是直接扫 facts
- 只在没有更好归属时，才把内容继续放进公募/私募详情共享的 manual profiles
