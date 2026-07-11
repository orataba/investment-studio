# Portfolio Operations Workbench Instrument Core

`instrument-core` 是 `Portfolio Operations Workbench` 当前唯一计划抽出来的最小共享业务底座。

## 当前范围

只承载下面这些跨 app 都会用到、且语义稳定的对象：

- `instrument_id`
- `instrument_name`
- identifiers
- `instrument_type`
  - 当前类型集合：`fund | etf | index | bond | equity | cash | fx | other`
- `currency`
- typed `market_data`
  - `metric_family`: `price | nav | fx`
  - `quote_basis`: `last | close | adjusted_close | official_nav | total_return_nav | spot | clean_price | dirty_price | par`
- `quote_selection_policy`
  - `trading`
  - `valuation`
  - `total_return`
  - `chart`
  - `reference`
- canonical `corporate_action_event`
  - record-date EOD / effective-date BOD
  - exact `new_units / old_units` ratio
  - fractional-unit treatment and provenance
  - `detected` 与 `confirmed` 分离；只有 confirmed 事件可改变组合份额

## 当前不放

- watchlist read models
- fund scoring
- portfolio
- account
- transaction
- ledger posting
- risk snapshots

## 目录

- `python/`
  Python 侧共享 contract 模型
- `ts/`
  TypeScript 侧共享 contract 类型
- `INSTRUMENT_CORE_CONTRACT.md`
  当前最小共享边界说明

## 原则

- `instrument-core` 只负责“资产身份 + typed market facts + 最小 quote selector policy + canonical corporate action facts”。
- 上层 app 必须自己 materialize 自己的 read models。
- `Watchlist` 和 `Portfolio` 都可以消费 `instrument-core`，但不能把自身业务对象塞回共享层。
- selector role 放在共享层，是因为不同资产类别读取 `valuation / trading / total_return / chart` 时需要稳定约定。
- corporate action 是共享 security master 事实；具体持仓调整、成本结转和现金替代仍由 Portfolio 账本负责。accrued interest、yield/spread 暂不进入共享 contract。
