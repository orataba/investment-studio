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

`research_entry.context_json` 保留完整原件，`read_context_json` 提供内容读取投影；`access_context_json` 仅保留精确的 `research_actor / execution / portfolio_id / risk_scope`。两份投影由同一次 ORM 写入事务派生，权限检查与历史组合范围查询只解析窄权限投影，不反复扫描历史分析正文。历史中曾关联的组合仍全部参与授权，运行工具仍在边界与锁定当前记录后检查 actor、attempt 和实时组合权限；没有跨请求权限缓存。新增列通过逐条 backfill 从既有读取投影派生，不改写原件、内容投影或历史时点；导入研究经现有 ORM 入口，恢复 dump 则保留投影列。直接 SQL 修改研究 context 不能绕过这些派生合同。

迁移 `20260920_0060` 为 PostgreSQL `research_entry` 增加基于留存 `context_json.instrument_ids` 的 GIN 表达式索引。读取先按该次运行的原始标的范围筛选，再解析命中记录的研究字段；不能用后来可变的 `research_topic.instrument_ids` 替代历史范围。索引通过同一不可变数据库函数提取文本数组，由 PostgreSQL 随每次写入维护；它不是第二份研究事实或跨请求缓存。原始 context、来源正文、作者及权限判断保持原样。SQL 工作副本仅为处理 PostgreSQL JSON 中保留的 NUL 转义而转换，选中的原始字段仍精确还原。改变提取函数语义必须通过迁移重建依赖索引；SQLite 使用等价的既有逐行范围谓词。

浏览器 HTTP 投影与完整研究输入分开：`/api/sector-research` 的运行状态使用 `include_events=false` 跳过事件与事件历史读取，只携带当前投资判断及其原始版本，不重复附带整份底稿；`/dossier?current_only=true` 只读取当前底稿、基础档案及研究指导，跳过活动历史、PM 修订、主题时间线和历史来源集合；辅助入口展开后读取材料与完整历史，但不计算页面未使用的研究员议程和主题活动；精确来源／版本请求始终走原始路径。`/dossier` 的来源列表保留身份、时钟、归属与展示用的数值／方法，嵌套来源、完整序列、输入版本、正文、快照、公司资料和计算输入不进入首屏来源目录，由既有 `source_id`、`version_id` 查询按需读取。指定来源或版本的查询不使用展示投影，仍受相同标的及历史版本权限约束。领域服务、风险输入与 agent 的已绑定证据保持完整；展示优化不改写存储内容、研究结论或来源时点。

- `/api/research/catalogue`、`/connections` 提供登记标的与外部证据连接状态。
- `/api/research/topics` 管理持续专题；专题下的 `/entries`、`/files`、`/analysis` 保存材料或发起助手运行。
- `/api/research/runs/{run_id}/context`、`/tools` 只向绑定运行的受限工具提供证据；运行失败保留输入，服务重启将未完成运行标为失败。
- `/api/risk` 只读当前事项；`/api/risk/rules/{instrument_id}`、`/api/risk/cases` 和单事项更新接口管理复核与跟进。

`/api/instruments/{instrument_id}/reference-data` 的参考资料仍保留原 `fetched_at` 和章节快照；当前读取另带 `current_data`，区分最新正式净值日期、实际来源与导入时间、最近采集检查。邮件来源来自该净值的留存 lineage，导入时钟按相同标的和 route 精确读取 `data_ingestion.email_nav_candidate_route`，不以行情事实更新时间代替。`source` 只展示当前配置来源，可能与最新一条事实的实际文件夹不同。`no_new_data` 表示检查完成但没有新增，不表示旧净值失效。内部按 `as_of` 读取的历史研究不附加这些当前状态，继续使用当时留存的来源上下文。

