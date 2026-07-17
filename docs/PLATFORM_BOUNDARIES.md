# Portfolio Operations Workbench Platform Boundaries

## Current Topology

`Portfolio Operations Workbench` 现在是一个单仓、多 app、单 PostgreSQL 的结构：

- `apps/platform`
  平台入口和 Database Dashboard。
- `apps/watchlist`
  fund/index Watchlist / local detail / monitoring / recalc；Copilot 当前只保留后端扩展接口，不作为已发布 UI。
- `apps/portfolio`
  Portfolio / account / transaction / performance / risk / research / taxonomy。

数据库拓扑是：

- `instrument_registry` schema
- `platform` schema
- `watchlist` schema
- `portfolio` schema

不是：

- `platform` schema 作为共享事实源或跨 app 数据 API
- `watchlist` / `portfolio` 通过 app-to-app HTTP 互相取数
- 一个 app 的 detail page 直接充当另一个 app 的上下文页

## Runtime Boundaries

### Platform

- 直接读写 `instrument_registry` 中的共享资产事实
- 直接读写 `platform` 中的邮件抓取、原始证据、解析、重试和候选路由状态
- 提供平台首页、app registry 和 Database Dashboard API
- 可以下线；`watchlist` 和 `portfolio` 的核心读写路径不应受影响

### Watchlist

- 直接读写 `watchlist`
- 直接读取 `instrument_registry`
- 当前已发布范围收口为 `fund` 与 `index` 资产类型；其他共享资产可以存在于 registry，但不进入 Watchlist 主工作面
- 在本地维护自己的 read models、recalc jobs、manual profile 和产品框架；Copilot 仅保留 backend extension boundary

### Portfolio

- 直接读写 `portfolio`
- 直接读取 `instrument_registry`
- 在本地维护自己的 ledger、lots、performance、risk、taxonomy、target set、research

## Same-Name Page Boundary

Watchlist 与 Portfolio 会使用相同的投资术语，但这些页面不是同一个业务对象：

- Watchlist Instrument Detail 位于 `/instruments/:instrumentId`，Performance / Risk 面向单一 fund 或 instrument 的 NAV、price、benchmark 和单资产统计。
- Portfolio workspace 位于 `/portfolios/:portfolioId/...`，Overview / Performance / Risk / Research 面向组合账户、交易、现金流、持仓、TWR、归因、风险预算和规划求解。
- 两边可以遵守相同的视觉基线，但不得互用业务计算、页面 fixture、截图或验收结果。
- 修改或验收前必须同时确认 app、URL、源码目录与 API origin；页面标题相同不能证明页面身份相同。

## Shared Layer

当前共享层分成三部分：

### `packages/instrument-core`

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
- shared frontend styles

后续如果沉淀 layout、基础组件或设计系统，也优先放在这里，而不是复制到各 app。

### `instrument_registry` schema

承载共享资产主档和共享市场事实：

- instrument
- instrument_identifier
- instrument_market_data
- registry metadata

`instrument_registry` 的 Alembic 入口独立放在 [infra/instrument_registry](../infra/instrument_registry/README.md)，不再挂在 `platform` app 下。

## Platform Private Layer

### `platform` schema

承载 Platform 自己可重放、可审计的运行状态，不向 Watchlist 或 Portfolio 提供 canonical 市场事实：

- 每个邮箱目录及其 `UIDVALIDITY / UID` 高水位
- 邮件出现记录、附件内容哈希和消息到附件的关联
- 带 parser 版本、lease、重试与 dead-letter 状态的解析任务
- 尚未进入 Registry 的 NAV 候选和原始 NAV 证据
- 供应商原样提供的现金累计净值、复权口径声明与拒绝原因

该 schema 由 [apps/platform/backend/alembic](../apps/platform/backend/alembic) 独立迁移。口令、token 和附件二进制仍不进入数据库；附件表只保存内容哈希、元数据和受控存储引用。

## Storage Boundary

共享资产身份由数据库直接约束，而不是靠 app 约定：

- `portfolio.transaction_record.instrument_id -> instrument_registry.instrument.instrument_id`
- `watchlist.watchlist_item.instrument_id -> instrument_registry.instrument.instrument_id`
- `watchlist.instrument_detail.instrument_id -> instrument_registry.instrument.instrument_id`

这意味着：

