# Investment Studio Architecture Boundaries

## Current Topology

`Investment Studio` 是统一入口、按业务组组织代码与运行的工作区，不是全应用共用数据库的平台：

- `home/`
  登录与导航主页，不是业务 App，也不连接业务数据库。
- `shared-data/`
  独立的 CLI 数据维护与定时作业，没有 HTTP 服务。
- `apps/watchlist`
  公募 / 私募 / ETF / 股票 / 指数 Watchlist、local detail、monitoring 与 recalc。
- `apps/portfolio`
  Portfolio / account / transaction / performance / risk / research / taxonomy。
- `apps/regime`
  Git 子模块，独立代码仓、依赖、数据库、更新任务与发布流程；不导入 `shared-data`。

Watchlist/Portfolio 共用 `investment_studio` 数据库，包含：

- `instrument_data` schema
- `data_ingestion` schema
- `watchlist` schema
- `portfolio` schema

Regime 独立使用 `market_data` 数据库。即使同在一台 PostgreSQL 服务器，也不跨库引用，
不共用行情更新事务；重复使用 FMP 数据是有意保留的业务隔离。

## 文件与运行归属

| 内容 | 本地位置 | 云端位置 |
| --- | --- | --- |
| Studio 源码 | `~/Projects/investment-studio` | `/opt/investment-studio/current` |
| 共享数据库 | PostgreSQL `investment_studio`，集群 `/opt/homebrew/var/postgresql@17` | `investment-studio-postgres` 容器，`investment-studio-postgres-data-v1` 卷 |
| Watchlist 上传文件 | `~/.local/share/investment-studio/watchlist-documents` | 服务用户同名相对路径 |
| Portfolio 计算产物 | `~/.local/share/investment-studio/portfolio-research-outputs` | 服务用户同名相对路径 |
| 更新状态与锁 | `~/.local/state/investment-studio` | 服务用户同名相对路径 |
| Studio 密钥 | `~/.config/orataba/secrets/investment-studio` 与 `~/.pgpass` | `/home/investment-studio/.config/orataba/secrets/investment-studio` 与该用户 `.pgpass` |
| Regime runtime | `~/.local/state/asset-regime-dashboard-production` | `/var/lib/regime-dashboard` |
| Regime 密钥 | 独立 `~/.local/state/market-data/secrets`、`~/.config/asset-regime-dashboard/secrets` | 独立 `/etc/regime-dashboard/credentials`、`compose-secrets` |

邮件原始附件及解析证据在 `data_ingestion` 数据库中，不与 Watchlist 上传文档混放。
Regime 的物化快照、模型、运行结果归自己的 runtime；其部署包由子模块的发布流程生成，
不是从 Studio 数据库恢复。Linux Studio 服务用户为 `investment-studio`；Regime 保持独立 worker/API 身份。

不是：

- `data_ingestion` schema 作为共享事实源或跨 app 数据 API
- `watchlist` / `portfolio` 通过 app-to-app HTTP 取得 canonical 行情或复用账本、绩效计算
- 一个 app 的 detail page 直接充当另一个 app 的上下文页

## Runtime Boundaries

### Home

- 只处理登录、会话、业务入口地址和自身健康检查
- 不导入 `studio_data` 或 `instrument-core`，不读取数据库或供应商密钥
- `home.env` 与后台维护的 `data.env` 分开
- `home/apps.json` 定义导航项，`INVESTMENT_STUDIO_HOME_APP_URLS` 只覆盖运行地址；导航目录不承担服务发现或数据状态管理

### Data

- 直接读写 `instrument_data` 中的共享资产事实
- 直接读写 `data_ingestion` 中的邮件抓取、原始证据、解析、重试和候选路由状态
- 共享数据维护只能经 CLI 与定时任务执行，不提供 HTTP 服务
- 不依赖主页进程；暂停作业不会使业务页面失去对已有事实的读取能力

### Watchlist

- 直接读写 `watchlist`
- 直接读取 `instrument_data`
- 当前已发布范围是 `public_fund / private_fund / etf / equity / index`；其他共享资产可以存在于共享资产数据，但不进入 Watchlist 主工作面
- 在本地维护自己的 read models、recalc jobs、manual profile 和产品框架
- 维护研究对话、证据、助手运行快照和标的风险跟进；Portfolio 的标的风险入口读写同一套 Watchlist 风险事项
- 研究工具通过显式配置的只读 API 取得 Portfolio 持仓与 Regime 状态；不取得交易写权限，不跨 schema 复制这些事实

