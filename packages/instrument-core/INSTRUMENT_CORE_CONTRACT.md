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
- `price_unit`（`per_unit | percent_of_par | rate`）
- `price_scale`（Decimal；分别为 `1 | 0.01 | 1`）
- `provider`
- `status`

### `QuoteSelectionPolicy`

- `trading[]`
- `valuation[]`
- `total_return[]`
- `chart[]`
- `reference[]`

### `SourceSettings`

- `source_mode / source_email / source_location / source_api_profile / source_email_rules`
- `expected_frequency`: `daily | weekly | monthly | event_driven`
- `market_calendar`: 非空 calendar identifier 或 `null`；无法可靠推断 venue 时必须为 `null`
- `release_lag_days`: 非负整数，表示 observation date 后的预期可用日延迟

默认 cadence 按品种确定：fund 为 `daily / lag 1`；equity、ETF、index、bond 与 FX 为
`daily / lag 0`；cash 与 other 为 `event_driven / lag 0`。只有 `.SH`、`.SZ`、`.HK`
这类能从 canonical identifier 明确推断的 venue 才默认 calendar；其他品种保持 `null` 并由数据运营配置。

Tushare listed-security ingestion 的 complete raw `close` 必须有同日 complete
`adjusted_close`。若 adjustment factor 不可用，raw close 只能以 `partial` 写入，refresh status
也必须为 `partial` 并列出缺失日期；不得静默发布孤立的 complete close。

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
  - `accrued_interest`（仅 `bond + price`；作为 clean price 的组成项，不能进入 selector role）

`price_unit` 与 `price_scale` 是持久化事实和读取响应中的必填字段，不是写入命令参数。
共享 store 以 canonical instrument type、`metric_family` 与 `quote_basis` 作为唯一权威确定性派生：
bond price 为 `percent_of_par / 0.01`，FX 为 `rate / 1`，其余为 `per_unit / 1`。
批量写入若携带这两个派生字段会拒绝整批，避免调用方与共享 contract 形成第二套规则。
Python runtime 只支持 Instrument Registry head `20260715_0011`，不会探测或兼容缺少数据库级行情合同与并发互斥的旧物理 schema。0011 保留 0010 的 per-instrument advisory mutex，并在直接 SQL 写入层强制 observation status、有限正数 value、master/point currency 一致，以及 maintained FX identity。

每条 market-data observation（不只 FX）都必须使用 instrument master currency，`value`
必须是有限正数，`status` 只能是 `complete | partial | unavailable`。共享 store 的单点、批量、
NAV replace 与 restore/reset 边界执行同一验证；缺 currency、错误 currency、零值、负值、
NaN/Infinity 和未知 status 都拒绝整次事务，不补 USD、不继承 master currency，也不把缺失
status 当成 `complete`。

FX identity 由 instrument-core 唯一维护：`fx-usd-hkd = USD/HKD`、
`fx-usd-cny = USD/CNY`。FX instrument 只能写入 `fx/spot`，其 instrument master
currency 与每条 observation currency 都必须等于 pair 的 quote currency，rate 必须是
有限正数。普通资产不能写 `fx/spot`，未知 FX instrument id 也不能由名称、ticker 或币种
猜测身份。共享 store 对单点和批量写入执行同一合同；读取到违反该合同的历史数据时必须返回
不可用结果，不得重标币种、补零、补日期、补 complete status 或回退到更旧数据。

`refresh_status.last_successful_requested_at` 是显式持久化 cursor。0011 会在表锁内一次性把旧
email success 的 `requested_at` 提升为该 cursor；运行时不再从当前 status/mode/requested_at
反推旧 cursor。

`QuoteSelectionPolicy` 的五个 role 都是非空必填持久化事实。0011 会一次性物化旧记录中缺失
或空的 role，并在前置检查中拒绝未知 role、未知/重复 basis 以及违反 role 语义的 policy；
运行时读取和更新不再按 instrument type 补 role 或替换空数组。只有创建新 instrument 时，
才把类型默认 policy 作为显式创建规则完整写入。

NAV history 的批量预览、导入与替换只支持 `instrument_type=fund`。非基金资产必须在解析上传内容前拒绝，shared store 也必须在删除或写入前执行同一条断言；其他资产的行情通过 typed market-data 写入路径维护。

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
