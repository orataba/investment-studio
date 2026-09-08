# Instrument Core Contract

## 目标

为 `Watchlist` 与 `Portfolio` 提供最小共享资产底座。

## Shared Objects

### `InstrumentCore`

- `instrument_id`
- `instrument_name`
- `instrument_type`
- `currency`
- `exchange_code`（`equity` 与 `etf` 必填；canonical MIC）
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
- `provider_symbol`

### `MarketDataPoint`

- `instrument_id`
- `metric_family`
- `quote_basis`
- `as_of_date`
- `value`
- `currency`
- `price_unit`（`per_unit | rate`）
- `price_scale`（Decimal；当前均为 `1`）
- `provider`
- `status`
- `nav_lineage`（仅 NAV；`provider_explicit | derived_dividend_reinvestment`）

NAV 对外只有两种 basis：`official_nav` 是单位净值，`total_return_nav` 是分红再投资复权
累计净值。普通累计净值（单位净值加历史现金分红）只作为 Platform 私域 raw evidence，不能写入
shared market data。`total_return_nav` 不存在时必须为 NA；不得回退到 `official_nav`、close 或
其他价格序列。派生复权净值必须记录算法版本、锚点日期和非空 evidence。

同一来源批次的 canonical price 与 raw OHLC 使用 `upsert_price_history` 在同一事务提交，
任一侧校验或持久化失败必须同时回滚价格、OHLC 和资产更新水位。单独修正某一事实表仍可使用
各自的 batch writer；相同事实重放不推进水位，不触发新的计算输入。

### `QuoteSelectionPolicy`

- `trading[]`
- `valuation[]`
- `total_return[]`
- `chart[]`
- `reference[]`

### `SourceSettings`

- `source_mode / source_email / source_location / source_api_profile / source_email_rules`
- `expected_frequency`: `daily | event_driven`
- `market_calendar`: 非空 calendar identifier 或 `null`；无法可靠推断 venue 时必须为 `null`
- `release_lag_days`: 非负整数，表示 observation date 后的预期可用日延迟
- `source_provider_currency / source_price_multiplier`: 供应商报价单位与写入 canonical price 前的乘数；仅在供应商单位与资产主币种不一致时使用

默认 cadence 按品种确定：`public_fund / private_fund` 为 `daily / lag 1`；equity、ETF、index 与 FX 为
`daily / lag 0`；cash 与 other 为 `event_driven / lag 0`。只有 `.SH/.SS`、`.SZ`、`.HK`、
`.L`、`.DE`、`.PA`、`.AS`、`.MI`、`.SW`
这类能从 canonical identifier 明确推断的 venue 才默认 calendar；其他品种保持 `null` 并由数据运营配置。

Tushare 场内基金 ingestion 的 complete raw `close` 必须有同日 complete
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

### Fund NAV 行为、证据、投影版本与累计因子

基金 NAV 复权账本是 append-only、可追溯的版本模型，不保存可覆盖的“当前事件”：

- `fund_nav_event`
  - `fund_nav_action_id` 是稳定行为 id；每条记录是一个不可变 revision。
  - revision 使用连续的 `revision_number`、`original | correction | cancellation` 和
    `supersedes_fund_nav_event_id` 构成严格单链，禁止分叉。
  - cancellation 必须原样保留前序业务 payload，只表达该 revision 已撤销。
  - 行为只保存 `cash_per_unit` 或非 1 的 `unit_ratio`，不保存复投净值、单次 multiplier 或可变 status。
  - 同日存在多项当前行为时，每项必须有唯一的正 `sequence_order`；缺失即停止投影。
  - `recorded_by / revision_reason / provenance` 为必填审计字段。
- `fund_nav_reinvestment_evidence`
  - 复投净值从行为本体分离，并绑定某个精确的 cash-distribution revision。
  - 同样采用不可变 original/correction/cancellation 单链；cancellation 必须保留前序数值。
- `fund_nav_projection_run`
  - 对 source observation fingerprint、当前行为 revision ids、当前复投证据 revision ids、算法、锚点和
    evidence 做 canonical fingerprint；run id 由该 fingerprint 确定。
  - run 冻结其输入 membership，旧 run 永久保留；`fund_nav_current_projection` 只原子切换当前指针。
  - `complete` run 必须有 anchor；无法证明复权时发布 `unavailable` run，anchor 与 factors 为空，
    并在 `evidence.unavailable_reason` 记录原因。
