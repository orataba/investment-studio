# Portfolio Operations Workbench Platform Boundaries

## Current Topology

`Portfolio Operations Workbench` 现在是一个单仓、多 app、单 PostgreSQL 的结构：

- `apps/platform`
  平台入口和 Database Dashboard。
- `apps/watchlist`
  fund/ETF/index Watchlist / local detail / research rating / monitoring / recalc；Copilot 当前只保留后端扩展接口，不作为已发布 UI。
- `apps/portfolio`
  Portfolio / account / transaction / performance / risk / research / taxonomy。

数据库拓扑是：

- `instrument_registry` schema
- `watchlist` schema
- `portfolio` schema

不是：

- `platform` 作为共享数据 API
- `watchlist` / `portfolio` 通过 app-to-app HTTP 互相取数
- 一个 app 的 detail page 直接充当另一个 app 的上下文页

## Runtime Boundaries

### Platform

- 只直接读写 `instrument_registry`
- 提供平台首页、app registry 和 Database Dashboard API
- 可以下线；`watchlist` 和 `portfolio` 的核心读写路径不应受影响

### Watchlist

- 直接读写 `watchlist`
- 直接读取 `instrument_registry`
- 当前已发布范围收口为 `fund`、`etf` 与 `index` 资产类型；其他共享资产可以存在于 registry，但不进入 Watchlist 主工作面
- 在本地维护自己的 read models、recalc jobs、manual profile 和产品框架；Copilot 仅保留 backend extension boundary

### Portfolio

- 直接读写 `portfolio`
- 直接读取 `instrument_registry`
- 在本地维护自己的 ledger、lots、performance、risk、taxonomy、target set、research

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
- quote_series
- quote_observation
- quote_observation_revision
- registry metadata

行情值不再保存在可覆盖的扁平表中：series 定义稳定口径，observation 定义业务日期，
revision 以 append-only 方式保存值、来源证据、状态及更正历史。应用读取的扁平 market-data
DTO 只是 current complete revision 的投影，不是另一份事实表。

`instrument_registry` 的 Alembic 入口独立放在 [infra/instrument_registry](../infra/instrument_registry/README.md)，不再挂在 `platform` app 下。

## Storage Boundary

共享资产身份由数据库直接约束，而不是靠 app 约定：

- `portfolio.transaction_revision_record.instrument_id -> instrument_registry.instrument.instrument_id`

Portfolio 的交易真源是 append-only revision ledger；日常计算只读取
`portfolio.transaction_current` 最新有效视图。视图不是可写表，修订记录中的
instrument snapshot 只用于保存当时输入证据，canonical instrument identity 仍由
Instrument Registry 外键约束。
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

### Watchlist Read Models

1. 用户把共享资产加入 watchlist
2. `watchlist` 持有自己的本地镜像和 read models
3. stale 检查发现 canonical 数据变化时，写 durable `recalc_job`
4. 后台 worker 消费 job，刷新本地 read model 和 canonical metadata mirror

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

共享的是“资产身份和市场事实”，不是 app 业务语义。

## Operational Rule

如果未来再新增功能，默认遵守这条判断：

- 如果是共享资产身份或共享市场事实，优先放 `instrument-core + instrument_registry`
- 如果是某个 app 的工作流、派生读模型、研究判断、展示状态，必须留在 app 私域
