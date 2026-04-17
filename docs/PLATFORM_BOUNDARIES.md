# Yungu Platform Boundaries

## 目标

`Yungu` 是一个平台，下面有两个独立 app：

- `Watchlist`
- `Portfolio`

当前阶段不做业务融合，只做平台化整理与最小共享抽取。

## Shared

当前只计划共享最小资产核心层：

- `asset_id`
- `asset_name`
- identifiers
- `asset_type`
- `currency`
- `price`
- `nav`
- `fx`

后续可视情况再纳入：

- very basic asset master metadata
- common document/source plumbing
- common copilot infrastructure

## App Private

### Watchlist

保留私域对象：

- watchlists
- watchlist views
- field registry
- instrument attributes
- fund manual profiles
- fund scoring
- watchlist read models
- watchlist/detail copilot context

### Portfolio

保留私域对象：

- portfolios
- accounts
- transactions
- ledger postings
- lots
- target sets
- portfolio snapshots
- risk snapshots
- period risk summaries
- review packs

## 共享原则

- 共享数据源，不共享业务语义。
- 共享主键，不共享 app 私域对象。
- 共享技术底座，不共享业务读模型。
- 共享设计语言，不共享页面职责。

## 资产详情原则

未来资产详情必须分层：

- shared asset core
- watchlist context
- portfolio context

不允许用一个 app 的 detail page 直接替代另一个 app 的上下文页面。