- `fund_nav_adjustment_factor`
  - factor 属于某个不可变 run，id 由 `run_id + factor_logical_key` 确定。
  - event-derived factor 通过 `previous_fund_nav_adjustment_factor_id` 组成唯一有序链；同日多行为也按
    `sequence_order` 逐项连接，不按日期覆盖。
  - factor 必须引用该 run 冻结的行为/evidence revision；默认读取只返回当前 run 的 factors，审计读取
    同时保留所有历史 runs/factors。

现金分红的单次乘数只在投影时计算为 `1 + cash_per_unit / reinvestment_nav`，份额拆分为
`unit_ratio`；不把这个派生值重复持久化到行为表。事件派生曲线从 level 1 的
`zero_cash_anchor` 或 `window_normalized_anchor` 开始沿唯一 predecessor chain 连乘。前者只表示累计现金差额已证实为零；后者表示未知的窗口前复权因子被约去，绝不证明历史无分红。管理人明确提供复权累计净值时，同日
`provider_implied` level 为 `total_return_nav / official_nav`。两种路径都必须满足：

`total_return_nav_t = official_nav_t × factor_level_t`

factor level 固定保留 18 位小数，materialized `total_return_nav` 固定保留 16 位小数；两者统一使用
正数 half-up 舍入，与 PostgreSQL `NUMERIC/ROUND` 的结果一致，避免运行时与数据库各自接受不同曲线。

每条 complete `total_return_nav` 必须引用当前 run 中的 factor logical key；instrument-core 解析为
确定性的 factor record id 和实际 level 后再原子写入。provider-explicit 行必须引用同日 provider
factor；event-derived 行不能跨过尚未进入因子链的行为。缺少复投净值、顺序、行为证据或任何必要
lineage 时，不生成错误累计净值，只发布 `unavailable` 并让 `total_return_nav` 保持 NA。

所有 event/evidence/run/membership/factor revision 都禁止物理 UPDATE/DELETE；修正与撤销只能追加新
revision，旧 run/factor 也不能被 current pointer 切换所清理。测试初始化用的 `reset_store` 不接受
projection ledger payload，也拒绝擦除已经存在的账本；需要重新初始化时应重建测试数据库。

普通累计净值跳变只能留在 Platform 私域作为待审 candidate，不能进入上述 canonical ledger。
整套 NAV 账本也不能复用 `CorporateActionEvent`：后者是会改变 Portfolio 实际持仓数量与成本基础的
security-master 事实；基金 NAV 分红再投只用于构造 TWR 指数，不代表投资者真实获得或再投资份额。
即使同一基金拆分最终也需要影响持仓，仍必须单独确认并发布为 `CorporateActionEvent`。

共享层使用下面两级 typed identity，不暴露粗粒度 `metric_code`：

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
  - `par`

`price_unit` 与 `price_scale` 是持久化事实和读取响应中的必填字段，不是写入命令参数。
共享 store 以 canonical instrument type、`metric_family` 与 `quote_basis` 作为唯一权威确定性派生：
FX 为 `rate / 1`，其余为 `per_unit / 1`。
批量写入若携带这两个派生字段会拒绝整批，避免调用方与共享 contract 形成第二套规则。
Python runtime 只支持当前仓库 migration head 所定义的 Instrument Registry schema，不会探测或兼容更早物理 schema；当前 revision 由 Alembic source 和 `migration-heads` gate 确定，不在合同里复制易漂移的编号。Registry 类型集合是 `public_fund | private_fund | etf | index | equity | crypto | cash | fx | other`。股票与 ETF 都必须持有 canonical MIC `exchange_code`；FMP 资产还必须持有 `provider_symbol` identity。支持的 listing MIC 为 `XNAS / XNYS / XASE / BATS / XHKG / XSHG / XSHE / XLON / XETR / XPAR / XAMS / XMIL / XSWX`，其中 `BATS` 只用于 ETF 目录。直接债券、FCN 与期权不进入共享资产表。FCN 与期权合约条款、生命周期和交易事实属于 Portfolio 私域；直接债券当前不在产品交易范围内。直接 SQL 也必须满足 canonical 行情、NAV lineage、基金行为状态机、计算输入与 broker identity 约束。