- app 私有 schema 可以引用共享资产
- 共享资产重命名/修正要通过 canonical registry 完成
- 本地脏数据会在 FK 迁移或写入时暴露，而不是长期静默漂移

## Data Flow

### Shared Instrument Maintenance

1. 在 `Database Dashboard` 维护共享资产主档、identifier、价格、净值、FX
   支持手工录入、CSV/Excel 文件导入、邮件刷新，并能直接查看选中资产的共享市场数据与净值历史
2. 数据写入 `instrument_registry`
3. `Watchlist` 和 `Portfolio` 直接从 `instrument_registry` 读取

邮件通道先进入 `platform` 私有 ingestion state：逐目录按 `UIDVALIDITY + UID` 增量发现，仅对可能命中的邮件抓取正文与附件，并按附件 SHA-256 和 parser 版本去重。目录归档规则只是缩小候选范围；`INBOX` 仍是显式扫描目录，日频/周频只用于 freshness 判断，不能替代目录覆盖。解析结果必须先保留原始证据和匹配依据，再由严格 NAV 口径校验写入 Registry。

基金在 Registry 中只允许两种 NAV identity：`official_nav` 是单位净值，`total_return_nav` 是分红再投资后的复权累计净值。单位净值加历史现金分红的普通累计值不是回报指数，只能作为 Platform 私有原始证据，不能写成 `total_return_nav`。只有供应商明确提供可信复权序列，或系统拥有完整分红与再投资信息并完成可审计计算时，才写入复权累计净值；否则该字段保持 unavailable/NA，不使用单位净值、现金累计值或交易价格兜底。

Registry 行情合同在数据库与 shared store 两层一致执行：point currency 必须等于 instrument
master currency，value 必须有限且大于零，status 必须 canonical；FX 还必须匹配维护中的
pair id、`fx/spot` identity 与 pair quote currency。消费者不得用 USD/base currency、另一币种
序列或本地旧行情替代不合格的 canonical observation。

Registry 的 `quote_selection_policy` 必须显式持久化五个非空 role。0011 只在迁移时一次性
物化历史缺口；迁移后 shared store 读取、更新和各消费者都不得动态补默认 role。类型默认
policy 仅是新建 instrument 时完整写入的领域规则。

### Watchlist Read Models

1. 用户把共享资产加入 watchlist
2. `watchlist` 持有自己的本地镜像和 read models
3. stale 检查发现 canonical 数据变化时，写 durable `recalc_job`
4. 后台 worker 消费 job，刷新本地 read model 和 canonical metadata mirror

Watchlist 的历史价格/NAV 计算只接受 Registry 中符合 master currency 且 status=complete 的
序列，并严格按持久化 `quote_selection_policy` 选取；Registry 无序列或无 policy 时结果为
unavailable，不读取 Watchlist 旧 `nav_fact` 作为行情 fallback。

### Portfolio Facts

1. 用户显式创建 portfolio / account / transaction，或用导入脚本导入
2. `portfolio` 只在自己的 schema 持久化业务事实
3. valuation / holdings / charts / performance 使用 `instrument_registry` 的 canonical instrument 和 market data

## Non-goals

下面这些仍然不属于共享层：

- watchlist rows
- fund detail read models
- monitoring labels / research tags / manual profiles
- portfolios
- accounts
- transactions
- ledger postings / lots
- target sets / research runs

共享的是资产身份、经过校验的市场事实，以及解释这些事实何时应到达、来自何处所必需的非秘密 source descriptor / schedule；不是 app 业务语义。`SourceSettings` 中的 source mode、位置/profile、email rule 描述、expected frequency、market calendar 和 release lag 属于 Registry 合同，因为所有消费者都需要同一 freshness/provenance 口径。真实邮箱口令、API token、抓取进程、原始附件、原始 NAV 证据、解析器、重试状态和操作 UI 仍属于 Platform 私域，不得写入共享 Registry 记录。

## Operational Rule

如果未来再新增功能，默认遵守这条判断：

- 如果是共享资产身份、共享市场事实，或这些事实的非秘密 provenance / 到达日程合同，优先放 `instrument-core + instrument_registry`
- 如果是 Platform 抓取、证据、解析、重试或候选工作流，放入 `platform` schema
- 如果是某个 app 的工作流、派生读模型、研究判断、展示状态，必须留在 app 私域