价格复核的 `price_rule_summary` 与事项计数分开：`configured` 为启用复核线数，`evaluable` 为当前数据满足要求的启用规则数，`triggered` 为其中越线数，`unavailable` 为缺读数、样本不足或数据覆盖不合格的启用规则数；没有配置的规则是 `not_configured`，不会记为“未触发”。同一 `period_loss` 事项可以对应多条越线规则，`quantitative` 事项数为零不等于没有默认阈值或没有风险。每条规则保留正数损失限额、实际读数、窗口观察数及起止日，另带来源 `default` / `custom` / `unknown`：未修改的历史波动初值可确认为默认；新手改只把改变的字段标为自定义；旧记录只有“曾手改”标记而无逐项来源时不猜测。高点回撤线没有系统默认，非空时为自定义。

`prepare_run` 将规则摘要、校准与实际观察值一同冻结在现有 `risk_inputs`，工具 `read_risk_instrument(s)` 读取这份快照，根索引提供 `price_rule_counts`。规则的 `settings_updated_at` 表示当前保存配置生效的时间，并非行情日期；任务编号、冻结时刻和完整参数共同确定当时版本，`contract_version` 仅表示摘要结构版本。研判展示投影保存同一份规则事实，运行中更改设置只影响新输入和过期提示。旧报告缺少摘要时保留原结论与原始证据，显示未记录，禁止用当前规则倒填历史。GET 不生成初值、不补写来源。普通市场滚动窗口为 1/5/21/63 个收益观察值，24/7 市场为 1/7/30/90，均非本周／本月累计收益。

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
- 当前页面的视图更新串行写入，保存中/失败重试明确可见；旧响应不能重新套用旧列到正在编辑的视图。筛选或分组请求与返回结果按完整查询条件绑定，待加载字段不作为空值分组事实展示。
- Name 固定列与选择辅助名称保留唯一标识。全选覆盖当前全部筛选结果（含尚未展开行）；搜索/筛选变动仅保留仍匹配的已选成员，展开更多行不改变选择。组级数字是有效成员算术平均，显示有效数/总数，缺失不计零；混合指标日期不汇总。
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

`GET /api/instruments/{instrument_id}/monitoring` 读取后台物化的固定指标目录（回撤、区间最大回撤、1M 滚动波动／下行偏差、VaR／ES）、序列、样本口径和不可用原因；`metric_snapshot_id` 查询参数读取留存旧版。`GET /api/instruments/{instrument_id}/tail-risk` 选择同份快照预计算的置信度 0.95／0.99 与回看 365／1095／1825 天组合，保留原小数收益单位，不从显示价格重复推导。历史合同新增到既有 `risk_snapshot.monitoring_json`，旧行空值不伪造历史；最新投影存 `InstrumentRiskReadModel.payload_json.monitoring`，供 agent 的精简数值在 `monitoring_summary`；风险 workspace 与普通 `/risk` 接口只返回该摘要，完整曲线留在专用监测接口。确定性 worker 随来源和应到时钟重建，读接口不计算，独立于付费研究 agent。

`PUT /api/risk/rules/{instrument_id}` 保留 `drawdown_limit`、`period_limits`，可附 `severities`（drawdown/day/week/month/quarter 对应 attention 或 severe）与 `change_reason`。编辑界面传回读取时的 `expected_rule_version`；版本已变时返回 409，避免覆盖另一人的编辑。旧规则默认 attention；显式严重且有效触发才产生 `mandatory_notice`。`calibration_json` 保留初始来源并追加 `rule_revision`、`rule_history`，风险 workspace 返回 `rule_version`、每条规则的 `metric_snapshot_id`、严重性和强制提示状态。`POST /api/risk/rules/{instrument_id}/preview` 接受相同输入，只读比较留存样本上的新旧历史命中及缺失数量，不改变规则、事项或通知。经济、统计、频率和历史边界见 [RETURN_SERIES_CONTRACT.md](./RETURN_SERIES_CONTRACT.md)。

Performance snapshot 的 `return_1w / return_1m / return_3m / return_6m / return_mtd / return_ytd / return_1y` 必须完整投影到 `watchlist_row_read_model`、field registry、筛选/排序和导出。新增窗口时不能只改计算 snapshot 而遗漏 migration、repository 或 serializer。窗口边界及 return semantics 见 [RETURN_SERIES_CONTRACT.md](./RETURN_SERIES_CONTRACT.md)。

