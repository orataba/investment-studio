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
  - `valuation`: `close -> adjusted_close -> last`
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
- review exports

## 当前消费方式

- `Watchlist` 用它承接 fund identity 和 canonical NAV / market data
- `Portfolio` 用它承接 instrument identity 和 role-based quote selection

但两个 app 的派生结果都必须在各自 app 内部完成。
