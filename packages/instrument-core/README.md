# Portfolio Operations Workbench Instrument Core

`instrument-core` 是 `Portfolio Operations Workbench` 当前唯一计划抽出来的最小共享业务底座。

## 当前范围

只承载下面这些跨 app 都会用到、且语义稳定的对象：

- `instrument_id`
- `instrument_name`
- identifiers
- `instrument_type`
  - 当前类型集合：`public_fund | private_fund | etf | index | equity | cash | fx | other`
- `exchange_code`
  - 仅股票必填，使用 canonical MIC；其他类型必须为空
- `currency`
- typed `market_data`
  - `metric_family`: `price | nav | fx`
  - `quote_basis`: `last | close | adjusted_close | official_nav | total_return_nav | spot | par`
  - 公募和私募 NAV 对外仅 `official_nav`（单位净值）与带强 lineage 的 `total_return_nav`（分红再投资复权累计净值）
  - source cadence: `expected_frequency`, optional `market_calendar`, and non-negative `release_lag_days`
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
- internal fund NAV adjustment ledger
  - immutable `fund_nav_event` revision chains: `cash_distribution | unit_split`
  - immutable reinvestment-evidence revision chains bound to exact event revisions
  - deterministic projection runs that freeze current revision membership; old runs are retained
  - per-run cumulative factors: `provider_implied | event_derived`, with explicit predecessor links
  - 普通累计净值差只留在 Platform raw/candidate 域，不能进入 canonical ledger

## 当前不放

- watchlist read models
- fund scoring
- portfolio
- account
- transaction
- ledger posting
- risk snapshots
- direct bond instruments（不进入 Registry / Watchlist，当前也没有 Portfolio 交易入口）
- FCN / option contract terms and lifecycle events（由 Portfolio 本地持有）

## 目录

- `python/`
  Python 侧共享 contract 模型
- `ts/`
  TypeScript 侧共享 contract 类型
- `INSTRUMENT_CORE_CONTRACT.md`
  当前最小共享边界说明

## 原则

- Python runtime 要求 Instrument Registry 已迁移到 `20260818_0025`；该 head 将基金拆成 `public_fund / private_fund`，并为股票增加 canonical `exchange_code`，同时完整包含 observation/FX、NAV lineage、区间归一化收益锚点、基金行为账本、计算输入与 broker identity。运行时不探测或兼容更早物理 schema。
- market-data 写入命令只提交 instrument identity、metric/basis 与观测值；共享 store 唯一负责派生并持久化必填的 `price_unit / price_scale`，读取响应不得省略它们。
- 每条 market-data observation 必须显式提交 canonical `currency / status` 与有限正数 value；API、批量写入和 restore 都不推断缺失 status 或 currency。
- `quote_selection_policy` 的五个 role 都必须完整、非空持久化；0011 一次性物化历史缺口，此后运行时不再补 role。类型默认只在创建新 instrument 时显式写入。
- FX instrument/pair/quote-currency mapping 只在 Python `fx_contract` 中维护；FX 仅允许 canonical `fx/spot` 有限正数 observation，读取不修补错误币种或无效 rate。
- NAV history 批量导入只面向 `public_fund / private_fund`；其他类型在文件解析和持久化前统一拒绝，不提供兼容路径。
- 公募和私募的 `total_return/chart` 只允许 `total_return_nav`，缺失时返回 NA；不回退单位净值或价格。现金累计净值只留在 Platform raw evidence。
- complete `total_return_nav` 必须引用当前 projection run 的确定性 factor，且严格满足 `total_return_nav = official_nav × factor_level`；缺少复投证据时发布 unavailable run，不写猜测值。
- 基金 NAV 发布以 source watermark 做乐观并发控制，并在单事务内追加 revisions、冻结 run、写 factors/曲线和切换 current pointer；精确重放不更新 watermark。
- NAV ledger revisions 和历史 runs/factors 禁止物理更新或删除；修正、撤销和重投影一律追加新记录。
- `instrument-core` 只负责“资产身份 + typed market facts + 最小 quote selector policy + canonical corporate action facts”。
- 上层 app 必须自己 materialize 自己的 read models。
- `Watchlist` 和 `Portfolio` 都可以消费 `instrument-core`，但不能把自身业务对象塞回共享层。
- selector role 放在共享层，是因为不同资产类别读取 `valuation / trading / total_return / chart` 时需要稳定约定。
- corporate action 是共享 security master 事实；具体持仓调整、成本结转和现金替代仍由 Portfolio 账本负责。
