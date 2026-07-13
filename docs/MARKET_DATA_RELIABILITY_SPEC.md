# Market Data, Freshness and Reliability Specification

状态：Phase 2 canonical specification
生效日期：2026-07-13

## 1. 目的

本文定义 Workbench 中市场数据、序列选择、时间状态和投资指标可靠性的唯一语义。Platform、
Watchlist、Portfolio 和未来 ETF Live 只可以共享这些基础语义，不能各自实现 quote fallback、
freshness 或可靠性判定。

目标不是让所有数字都可计算，而是确保每个可见数字回答四个问题：用了哪条序列和哪个修订、
数据对应什么业务日期、结果何时及按什么方法计算、为什么可以或不可以用于判断。

## 2. 四层对象

### 2.1 QuoteSeries

`QuoteSeries` 是 canonical 序列身份。当前 canonical identity 为：

```text
instrument_id + metric_family + quote_basis + currency
```

其中 `currency` 是显式三位大写事实，不存在隐式 USD。缺失或非法币种在写入 canonical identity 前即失败；非 FX series 必须与 instrument currency 一致。

`source_ref`、文件名、邮件名和导入批次属于 observation provenance，不进入 canonical series identity。
这是因为同一条人工维护净值序列可以由历史文件、后续邮件和人工更正共同维护；把每个 source reference
当成 series 会把一条连续的 canonical 序列错误切碎。

如果未来保存 provider-native raw feeds，应建立独立 `source_series`，再通过明确 adoption policy 形成
canonical series，不能改变本定义的含义。

Instrument master identity 也不能冒充行情事实。`20260713_0010` 仅预置 maintained
`USD/HKD`、`USD/CNY` 的 canonical FX instrument identity 与 spot role policy；迁移不得因此
创建空壳 `QuoteSeries`，更不得用固定值、迁移时间或当前 wall clock 伪造 observation。

### 2.2 QuoteObservation

`QuoteObservation` 只表示某个 series 在一个 `as_of_date` 上的逻辑观察。一个
`quote_series_id + as_of_date` 只能有一个 logical observation；它本身不保存可变值。

### 2.3 QuoteObservationRevision

值、source reference、source status、source published time 和 ingestion time 都属于 append-only revision。
同日数据更正必须：

1. supersede 旧 current revision；
2. append `revision_number + 1`；
3. 推进 series、instrument 和 registry 的 data version；
4. 使包含旧 revision 的 calculation manifest 失效。

相同 payload 的重试是 no-op。source 撤回或全量替换删除必须 append tombstone revision；不得物理删除历史，
也不得让被撤回的旧值继续作为 current observation。

旧数据无法证明 `ingested_at` 时必须为 `null` 并给出 `unknown_ingestion_time`，禁止用 migration time、
calculation time 或当前 wall clock 冒充。

### 2.4 CalculationRun 与 InputManifest

任何面向投资决策的持久化计算都绑定一个 immutable run 和 manifest。manifest 至少逐 role 记录：

- instrument id；
- quote series id；
- observation/revision 范围或精确 revision ids；
- quote policy revision；
- metric family、basis、currency 和 calculation frequency；
- FX direct/inverse/cross legs；
- requested/effective as-of；
- methodology version。

`current` 只表示 manifest fingerprint 与当前 adopted revisions、policy revision 和 methodology version 完全一致。
有 snapshot、有日期或任务曾成功都不能替代这个判断。

## 3. Quote role resolver

Registry 是 quote role 解析的唯一权威。角色包括：

- `valuation`：账本公允价值；禁止 total-return basis；
- `trading`：交易参考价格；
- `total_return`：收益、风险和回撤输入；
- `chart`：仅展示的价格路径；
- `reference`：非估值参考数据。

resolver 的输入是 instrument、role、policy revision、requested as-of 和允许的 source status；输出必须是一个
完整 series identity 或 typed unavailable。规则：