每个 `watchlist_row_read_model.last_nav_date` 是该 instrument 的 metric as-of，不是名单共用日期。Screener 即使未选日期列也必须在每行返回 `metric_as_of_date`，并在 `snapshot_metadata` 返回行终点的 min/max、是否混合及缺失数量。Peer snapshot 只比较相同 as-of；路径风险还必须记录 Instrument Data frequency/calendar 缺点状态。

`instrument_chart_read_model` 同一行保存详情历史、列表和 NAV 投影：

| 字段 | 合同 |
| --- | --- |
| `payload_json` | 完整 canonical 图表历史，供详情读取；保持原 JSON 类型。 |
| `screener_payload_json` | 紧凑列表投影：`selected_series`、`latest_values`、`date_range` 与 `sparklines`；不包含完整 `series`。SQL NULL 表示待补齐，不能视为已就绪的空结果。 |
| `nav_payload_json` | 完整 NAV API 数值合同，复用本轮选择、计算频率和 return series；SQL NULL 表示尚未构建，不从旧显示价格推断收益。 |
| `source_cutoff_at`、`data_freshness_status`、`materialization_version` 及既有计算时间字段 | 三个投影共用的来源、版本与新鲜度依据。 |

canonical recalc 在同一事务内写入三个 payload。`sparklines` 固定保存现有 `return_chart_1d / return_chart_1w / return_chart_1m / return_chart_1y` 四个窗口，复用完整图表的窗口锚点、采样、精度和覆盖状态算法。当前列表列菜单仍只开放 `1M` 走势，内部四窗口不代表新增用户选项。Screener 仅读紧凑投影并按所选列取窗口，包含无走势列的请求也不读取完整历史；人工研究状态、同类比较及新鲜度判断仍沿用各自实时读取路径。

迁移 `20260920_0059` 只新增可空列，不扫描重写完整图表。升级必须通过根目录统一迁移与发布流程：停止 managed writers、备份、迁移和目录刷新后，执行 `backend/scripts/refresh_release_watchlists.py`。脚本在发布事务中分批从已存图表派生所有缺失投影，包含归档资产，不调用行情提供商；失败会回滚本次补齐。补齐可重复运行，已经就绪的投影不重写。`infra/scripts/audit_live_data.py` 要求 Watchlist head 与当前迁移一致，且 `watchlist_screener_projection_ready` 的 NULL 数量为零，方可恢复流量和调度。0059 仅改变列表读取投影，当时没有递增金融指标 materialization version。数据库回退此迁移只删除新投影列，原完整图表与来源时点保留；代码、数据库与备份按统一发布回滚流程配套处理。

运行时遇到已有图表但投影为 NULL，Screener 返回 HTTP 503 和 `Retry-After: 1`，先通过既有 durable 队列提交修复任务；worker 的 source-generation 对账也将缺失投影视作待重算。不会把完整历史当作永久兼容读取路径，也不把缺失投影静默呈现成空图。

迁移 `20261016_0075` 新增可空 `nav_payload_json`，不修改旧图表、历史事实或来源时点。v22 的 canonical recalc 使用与 performance/risk/chart 相同的捕获输入生成 NAV；统一发布脚本必须重建所有 active 本地标的，worker 对账及首次读取也会为缺失 NAV 投影持久入队。`GET /nav-series` 返回 `source_cutoff_at`、`last_recalculated_at`、`materialization_version` 与重算状态；重算未完成时保留旧代数值并明确 pending，缺失时返回 503 和 Retry-After，可显式重试。手工比较标的设置即时 overlay，计算口径和数值仍属于所标版本。已归档资产已有投影保留；无投影时复用原 NAV builder 保留既有 200/不可用序列合同，不入队、不借用旧图表时点声称同代。无 canonical 的 archive 仍无选中序列，alias 仍沿 canonical 解析；这一分支不用于 active 资产。该迁移回退只删除新增列，既有图表不变。

