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

`currency` 必须是显式的三位大写货币代码。Instrument Registry、QuoteSeries 与导入边界都不允许在缺失时默认成 USD；非 FX quote 的币种必须与 instrument currency 一致。

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
- `source_ref`
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

默认 policy 约定（列表顺序是显式的 role 内优先级，不是应用层 fallback）：

- `fund`
  - `trading`: `last -> close -> official_nav`
  - `valuation`: `official_nav -> close -> last`
  - `total_return`: `total_return_nav -> dividend_adjusted_nav -> reinvested_nav -> adjusted_close`
  - `chart`: `total_return_nav -> dividend_adjusted_nav -> reinvested_nav -> adjusted_close -> official_nav -> close`
- `etf`
  - `trading`: `last -> close`
  - `valuation`: `close -> last`
  - `total_return`: `adjusted_close`
  - `chart`: `adjusted_close -> close -> last`
- `equity`
  - `trading`: `last -> close`
  - `valuation`: `close -> last`
  - `total_return`: `adjusted_close`
  - `chart`: `adjusted_close -> close -> last`
- `index`
  - `trading`: `close -> last`
  - `valuation`: `close -> last`
  - `total_return`: `adjusted_close`
  - `chart`: `adjusted_close -> close -> last`
- `bond`
  - `trading`: `clean_price -> dirty_price`
  - `valuation`: `dirty_price -> clean_price`
  - `total_return`: 空（未建立真实总回报序列时明确 unavailable）
  - `chart`: `dirty_price -> clean_price`
- `cash`
  - `trading/valuation/chart/reference`: `par`
  - `total_return`: 空
- `fx`
  - `trading/valuation/chart/reference`: `spot`
  - `total_return`: 空

`total_return` 只允许真实总回报 basis；`close`、`last`、`official_nav` 只能出现在其他适用 role，
不得在收益、回撤或风险计算中代替总回报序列。Policy 缺失、候选冲突或选中序列不完整时，
canonical resolver 返回 typed unavailable。

## Canonical FX Window

`canonical_fx.py` 是估值换汇的 canonical 读取边界：

- registry migration `20260713_0010` 只预置 `fx-usd-hkd` 与 `fx-usd-cny` 的
  canonical reference identity、严格 spot policy 和 primary ticker；它不伪造 series、
  observation 或汇率。没有真实 spot observation 时，跨币种计算仍必须 unavailable。
- 调用方必须传入已有 SQLAlchemy `Session`、显式版本化 freshness policy、consumer policy version、币种对和日期窗口。
- source series 只通过 canonical quote resolver 的 `valuation` role 锁定一次，并强校验 `metric_family=fx`、`quote_basis=spot`、instrument type 与 quote currency。
- 当前支持 `USD/HKD/CNY` 的 direct、inverse、USD-cross 与同币种 identity；交叉腿可按 `USD/CCY` 或 `CCY/USD` 方向维护。
- `rate_at()` 只读取锁定窗口，不发 SQL、不切 series、不读取 flat instrument detail，也不使用系统当前日期。
- 较新的 `partial/rejected/withdrawn` current revision 会阻断较老的 complete observation；任何交叉腿 late、missing 或 unavailable，整个 cross 都 unavailable。
- 结果保留 `Decimal rate`、effective as-of、freshness/reliability/reason codes，以及每条腿的 point/window/policy/revision dependency。
- fingerprint 包含 requested as-of、consumer/freshness policy、路径顺序、运算方向和每条腿 dependency；`source_ref` 是 observation provenance，不是 FX series/path identity，但正式的 `revision_id/payload_hash` lineage 必须进入计算依赖。

Instrument Core 不替 Portfolio 决定 freshness 阈值。Portfolio 的估值 consumer 固定显式使用 5 个日历日，并以独立 consumer policy version 进入依赖指纹。

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