1. 只接受 policy 明确绑定或严格 selector 唯一解析出的 series；
2. metric family、basis、currency、series lifecycle 和 status 全部参与匹配；
3. `complete`、`partial`、`rejected`、`withdrawn` 不得混用；
4. 同日 revision 只采用 current adopted revision；
5. 多个候选、缺 policy、缺 series 或 currency 不一致均 fail closed；
6. 只有版本化 policy 明确声明的候选优先级可以决定 series；禁止按 observation/输入数组顺序、
   字典序、最新日期或应用内默认 basis 兜底；一个计算窗口选定 series 后不得逐日换 basis 拼接；
7. frequency 是 series QA metadata 和 calculation basis，不能靠混合多条 series 后“推断”为一个频率。

所有 app 必须删除自己的 selector；同一 resolver DTO 在 Watchlist、Portfolio 和未来集成中产生相同结果。

## 4. 四个正交状态

### 4.1 Observation freshness

描述业务观察相对 `requested_as_of / effective_as_of`、series cadence 和市场日历是否及时：

```text
current | late | missing | not_applicable
```

历史查询不能相对 today 判断。2025 年末的 observation 对 2025 年末查询可以 current，对今天的 current view
可以 late。

### 4.2 Ingestion freshness

描述 source pipeline 与修订进入系统的状态：

```text
current | delayed | failed | never_ingested | unknown
```

`last_attempt_at`、`last_success_at`、`source_published_at`、`ingested_at` 永远分开。失败后有 last-good 数据时，
ingestion 仍为 failed，不能因为可以展示旧值而改成 current。

### 4.3 Calculation freshness

描述 materialized result 与当前 manifest/methodology 的关系：

```text
current | stale | queued | running | failed | unavailable
```

读请求不得同步写库或重算来隐藏 stale。允许返回 last-good，但必须携带 `stale`、`stale_since`、reason code
和当前 job state；没有 last-good 时返回真正 unavailable。

### 4.4 Metric reliability

描述样本覆盖、数学可解性和业务适用性：

```text
reliable | qualified | unavailable
```

reliability 与 freshness 正交。旧但完整的历史数据可以 reliable；刚入库的数据也可能因缺锚点或样本不足而
unavailable。

### 4.5 Readable input watermark

Watchlist 的 read model 与 performance/risk snapshot 可记录
`market_data_input_watermark_at`。它只能从计算开始时、同一数据库 Session 读取的 canonical
`Instrument.market_data_updated_at` 解析得到；缺失或非法值必须为 `null`，并携带稳定的
`unknown` reason code，禁止回退为 `now()`。

该字段只是方便人阅读和排查的输入血缘，不参与选值、freshness 或 stale 权威判定。Canonical resolver
形成计算依赖时必须纳入 resolver/selection/consumer policy version、精确 revision ids、payload hashes，
并生成带算法前缀的 `sha256:<hex>` fingerprint；但这不等于每个下游物化结果都应复制完整逐点血缘。

Watchlist 的 chart、summary、performance、risk read model 只持久化有界 quote-resolution 摘要：series/
policy 身份、quality/freshness 状态、reason codes、首末日期、observation/adopted/excluded counts 和 dependency
fingerprint。摘要必须通过正向字段模型生成，并声明
`schema_version=watchlist_quote_resolution_summary.v1`；禁止通过“dump 全模型后排除几个已知数组”的负向
过滤生成，以免未来新增列表字段后悄然放大。Chart 的 `resolution`，Summary 的 `quote_resolution`，以及 Performance/Risk 的当前
`quote_resolution` 和 `historical_quote_resolution`，均不得保存 `observations`、`points`，也不得在
`calculation_dependency` 中保存 `revision_ids`、`payload_hashes`、`excluded_revision_ids` 或
`excluded_payload_hashes` 数组。需要逐 observation/revision 审计时，使用只读 `GET
/api/instruments/{instrument_id}/nav-series` 投影；该按需接口可以返回完整证据，不属于物化 read-model
体积边界。