迁移 `20261016_0076` 新增 `instrument_risk_read_model.workspace_price_context_json`，当前物化版本为 v23。canonical 在同一事务中从本轮 `chart_payload.research_returns` 计算并发布规则无关的周期读数、当前样本限制、近期有效点数、上次回撤差及日期；不拿另一个监控样本代替该合同。Watchlist 风险抽屉使用 `GET /api/risk`（`projection=workspace`），在单次 SELECT 中读取风险必要字段与这些价格输入，随后 overlay 当前阈值/规则版本并复用原规则摘要函数。此投影只省略界面未使用的 `risk.monitoring_summary`，全部案例摘要、过滤范围、人工跟进及精确版本证据入口不变；默认 full 和 `summary=true` 合同保留。发布脚本通过既有重算路径补齐所有 active 本地标的；留存或归档行缺投影时只对该行执行原只读计算，不写业务状态、历史或任务。迁移不回写旧金融事实，回退只删除新增列。正常重新发布 risk 时必须同时写入或清空价格输入，不能留用前一代。

`GET /chart` 的默认 full 合同保留；`projection=display` 在数据库内仅选择展示序列、报价与元信息，`projection=quote` 保留既有报价合同。列表与全局监控消费紧凑投影。身份解析保留 corporate actions 与 canonical 重定向，前台 freshness 读 source summary；后台计算输入包含全量标量行情、corporate actions、当前 NAV run 的状态/断点/锚点，不展开历史 runs/factors/events。原审计读取入口和存储完整保留。

### 4.6 Recalc Jobs

重算已经 durable 化，不再依赖临时线程：

- `recalc_job`

stale read repair 也只会写 job，不会直接在 Web 请求里补算；后台 worker 会异步消费这些 queued jobs。

风险事项刷新使用 PostgreSQL `FOR NO KEY UPDATE` 串行化同一资产的风险写入，事项行本身仍使用 `FOR UPDATE` 保留人工跟进历史。刷新不更改资产身份键，因此不会阻塞 `recalc_job` 外键检查所需的 `KEY SHARE` 锁；批量行情通知可在风险扫描期间提交队列。`POST /api/recalc/bulk` 的成功响应表示持久入队或已有未完成任务，不表示重算完成；客户端超时也不能据此推断服务器事务已撤销，任务终态以持久队列为准。

数据覆盖事项解除后，同一观察日、预期日、事实修订及缺口质量再次触发时续写最近的原事项和复发记录，保留人工状态及跟进；新缺口另建事项，已有历史触发区间与其引用不物理合并。

Instrument Data 通知只是低延迟提示，不是正确性边界；worker 会分页对账源版本、本地 materialization cutoff、投影缺失和应到时钟，并通过同一条 durable、per-instrument 串行队列修复漏通知。每轮不再无条件扫描全部风险历史；行情重算、规则修改和研究笔记写入继续按标的刷新风险。period-loss 使用最后 N 个真实观察点，墙上时间本身不会改变这个窗口；coverage/stale/no-trade 的时钟变化通过 freshness 对账触发重算与风险刷新。事项跟进、复发、通知和研究复核调度保留各自原路径。

### 4.7 Watchlist 投资研究

自动研究仅监测当前底稿、重要活动主题、持续跟踪事件和未解除风险实际引用的来源。已留存的来源目录本身不构成监测依赖；未再被当前工作集引用的背景或其他产品网页修订不会单独启动模型。真实跨产品比较引用仍保留更正、撤回传播。监测器只在当前依赖已明确时移除同类悬空 pending，保留仍被引用的失败更正和归属未知的历史项；淘汰理由记于 `scan_cursor_json.last_dependency_pruning`，不推进成功应用水位、不计入模型上下文，也不改写历史原件。扫描使用单一路径，始终返回当前到期项及检查水位；是否创建新任务仅由持久 monitor 的 pending、attempted、applied 状态决定，不再以旧运行的同日尝试时钟隐藏到期事项。

研究与事件事实继续使用 `research_entry`、主题、来源与 `risk_case`。执行队列由 `research_execution` 保存，风控交付状态由 `research_risk_read_state` 按运行与 attempt 保存，新增计算原件由 `research_run_evidence` 按运行与来源保存；历史原件不改写，当前判断没有平行副本。页面按总览、标的动态、重要研究主题、量化指标阅读；总览直接显示建议、依据、机会和风险，资料与历史按需读取。数值来源的 `observation_domain=market|fundamental|unclassified` 区分市场观察与经营财务分析；量化指标同时提供确定性市场数据和适用基本面，市场观察模块新提交的 `market-quantitative.figure_source_ids` 必须符合该用途。原始历史来源与其版本保持完整，不根据标题猜测分类。