每条 market-data observation（不只 FX）都必须使用 instrument master currency，`value`
必须是有限正数，`status` 只能是 `complete | partial | unavailable`。共享 store 的单点、批量、
NAV replace 与 restore/reset 边界执行同一验证；缺 currency、错误 currency、零值、负值、
NaN/Infinity 和未知 status 都拒绝整次事务，不补 USD、不继承 master currency，也不把缺失
status 当成 `complete`。

FX identity 由 instrument-core 唯一维护：`fx-usd-hkd = USD/HKD`、
`fx-usd-cny = USD/CNY`、`fx-usd-eur = USD/EUR`、`fx-usd-gbp = USD/GBP`、
`fx-usd-chf = USD/CHF`。FX instrument 只能写入 `fx/spot`，其 instrument master
currency 与每条 observation currency 都必须等于 pair 的 quote currency，rate 必须是
有限正数。普通资产不能写 `fx/spot`，未知 FX instrument id 也不能由名称、ticker 或币种
猜测身份。共享 store 对单点和批量写入执行同一合同；读取到违反该合同的历史数据时必须返回
不可用结果，不得重标币种、补零、补日期、补 complete status 或回退到更旧数据。

五组维护中的 FX 主数据统一使用 FMP `historical-price-eod/full` 日线作为唯一 spot
时间序列来源。组合换算可以对这些 USD 交叉盘求逆或经 USD 枢轴计算，但不得混用手工汇率、
第二供应商数据或静默兜底。

`refresh_status.last_successful_requested_at` 是显式持久化 cursor；运行时不得从当前
status、mode 或 requested timestamp 反推、补写 cursor。

`QuoteSelectionPolicy` 的五个 role 都是非空必填持久化事实。未知 role、未知/重复 basis 以及
违反 role 语义的 policy 必须被拒绝；运行时读取和更新不按 instrument type 补 role 或替换空数组。
只有创建新 instrument 时，才把类型默认 policy 作为显式创建规则完整写入。

NAV history 的批量预览、导入与替换只支持 `public_fund` 和 `private_fund`。非基金资产必须在解析上传内容前拒绝，shared store 也必须在删除或写入前执行同一条断言；其他资产的行情通过 typed market-data 写入路径维护。

`quote role` 不直接固化到每条 market data point 上，而是放进 `QuoteSelectionPolicy`。
原因是同一个 basis 往往会被多个读取场景复用；例如 `official_nav` 既可用于 valuation，也可作为 reference。

默认 selector 约定：

- `public_fund` / `private_fund`
  - `valuation`: `official_nav -> close`
  - `total_return/chart`: `total_return_nav`（缺失即 NA，无 fallback）
- `equity`
  - `trading`: `last -> close`
  - `valuation`: `close -> last`
  - `total_return/chart`: `adjusted_close -> close -> last`
- `index`
  - `trading`: `close -> last`
  - `valuation`: `close -> last`
  - `total_return/chart`: `adjusted_close -> close -> last`
- `cash`
  - `valuation/reference`: `par`
- `fx`
  - `valuation/trading/reference`: `spot`

## Non-goals

下面这些不属于 shared instrument core：

- watchlist row
- public/private fund detail read model
- portfolio holdings
- transaction ledger
- risk snapshots
- portfolio period exports

## 当前消费方式

- `Watchlist` 用它承接公募、私募等 instrument identity 和 canonical NAV / market data；Watchlist taxonomy 只存放在 Watchlist 自己的 schema
- `Portfolio` 用它承接 instrument identity 和 role-based quote selection
- `Portfolio` 只把 `confirmed` corporate action 作为份额账本事件；`adjusted_close` 仅用于收益、风险和图表

但两个 app 的派生结果都必须在各自 app 内部完成。

### Native crypto research identity

`crypto` 是现货资产类型，与股票、ETF 和指数分别登记。当前维护 BTC/USD（`btcusd`，`ticker:BTCUSD`，`provider_symbol:fmp:BTCUSD`），不设证券交易所MIC，币种USD，`market_calendar=24/7`。现货价格使用已结束的UTC日线 close，收益为价格收益，不构造分红复权序列。Watchlist支持研究与价格工作面；Portfolio交易与衍生品underlying范围不因Registry新增类型而扩展。