### Portfolio

- 直接读写 `portfolio`
- 直接读取 `instrument_data`
- 在本地维护自己的 ledger、lots、performance、risk、taxonomy、target set、research
- 在本地维护 FCN/期权不可变合约及事件交易；只有合约的 underlying / deliverable 引用 Instrument Data 市场资产
- 标的风险面板经 Watchlist API 读取和更新跟进记录。组合风险、风险预算和研究求解仍由 Portfolio 独立计算；该连接失败只使标的风险面板不可用

## Same-Name Page Boundary

Watchlist 与 Portfolio 会使用相同的投资术语，但这些页面不是同一个业务对象：

- Watchlist Instrument Detail 位于 `/instruments/:instrumentId`，Performance / Risk 面向单一公募、私募或上市资产的 NAV、price、benchmark 和单资产统计。
- Portfolio workspace 位于 `/portfolios/:portfolioId/...`，Overview / Performance / Risk / Research 面向组合账户、交易、现金流、持仓、TWR、归因、风险预算和规划求解。
- 两边可以遵守相同的视觉基线，但不得互用业务计算、页面 fixture、截图或验收结果。
- 修改或验收前必须同时确认 app、URL、源码目录与 API origin；页面标题相同不能证明页面身份相同。

## Shared Layer

当前共享层分成三部分：

### `shared-data/instruments`

承载跨 app 稳定 contract 和共享持久化 helper：

- `instrument_id`
- `instrument_name`
- identifiers
- `instrument_type`
- `currency`
- typed market data / FX
- quote selection policy
- shared store helper / db models

### `packages/ui`

承载跨 app 的前端共享能力：

- language context
- language selector
- confirm dialog and modal focus management
- notice toast and download-format menu
- Sparkline and table export
- InstrumentRiskPanel 的展示和跟进交互；风险事项持久化仍由 Watchlist 所有
- request identity and serial task helpers

只沉淀已经在多个 app 中稳定复用的基础能力；业务布局、业务表格和领域组件仍留在各自 app。

### `instrument_data` schema

承载可跨 watchlist、portfolio 和数据源复用的市场资产主档与共享市场事实：

- instrument
- instrument_identifier
- instrument_market_data
- registry metadata

FCN 与期权合约依赖具体组合、账户、对手方和交易条款，不是共享市场资产；它们不进入 Instrument Data，也不产生 Instrument Data 行情。Instrument Data 只保存其 underlying / deliverable 等可复用证券。

`instrument_data` 的 Alembic 入口由独立的 [shared-data/instruments](../shared-data/instruments/MIGRATIONS.md) 管理，与主页无关。

## Data Ingestion Layer

### `data_ingestion` schema

承载后台摄取可重放、可审计的运行状态，不向 Watchlist 或 Portfolio 提供 canonical 市场事实：

- 每个邮箱目录及其 `UIDVALIDITY / UID` 高水位
- 邮件出现记录、附件内容哈希和消息到附件的关联
- 带 parser 版本、lease、重试与 dead-letter 状态的解析任务
- 尚未进入 Instrument Data 的 NAV 候选和原始 NAV 证据
- 供应商原样提供的现金累计净值、复权口径声明与拒绝原因

该 schema 由 [shared-data/alembic](../shared-data/alembic) 独立迁移。口令和 token 不进入数据库；附件内容、哈希和解析证据保存在受限的 `data_ingestion` 分区，不通过业务页面或公共 API 暴露。

## Storage Boundary

共享资产身份由数据库直接约束，而不是靠 app 约定：

- `portfolio.transaction_record.instrument_id -> instrument_data.instrument.instrument_id`（普通市场资产交易）
- `(portfolio.transaction_record.portfolio_id, derivative_contract_id) -> portfolio.derivative_contract_record`（FCN/期权交易）
- `watchlist.watchlist_item.instrument_id -> instrument_data.instrument.instrument_id`
- `watchlist.instrument_detail.instrument_id -> instrument_data.instrument.instrument_id`

这意味着：

- app 私有 schema 可以引用共享资产
- 共享资产重命名/修正要通过 共享资产数据 完成
- 本地脏数据会在 FK 迁移或写入时暴露，而不是长期静默漂移
- 普通交易最多引用一个 Instrument Data instrument，FCN/期权交易改为引用同 Portfolio 的本地合约；现金事实可以两者都不引用

## Data Flow

### Shared Instrument Maintenance