- 当前投资判断的同一版本增加 `opportunities`、`risks` 数组，每项包含稳定 `key`、标题、解释、下一观察，以及 `source_ids` / `event_keys` / `theme_ids` 引用；数值依据用 `figure_source_ids`。发布时绑定实际留存证据，历史按原版本读取。数组缺省继续保留旧判断，明确空数组才表示本轮已评估且暂无对应事项。旧记录没有数组时显示覆盖未建立，不推断“没有风险”。`coverage_status` 为 `assessed` / `limited` / `not_established`；`coverage_note` 说明限制。
- 新重要事件须有整数 `importance_score`（1–5）和具体 `importance_reason`；旧值允许未知。评分不等于确定性、方向或风险严重度。`market_views` 保存有发布者、日期和来源的公开观点；没有样本时不生成共识。`market_reaction` 仅解释绑定的确定性计算及其限制，不写自由浮点行情。
- `follow_up=watch|none|resolved` 只管理研究跟进。`watch` 可以不关联主题，必须有下一观察；默认期限为 30 个自然日。`follow_up_until`、`follow_up_reason`、`next_observation_on` 保留复核期限和原因。只有有写权限的 PM 能修改 `follow_up_pinned`；模型无法提交该字段。固定、明确未来节点、重大未结风险不会被普通到期流程关闭。检查、措辞、评分及固定操作不刷新事件事实时钟。服务器按本次进展的已核实发布日期保留 `development_at`：旧事件可凭新进展进入近期流，同时保留原发生日；补录不冒充当日新闻。显式 `progress_kind=editorial` 不算实质进展。
- 共享风险变更在既有 `risk_case.history_json` 中追加 `run_id`、发起人 `actor`、绑定 `scope`、`before` / `after` 与 UTC 时间，同一条变更同时保留在风险结果 `case_changes`。只记录实际改变的事项，不回填历史未知归属；现有版本比较和行锁继续阻止并发覆盖。
- 风险判断独立保存在 `risk_case.evidence_json.risk_assessment`。新风险线索先待复核；团队风控通过绑定事件版本的 `case_assessments` 写入 `pending` / `active` / `resolved` / `dismissed` 及原因。仅停止跟进、主题接管或调整研究方向不能清除既有有效风险；组合私有复核不得修改团队风险状态。Watchlist 和 Portfolio 读取同一风险事项。
- 研究与团队风控在发布时重新校验当前团队角色／服务研究权限，组合风控重新校验组合访问。PM 跟进与自动风险刷新在行锁下读取最新事项并追加历史；批量风险评估按事项标识排序加锁。首次自动风险创建和阈值更新按标的串行，避免并发创建重复事项或丢失 PM 记录。
- 风控的暂时失败沿既有 `queue_retry` 恢复同一运行与原发起人，最多三次总尝试；绑定快照和信息截止不在恢复中替换。没有已受理检查点的新尝试清空旧会话已读覆盖并重新阅读；已受理、尚未发布时只恢复原检查点发布，不再次调用模型。已失去发布权限的发起人不能重排共享研究或风险任务。
- 风控工具保留 48KB 返回上限；单条当前或历史记录过大时，返回同一分区原页的 `deferred` 目录，以 `page_offset` 绑定原页、`path` 定位字段、`offset` 续读该字段。原文、来源与 PM 判断不截断；全部 `next_offset` 和 `deferred.path` 分支实际交付后才通过必读检查，目录本身不能充当已读证据，未完成时返回精确 `missing_reads`。
- 公开来源统一由 `/research/runs/{run_id}/sector-evidence` 留存；旧 `/tools` 的 `search` / `source` 与任意 `public_result` 写入口已删除。历史 `tool_evidence` 仍可读取，搜索目录不能当作原文证据。PM 讨论引用同时携带 `pm_note_id` 和 `pm_note_revision`，服务器按原版本绑定，失效引用在入队前返回 422。
- `POST /research/runs/{run_id}/sector-search` 接受 `query/search_id/offset/limit/path`。首次仅发送公开查询（最多 1500 字符），由 API 以私有 Tavily key 检索；run token、主体、attempt、运行状态和阶段在联网前后复核。结果保存于原 `web_evidence`，返回 `search_id/provider/search_status` 及最多 48KB 的分页 `data`；以相同 query/search_id 续读只访问留存结果。未配置为 `operation=error/search_status=not_configured`；成功零条为 `operation=search/search_status=completed`。搜索线索不进入原件引用目录，不推进冻结输入／知识截止；实际原文仍通过现有 `sector-evidence` 的 fetch 取得并保存来源时钟。