当 current endpoint 不合格而系统保留 last-good performance/risk snapshot 时，当前
`quote_resolution` 必须反映本次解析，`historical_quote_resolution` 必须继续绑定生成该 snapshot 时的
有界摘要。两侧历史 fingerprint 不一致、摘要版本不明或清单损坏时，历史摘要必须置为 `null` 并返回稳定
reason code；禁止用本次窗口冒充旧 snapshot 的输入血缘。last-good read model 只能从明确白名单投影分析字段，
不得整包复制未知历史 JSON。

### 4.6 Domain freshness and peer-cohort publication

Chart、total return、performance、risk 和 peer comparison 是不同的发布域。每个域只能由自己的 role endpoint、
revision lineage 和 methodology 判定 freshness；一个域 current 不能让另一个域 current。特别是 chart
不得借用 total-return endpoint 或 summary 状态，最新 chart point 必须与已解析的 chart endpoint date
精确一致。

Peer comparison 只能使用同一 taxonomy node 中、同一 `as_of`、同一 resolved cadence、同一
performance/risk methodology 且成对快照完整的成员。Cohort fingerprint 必须包含成员集合、
taxonomy assignment 和每个成员的 performance/risk input hash。当任一成员或分类变化时，
仍引用旧 fingerprint 的全部已持久化 peer view 必须在同一事务中转为 `cohort_stale`，
并清空 ranking、percentile 和 category median。后续重算只有在成员收敛到同一 fingerprint
时才能再发布 current cohort。目标资产本身不 current 时，其 peer fields 必须清空，不展示
last-good ranking。

## 5. Response envelope

为避免列表响应膨胀，共享 lineage 放在 response-level `meta`，只有与默认状态不同的指标放在
`metric_quality`：

```json
{
  "data": {},
  "meta": {
    "requested_as_of": null,
    "effective_as_of": null,
    "observation": {
      "series_id": null,
      "as_of_date": null,
      "expected_frequency": null,
      "state": "missing",
      "reason_codes": ["missing_observation"]
    },
    "ingestion": {
      "source_published_at": null,
      "ingested_at": null,
      "last_attempt_at": null,
      "last_success_at": null,
      "state": "unknown"
    },
    "calculation": {
      "run_id": null,
      "state": "unavailable",
      "calculated_at": null,
      "input_fingerprint": null,
      "methodology_version": null,
      "stale_since": null,
      "failure_code": null
    },
    "reliability": {
      "state": "unavailable",
      "reason_codes": ["missing_observation"]
    }
  },
  "metric_quality": {}
}
```

API 返回 `null + reason code`；`—`、`Unavailable` 等展示文本只由统一前端组件生成。`null` 永不转成财务零值。

## 6. TWR valuation-boundary policy

当前系统使用**日估值 TWR，并显式采用固定现金流时点约定**：外部流入视为估值日
期初发生，外部流出视为估值日期末发生。当前交易事实只有日期、没有可信的日内时间戳，
因此系统不声称这是对任意日内现金流顺序的 `true TWR`：

```text
r_t = (MVE_t + CF_out,t) / (MVB_t + CF_in,t) - 1
```

| Valuation | External flow | Daily TWR | Risk observation | Linked period crossing date |
| --- | --- | --- | --- | --- |
| fresh complete | none | available | eligible when market observation exists | available |
| fresh complete | contribution/withdrawal | available | eligible when market observation exists | available |
| cadence/calendar carry-forward | none | link may carry; no invented market return | ineligible unless another position has a fresh market observation | qualified/available if no broken boundary |
| cadence/calendar carry-forward | any external flow | unavailable | ineligible | unavailable |
| resolver-late price or FX | any external flow | unavailable | ineligible | unavailable |
| incomplete/missing | external flow or unknown flow conversion | unavailable | ineligible | unavailable |

表中的 `stale` 指 resolver 依据版本化 cadence / market calendar / source state 判定的
`observation freshness = late`，不是简单的 `observation_date < valuation_date`。周末、节假日以及
按约定周频或月频发布的基金净值可以是正常 carry-forward；它们需要质量标识并退出日频风险样本，
但不能仅因自然日差异自动断开没有外部现金流的 TWR。若 carry-forward 日恰好发生外部出入金，
即使该 carry 符合预期 cadence，也没有 cash-flow boundary 所需的 fresh valuation，必须以
`carried_forward_valuation_on_external_flow` 断链；不能为了避免 stale 标签而继续复合。

