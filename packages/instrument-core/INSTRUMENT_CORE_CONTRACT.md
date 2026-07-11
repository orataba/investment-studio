# Instrument Core Contract

## 目标

为 `Watchlist` 与 `Portfolio` 提供最小共享资产底座。

## Shared Objects

### `InstrumentCore`

- `instrument_id`
- `instrument_name`
- `instrument_type`
- `currency`
- `identifiers[]`

### `InstrumentIdentifier`

- `identifier_type`
- `identifier_value`
- `is_primary`

Supported `identifier_type` values:

- `ticker`
- `exchange_ticker`
- `ts_code`
- `isin`
- `cusip`
- `sedol`
- `internal`
- `fund_name`
- `cash_currency`
- `other`

### `MarketDataPoint`

- `instrument_id`
- `metric_family`
- `quote_basis`
- `as_of_date`
- `value`
- `currency`
- `provider`
- `status`

### `QuoteSelectionPolicy`

- `trading[]`
- `valuation[]`
- `total_return[]`
- `chart[]`
- `reference[]`

### `CorporateActionEvent`

- `corporate_action_event_id`
- `instrument_id`
- `action_type`（当前为 `share_split`，正拆/反拆由 ratio 表达）
- `announcement_date`
- `record_date`（权益登记日 EOD）
- `effective_date`（生效日 BOD）
- `payable_date`
- `new_units / old_units`（每旧份对应的新份额比例）
- `quantity_rounding / quantity_precision`
- `cost_basis_treatment`（当前仅 `carry`）
- `status`（`detected | confirmed | cancelled`）
- `source / external_event_id / provenance`

`detected` 只用于数据质量提示，绝不能改变 Portfolio 数量。只有发行人、交易所或登记结算证据确认 ratio、日期和碎股处理后，事件才可进入 `confirmed` 并在账本生效。

当前共享层不再暴露一个粗粒度 `metric_code`，而是拆成：

- `metric_family`
  - `price`
  - `nav`
  - `fx`
- `quote_basis`
  - `last`
  - `close`
  - `adjusted_close`
  - `official_nav`
  - `total_return_nav`
  - `spot`
  - `clean_price`
  - `dirty_price`
  - `par`

`quote role` 不直接固化到每条 market data point 上，而是放进 `QuoteSelectionPolicy`。
原因是同一个 basis 往往会被多个读取场景复用；例如 `official_nav` 既可用于 valuation，也可作为 reference。

默认 selector 约定：

- `fund`
  - `valuation`: `official_nav -> close`
  - `total_return/chart`: `total_return_nav -> adjusted_close -> official_nav -> close`
- `equity`
  - `trading`: `last -> close`
  - `valuation`: `close -> last`
  - `total_return/chart`: `adjusted_close -> close -> last`
- `index`
  - `trading`: `close -> last`
  - `valuation`: `close -> last`
  - `total_return/chart`: `adjusted_close -> close -> last`
- `bond`
  - `trading`: `clean_price -> dirty_price`
  - `valuation`: `dirty_price -> clean_price`
- `cash`
  - `valuation/reference`: `par`
- `fx`
  - `valuation/trading/reference`: `spot`

## Non-goals

下面这些不属于 shared instrument core：

- watchlist row
- fund detail read model
- portfolio holdings
- transaction ledger
- risk snapshots
- portfolio period exports

## 当前消费方式

- `Watchlist` 用它承接 fund identity 和 canonical NAV / market data
- `Portfolio` 用它承接 instrument identity 和 role-based quote selection
- `Portfolio` 只把 `confirmed` corporate action 作为份额账本事件；`adjusted_close` 仅用于收益、风险和图表

但两个 app 的派生结果都必须在各自 app 内部完成。