- `POST /research/runs/{run_id}/wind-tools` 接受 `domain/offset/limit/path`，按域分页发现官方 Wind 只读工具及参数；`POST .../wind-query` 接受 `instrument_id/domain/tool/arguments`，由后端查询并留存供应商原件。开放 `financial_docs/fund_data/stock_data/index_data/economic_data`，禁止任意端点和通用分析。返回 `computed:` 来源编号沿现有 `read_research_numbers` 及量化输入选择器读取，但 `analysis_kind=provider_snapshot` 明确不是系统计算；原件 `data.payload` 保留供应商完整 JSON。目录同步保留 `analysis_kind/provider/retrieved_at`，页面区分取得时间和计算时间。相同运行内同请求复用；只读供应商查询不改 canonical 数据或账本，凭据仅在后端外置配置中保存。

主研究员通过原生 `delegate_research` 按需创建具体任务；标的动态、主题、量化是职责模板，不要求每次全部执行。受信任的 Harness 适配器调用 `POST /api/research/runs/{run_id}/tasks`，将真实子会话身份绑定任务，再通过 `GET .../tasks/{task_id}/context` 和 `POST .../tasks/{task_id}/result` 读取分工上下文、保存经验证的提案；失败由 `POST .../tasks/{task_id}/failure` 留存具体限制。主题专员仅能进一步委派同标的、范围不扩大的主题子任务，最多两层。

`context.research_tasks` 按任务保存角色、标的、主题范围、父任务、执行尝试及输入截点。上下文路径支持字符串字段及整数数组索引；主研究员通过 `task_results` 读取提案，子会话只通过原生 `read_assigned_context` 定位自己的分工和直接子任务。当前有效任务结束后，只有主研究员可提交最终草稿；独立核证读取各任务原始风险与限制。事件没有实质变化时可提交稀疏回执 `{action: "checked", event_key, event_version_id}`，不得附替代正文、来源或日期；服务核对冻结与当前版本，仅更新复核时间。提案不是独立事实来源，不直接发布事件或主题，也不修改人工记录。恢复时复用已验证且输入未变的结果，未完成任务留存中断原因。

`GET/PUT /api/research/guidance` 读取／保存团队共同 `guidance` 与六个角色的 `section_skills`（overview、recent_updates、themes、quantitative、risk、review）。写入仅允许团队可写成员，使用 `expected_revision` 并保留版本与作者；修改 risk 文本还须提供非空 `reason`，其他角色不新增原因要求。未保存的默认框架读取不落库。新运行在准备期冻结团队指导，工具仅投影本角色所需的部分，旧运行继续原冻结含义。

`GET/PUT /api/risk/guidance` 提供团队通用与单标的风险补充，保存使用 `scope`、可选 `instrument_id`、`text`、`expected_revision` 和必填 `reason`，只更新对应风险字段并保留其他研究指导及其作者时钟。展示用 `content_revision` 表示风险文本的实际版本，并发保存仍核对底层 `revision`；从未设置的标的补充显示内容版本 0、空作者与时间，已有文本再清空则形成真实新版本。`GET /api/risk/guidance/preview` 只读已有案例和上次正式筛选，不调用模型模拟未保存指导。