1. 在 `CLI 数据维护` 维护共享资产主档、identifier、价格、净值、FX
   支持手工录入、CSV/Excel 文件导入、邮件刷新，并能直接查看选中资产的共享市场数据与净值历史
2. 数据写入 `instrument_data`
3. `Watchlist` 和 `Portfolio` 直接从 `instrument_data` 读取

邮件通道先进入 `data_ingestion` 私有 ingestion state：逐目录按 `UIDVALIDITY + UID` 增量发现，仅对可能命中的邮件抓取正文与附件，并按附件 SHA-256 和 parser 版本去重。目录归档规则只是缩小候选范围；`INBOX` 仍是显式扫描目录，日频/周频只用于 freshness 判断，不能替代目录覆盖。解析结果必须先保留原始证据和匹配依据，再由严格 NAV 口径校验写入 Instrument Data。

基金在 Instrument Data 中只允许两种 NAV identity：`official_nav` 是单位净值，`total_return_nav` 是分红再投资后的复权累计净值。单位净值加历史现金分红的普通累计值不是回报指数，只能作为 摄取层私有原始证据，不能写成 `total_return_nav`。只有供应商明确提供可信复权序列，或系统拥有完整分红与再投资信息并完成可审计计算时，才写入复权累计净值；否则该字段保持 unavailable/NA，不使用单位净值、现金累计值或交易价格兜底。

Instrument Data 行情合同在数据库与 shared store 两层一致执行：point currency 必须等于 instrument
master currency，value 必须有限且大于零，status 必须 canonical；FX 还必须匹配维护中的
pair id、`fx/spot` identity 与 pair quote currency。消费者不得用 USD/base currency、另一币种
序列或本地旧行情替代不合格的 canonical observation。

Instrument Data 的 `quote_selection_policy` 必须显式持久化五个非空 role；shared store 读取、更新和
各消费者都不得动态补默认 role。类型默认 policy 仅是新建 instrument 时完整写入的领域规则。

### Watchlist Read Models

1. 用户把共享资产加入 watchlist
2. `watchlist` 持有自己的本地镜像和 read models
3. stale 检查发现 canonical 数据变化时，写 durable `recalc_job`
4. 后台 worker 消费 job，刷新本地 read model 和 canonical metadata mirror

Watchlist 的历史价格/NAV 计算只接受 Instrument Data 中符合 master currency 且 status=complete 的
序列，并严格按持久化 `quote_selection_policy` 选取；Instrument Data 无序列或无 policy 时结果为
unavailable。Watchlist-local `nav_fact` 只用于审计，不是行情计算输入。

### Portfolio Facts

1. 用户显式创建 portfolio / account / transaction，或用导入脚本导入
2. 普通市场资产交易引用 Instrument Data instrument；首笔 FCN/期权交易在 `portfolio` 内原子创建不可变 derivative contract，后续事件只引用同一 contract id
3. `portfolio` 只在自己的 schema 持久化账户、交易、合约、ledger 与组合计算事实
4. 普通市场资产的 valuation / holdings / charts / performance 使用 Instrument Data canonical market data；FCN/期权按事件记账，在事件之间使用成本/权利金负债口径，不进入实时定价、协方差、Risk Budget 或 Research 序列

## Non-goals

下面这些仍然不属于共享层：

- watchlist rows
- instrument detail read models
- monitoring assessments / investment research / manual profiles
- portfolios
- accounts
- transactions
- ledger postings / lots
- target sets / research runs

共享的是资产身份、经过校验的市场事实，以及解释这些事实何时应到达、来自何处所必需的非秘密 source descriptor / schedule；不是 app 业务语义。`SourceSettings` 中的 source mode、位置/profile、email rule 描述、expected frequency、market calendar 和 release lag 属于 Instrument Data 合同，因为所有消费者都需要同一 freshness/provenance 口径。真实邮箱口令、API token、抓取进程、原始附件、原始 NAV 证据、解析器、重试状态和后台维护命令仍属于 data_ingestion 私域，不得写入共享 Instrument Data 记录。

## Operational Rule

如果未来再新增功能，默认遵守这条判断：

- 如果是共享资产身份、共享市场事实，或这些事实的非秘密 provenance / 到达日程合同，优先放 `instrument-core + instrument_data`
- 如果是 后台抓取、证据、解析、重试或候选工作流，放入 `data_ingestion` schema
- 如果是某个 app 的工作流、派生读模型、研究判断、展示状态，必须留在 app 私域