发生 unreliable cash-flow boundary 后：

1. 当日标记 `stale_valuation_on_external_flow`；
2. 不从该 stale NAV 继续生成次日收益；
3. 第一笔 fresh complete valuation 只建立新 anchor；
4. 后续有效 sub-period 才恢复；
5. 跨断点窗口的 cumulative/annualized TWR 和 drawdown 均 unavailable；从 re-anchor 开始的新窗口可计算。

系统不静默切换 Modified Dietz、旧值携带或缺失日跳过复合。若未来记录可信的现金流
时间戳并支持日内估值，必须新建版本化的现金流时点政策与计算版本，不能回写解释既有结果。

## 7. Stable reason codes

首批 reason code：

```text
missing_observation
late_observation
unknown_ingestion_time
partial_series
rejected_observation
withdrawn_observation
ambiguous_quote_series
missing_quote_policy
currency_mismatch
missing_anchor
stale_valuation_on_external_flow
carried_forward_valuation_on_external_flow
incomplete_valuation_on_external_flow
awaiting_fresh_valuation_anchor
fresh_valuation_reanchor
invalid_return_denominator
crosses_broken_twr_boundary
stale_valuation_without_external_flow
carried_forward_valuation_without_external_flow
insufficient_history
unaligned_dates
missing_fx
stale_fx
missing_benchmark
calculation_pending
calculation_failed
methodology_outdated
input_revision_changed
```

reason code 是 API contract；展示文案可本地化，不能把自由文本当状态。

## 8. Migration and acceptance invariants

每个 Phase 2 migration 必须证明：

1. legacy observation 数量与 revision-1 backfill 数量一致；
2. 每个 logical observation 恰有一个 current revision；
3. legacy `ingested_at` 全部为 null；
4. deterministic ids 在 SQLite/PostgreSQL 和重复演练中一致；
5. no-op ingestion 不推进 watermark，值/source/status 变化和晚到旧日期修订会推进；
6. withdrawn observation 不出现在 adopted market data，但 revision history 可查；
7. policy-only 与 methodology-only 变化会使依赖 calculation stale；
8. Watchlist 和 Portfolio 对同一 role/as-of 得到相同 series/revision；
9. 前端不再包含 annualization、covariance、Sharpe、drawdown、risk contribution 等投资计算；
10. 空库升级、真实库迁移、数据审计、PostgreSQL integration、前端构建和恢复演练全部通过。
11. Watchlist fund consumer 使用 45 个日历日 freshness window，其他 canonical 类型使用 5 日；分类只看 canonical `instrument_type`。
12. late/missing 重算不得删除 last-good snapshot；current/trailing 指标不可用与 historical availability 必须分开表达。
13. 水位列语义改名在 SQLite/PostgreSQL 逐值保持；删除零行重复 facts 边界明确不可逆，回滚依赖 migration 前备份。
14. amount-only AUM 不得进入 read model 或前端；只有 amount、currency、as-of、source/revision lineage 和统计 grain 同时明确时，才允许以新的版本化事实模型重新引入。

## 9. Performance baseline

本机、local PostgreSQL、六个 launchd 服务运行状态，2026-07-13 Phase 2A 迁移前；选择当前 observation
最多的 instrument `sj3438`，每个 endpoint 顺序请求 20 次：

| Endpoint | P50 | P95 | Max |
| --- | ---: | ---: | ---: |
| Platform instrument detail | 76.8 ms | 99.6 ms | 172.6 ms |
| Watchlist instrument summary | 73.3 ms | 92.2 ms | 141.1 ms |

Phase 2A 实库迁移后必须使用同一方法复测。任何显著退化必须解释并通过索引、批量加载或 read projection
解决，不能以短 TTL cache 掩盖错误查询模型。