`GET /api/risk/review` 的 `notice_items` 是当前范围的默认短提示，`notice_status` 区分已筛选、待筛选与不可用。首页使用 `GET /api/risk/alerts` 并传入查询参数 `all=true`，读取全部团队可见短提示及严重规则事实。`PATCH /api/risk/notices/{run_id}/{notice_id}` 带 `expected_notice_version`、等级、标题、正文及原因，更正须具备原团队或组合的写权限。`PUT /api/risk/notices/receipt` 保存本人当前提示版本的已读／静默状态，严重规则版本通过 `rule_versions` 一并核对；只读成员也可记录自己的回执。Portfolio 经 `/api/instrument-risk` 原身份转发，服务器重新检查原提示范围，私有组合内容不进入团队首页。

`PUT /api/research/instruments/{instrument_id}/dossier/mandate` 接受 `guidance`、四部分 `section_skills` 和 `expected_revision`。 此处是单标的补充，角色留空继承团队框架；修改不自动启动运行。`POST .../themes/{theme_id}/notes` 保存人工记录，`DELETE .../themes/{theme_id}` 按当前 revision 删除主题及研究引用，保护 PM、事件事实与风险状态。模型不得通过专员工具编辑用户 skill 或 PM 观点。

新增或扩展的读取／工具合同：

| 入口 | 行为 |
| --- | --- |
| `GET /api/research/instruments/{instrument_id}/events` | `scope=recent|watch|history` 分别读取近 7 个自然日、仍在跟进或全部历史事件；接受 `display_timezone`、`offset`、`limit`；返回分页信息和单列的旧材料晚到提示。默认只读当前事件，`event_key` 精确定位；`include_history=true` 按需读取该事件的历史研究及 PM 操作审计（独立 PM 观点沿用投资观点与完整历史入口）。 |
| `PATCH /api/sector-research/events/{case_id}/follow-up` | PM 固定／取消固定；须携带 `follow_up_pinned` 和当前 `event_version_id`，拒绝过期版本和只读身份。 |
| `GET /api/research/instruments/{instrument_id}/themes` | `include_history=false` 返回主题概要投影，不读取完整历史和原文；单个 `themes/{theme_id}` 在展开时读取完整详情。 |
| 既有 dossier 版本／来源入口 | 保留标的与版本权限；支持精确事件版本，不把当前原文代替历史证据。 |
| `POST /api/research/runs/{run_id}/numeric` | 支持 `action=observations|event_reaction`；新增原件按绑定运行与 source_id 写入 `research_run_evidence`，原 `computed_metrics` 历史仍可读取。可传 `benchmark_id`；事件反应须有 `event_date`，`event_timing=date_only|before_open|after_close`，默认日期粒度。计算口径见 [Return Series Contract](./RETURN_SERIES_CONTRACT.md)。 |

#### 发布与重置

发布按[数据库工作流](../../../docs/DATABASE_WORKFLOW.md)停写、备份并运行检查。经用户授权的历史生成研究清理由专用 reset 命令完成，先预览，保护交易、行情、人工资料及权限，验证核心表前后摘要一致。不得以本地整库覆盖云端。新四部分研究不依赖旧生成结果，风险独立状态、来源版本与权限合同继续有效。

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
- `POST /api/watchlists/{watchlist_id}/items/coverage-status`
- `POST /api/watchlists/{watchlist_id}/items/delete`
- `POST /api/watchlists/{watchlist_id}/items/move`
- `POST /api/watchlists/{watchlist_id}/items/copy`
- `POST /api/watchlists/{watchlist_id}/views`
- `PUT /api/watchlists/{watchlist_id}/views/{view_id}`

名单详情同时返回实际成员 `instrument_ids`，独立于当前视图、筛选和行情计算覆盖；添加窗口据此标记并禁选已有成员，不以可见表格行推断成员关系。

创建请求只接受 `name`、`description`；列表摘要、详情以及创建/复制结果均返回创建人字段。复制的创建人为执行复制的成员，源列表的作者不继承。创建、复制、改名和增删成员均要求团队写权限；列表排序和个人显示视图允许只读成员维护。

名单添加请求接受 `instrument_ids` 和可选 `coverage_status`（`Watch / Proposed / Invested / Paused / Exited`）。不传状态或传 `null` 保留原状态；指定状态时，已存在成员也会更新其全局状态。成功返回 `accepted_count` 和 `pending_recalc_instrument_ids`；成员、状态和新成员的持久队列在一个事务中提交，行情计算由现有 worker 完成，接口不再声称已完成重算。已有 queued 全量任务可合并，已有 running 任务必须有一个提交后的后继任务。队列提交失败回滚整个添加，后台计算失败不删除已保存的成员。

批量状态接口接受非空 `instrument_ids` 与 `coverage_status`，先校验全部标的属于该列表再原子更新，返回 `updated_count`。状态属于标的，在所有名单和详情中一致；状态修改不触发全历史行情重算，列表在过滤、排序和分组之前批量覆盖真实属性表的最新状态，详情同样读取当前属性，不会被较早启动的物化任务覆盖回旧值；系统名单不能修改成员，但可通过此接口更新成员状态。成员检查失败不做部分写入；只读权限仍不能调用写接口。

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
- 股票由 Instrument Data 的 canonical `exchange_code` 确定市场分支；用户可在该市场分支内选择行业叶子，不能跨市场赋值。默认的待分类叶子不构成同类组。
- 原生加密资产的 `crypto-native` 为导航分类，不自动构成同类比较组，也不要求不适用的地域字段。
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


### 研究员编制与版本化发布

`POST /api/research/instruments/{instrument_id}/dossier/materials` 收录自行整理的事实笔记或摘要时须传 `source_kind="generated_source_summary"`；资料仍可阅读，但按既有 `derived_reference` 合同排除于原始核证依据。省略该字段保持用户提供原始材料的既有语义；来源类型不由正文中的免责声明推断。

`POST /api/research/imports/preview` 是只读校验；`POST /api/research/imports/publish` 仅接受明确标的、同一内容包及预览返回的 `expected_versions`。使用者须有团队研究写权限。发布与正常研究共用标的锁、结构和来源校验、主题数量/固定规则及版本保存；期间研究或主题已变化时拒绝旧预览。内容包和预览文件保存在仓库外。

已有 `watchlist:research` 服务身份可调用这两个精确入口，按真实服务身份留痕；模型运行凭证不能自行调用。编制入口不接受研究设置修改，完整编制须明确提供 `key_drivers` 与 `next_research`（允许空数组），避免无意继承不一致的旧摘要。常规 DeepSeek 仍使用原有稀疏更新合同。

编制包提供逐项来源原文或精确摘录及定位，并将量化图表绑定至实际留存的数值表和计算说明。材料进入独立的作者提供原文命名空间，不冒充系统联网抓取；预览和结构校验本身不等于事实核证。`notebook.publication` 显示作者、研究截至日期、是否首次基线与核查方法，明确记录 `model_execution=false` 和 `independent_model_review=false`。作者依据真实复核结果填写说明，系统不能把声明升级为独立核证。

本入口用于编制当前报告和持续主题，不接受事件历史改写。关闭或替换旧主题必须显式给出原因，既有用户固定主题和 PM 投资观点仍按原合同保护；旧报告、主题版本与原始证据继续保留。DeepSeek 后续研究沿用现有持续研究与独立核证路径，不因人工编制而绕过原有要求。

```bash
python apps/watchlist/backend/scripts/import_research.py \
  --api-base http://127.0.0.1:8000 --instrument-id TARGET \
  --input /external/research-package.json --preview-output /external/research-preview.json
python apps/watchlist/backend/scripts/import_research.py \
  --api-base http://127.0.0.1:8000 --instrument-id TARGET \
  --input /external/research-package.json --publish --preview /external/research-preview.json
```

远程调用使用现有授权的 `WATCHLIST_IMPORT_TOKEN` 或 `WATCHLIST_IMPORT_COOKIE` 进程环境，不在命令参数或仓库保存凭据。CLI 默认以 API 地址的 Origin 遵循既有请求来源校验；不同前台域名可显式传 `--origin`。本地与云端分别预览、发布并保留各自历史，禁止整库复制研究记录。
