# Calculation Publication Spine Specification

状态：Phase 3B 实施契约

版本：2026-07-14

首个 producer：Portfolio Daily Valuation / Performance

## 1. 目标与不可妥协边界

本规范建立 Workbench 唯一的计算运行、输入冻结、持久任务和发布主干。首批范围可以只覆盖
Portfolio Daily，但主干生命周期必须可以直接承载后续 Portfolio Risk、Watchlist Analytics 和
Allocation Research，不允许增加 producer 时更换 run/manifest/publication 架构。

以下能力必须在首个 producer 同时完成：

- 财务会计链从交易 revision 到日频输出全程使用 Decimal/NUMERIC；
- 每次 run 在开始计算前冻结并 seal 完整输入；
- calculator 只读取 manifest 指定的 revision/snapshot，不回查 current facts；
- 任务持久化，具备去重、租约、心跳、重试和 fencing；
- 输出不可变，current publication 通过单事务原子切换；
- GET 纯读，不触发计算、等待、写库或 live fallback；
- 同一 manifest 与 methodology 产生相同 canonical output hash；
- mid-run 输入变化、worker 崩溃或陈旧 lease 不能发布错误 current。

严格历史浏览 UI、任意 revision cut-off 重建与复杂 reconciliation workflow 属于 Phase 3C，
不在本阶段。它们后置不影响已发布 run 按 sealed manifest 的确定性复算。

## 2. 模块与数据库边界

新增 `calculation_registry` PostgreSQL schema 和独立 Alembic chain。该 schema 只拥有跨领域稳定的
运行生命周期，不拥有估值、绩效或风险公式：

```text
calculation_registry
  calculation_scope_generation
  calculation_run
  calculation_input_manifest
  calculation_job
  calculation_publication
  calculation_current_publication
  calculation_recompute_intent

portfolio
  portfolio_calculation_*_input
  portfolio_daily_snapshot_version
  portfolio_daily_holding_version
  portfolio_daily_contribution_version
```

domain-specific dependency rows 与 outputs 留在 producer 的 schema；它们可以同时对本领域 revision
和 `calculation_registry` manifest/run 建立外键。这样新增 Watchlist producer 只需增加 Watchlist
dependency/output adapter，不把领域 payload 塞进通用 JSON，也不改变 registry 状态机。

迁移顺序固定为：

```text
instrument_registry -> calculation_registry -> portfolio -> watchlist
```

`migrate_all.sh`、空库 CI、备份/恢复、发布审计和本地服务安装器必须把第四条迁移链作为一个整体处理。
仍然是一个 PostgreSQL、一个 API 和一个 worker，不拆微服务。

## 3. Registry 数据模型

### 3.1 Scope generation

`calculation_scope_generation` 以 `(calculation_kind, scope_kind, scope_id)` 唯一标识计算范围，保存单调递增
`generation`。会影响当前输入的业务写入必须在同一数据库事务中：

1. 写入 fact revision；
2. 推进受影响 scope generation；
3. 创建 deduplicated recompute intent。

manifest 捕获 generation；publish CAS 必须再次确认 generation 未变化。只依赖时间戳、全局 watermark
或 `max(updated_at)` 不构成并发保护。

### 3.2 Run 与 manifest

`calculation_run` 至少保存：

- `run_id`；
- `calculation_kind / scope_kind / scope_id`；
- `requested_as_of / effective_as_of / cutoff_at / timezone`；
- `methodology_version / input_schema_version / output_schema_version`；
- `captured_generation`；
- `status / status_reason_code`；
- `requested_by / created_at / started_at / completed_at`；
- `manifest_id / published_output_hash / superseded_by_run_id`。

run 状态机是封闭集合：

```text
capturing -> queued -> running -> succeeded -> published
    |          |         |            |
    +----------+---------+------------+-> superseded
               |         |
               +---------+-> failed
```

只允许预先定义的转换。`published`、`superseded` 和 terminal `failed` 不可回退；重试增加 job attempt，
不把 terminal run 改回 running。输入变化创建新 run，不修改旧 manifest。

`calculation_input_manifest` 从 `building` 转为 `sealed` 后不可更新、删除，也不可再增加 dependency row。
dependency INSERT trigger 必须锁 manifest row；seal 与并发 INSERT 必须串行化，避免在 seal commit 后插入输入。
seal 时保存：

- canonical manifest hash；
- 每类 dependency count；
- captured generation；
- sealed_at；
- schema version。

数据库 trigger 禁止 sealed manifest 及其 dependency rows 的 UPDATE/DELETE。

### 3.3 Durable job 与 fencing

`calculation_job` 至少保存：

- `job_id / run_id / dedupe_key`；
- `queued / leased / retry_wait / succeeded / failed / superseded` 状态；
- `attempt / max_attempts / available_at`；
- `lease_owner / lease_expires_at / heartbeat_at`；
- 单调递增 `fencing_token`；
- 结构化 `failure_code` 与受限长度诊断文本。

worker 使用 `FOR UPDATE SKIP LOCKED` claim；每次取得或接管 lease 都推进 fencing token。heartbeat、写入输出、
完成 job 和 publish 均必须匹配当前 lease owner 与 fencing token。旧 worker 即使在超时后恢复，也只能失败，
不能覆盖新 worker 的结果。

相同 scope、as-of、generation、methodology 和 input schema 的 queued/running run 使用唯一 dedupe key。
瞬时失败且 generation 未变化时重试同一 sealed manifest；generation 已变化时旧 run superseded，创建新 run。

### 3.4 Immutable publication

domain output rows 的主键必须包含 `run_id`，写入后禁止 UPDATE/DELETE。`calculation_publication` 是不可变
发布记录，保存 run、manifest、output schema、canonical financial output hash 和 published_at。

`calculation_current_publication` 是唯一可变指针，以 `(calculation_kind, scope_kind, scope_id)` 为主键。
publish 在一个 `SERIALIZABLE` 或等价显式锁事务中完成：

1. 验证 run、manifest、job lease/fence 与 captured generation；
2. 验证全部输出、闭合不变量与 canonical output hash；
3. 插入 immutable publication；
4. CAS 更新 current pointer；
5. 将 run/job 标为 published/succeeded。

reader 只从 current pointer 进入输出，因此只能看到完整 old publication 或完整 new publication，绝不看到
部分新 run。失败、超时或 superseded run 不改变旧指针。

## 4. Portfolio Daily manifest

manifest capture 使用单一 `REPEATABLE READ` snapshot。typed dependency 至少包括：

- 每笔采用的 `transaction_id / transaction_revision_id / revision_number / payload_hash`；
- portfolio 与 account 的 canonical immutable configuration snapshot/hash；
- base currency、benchmark、taxonomy assignment 与适用 policy snapshot/hash；
- instrument identity/currency/corporate-action snapshot/hash；
- quote `series_id / observation_id / exact revision_id / status / value / observed_at`；
- quote selection-policy revision、consumer role、requested window、candidate selection/exclusion reason；
- FX 的 source/target currency、direct/inverse/cross path、每条 leg 的 series/observation/exact revision；
- valuation calendar、cut-off/timezone、freshness/cadence policy；
- 采用 suffix/incremental calculation 时的 exact prior publication id、run id 和 output hash。

只保存 aggregate fingerprint 不足以复算。config 没有 revision chain 时保存完整 canonical snapshot，
calculator 不得读取可变 current row。quote/FX dependency 同时登记 series window，使新增 observation 或
新 eligible revision 也能精确找到受影响 publication，而不仅能识别已采用 revision 的原地 supersede。

首版若无法证明 suffix calculation 与 prior publication 的依赖完备性，必须 full rebuild；不允许为了性能
隐式沿用旧输出。

## 5. 数值契约

### 5.1 Decimal 会计边界

新增唯一 `calculation_numeric` 模块，所有财务 calculator 在显式 local context 中运行：

```text
precision = 50
rounding = ROUND_HALF_EVEN
trap = FloatOperation, InvalidOperation, DivisionByZero, Overflow
```

输入 scale 沿用已发布账本契约：

- quantity / price：12 位小数；
- amount / fee / tax：8 位小数；
- FX rate：18 位小数。

内部 lot、cash、market value、cost、realized/unrealized P&L、income、fee、tax、FX、NAV、TWR 与 contribution
不做隐式 quantize。只有事实接收边界和发布字段边界按版本化字段 scale 做 HALF_EVEN。日频派生财务字段
使用明确的 NUMERIC scale，API 返回 plain canonical decimal string：禁止 scientific notation、NaN、Infinity、
`-0` 和 JSON number。

当前 `ledger.py`、`performance.py`、canonical quote/FX 转换和三张日频 Float 物化表必须在首个 producer
迁移中同时删除/替换；不能把旧 float calculator 包装进新 registry 后称为完成。

### 5.2 统计边界

只有 covariance、correlation、优化器和其他明确的统计线性代数边界允许转换为 float64。转换契约必须固定：

- instrument/series 排序；
- complete-case 与缺失值政策，禁止补零和隐式 pairwise mix；
- return frequency 与 sample covariance `n-1` 等估计量；
- PSD/regularization 方法；
- solver tolerance、迭代上限和 failure condition；
- 输出 canonicalization、允许误差和 methodology version。

统计不可解、样本不足、矩阵条件不满足或 solver 不收敛时返回 `Unavailable + reason code`，不返回近似成功。

### 5.3 Portfolio Daily 方法

日频 TWR 继续遵循 [MARKET_DATA_RELIABILITY_SPEC.md](./MARKET_DATA_RELIABILITY_SPEC.md) 的固定现金流时点：

```text
r_t = (MVE_t + CF_out,t) / (MVB_t + CF_in,t) - 1
```

外部流入视为期初，外部流出视为期末。没有可信日内时间戳时不宣称 true intraday TWR，也不切换
Modified Dietz。denominator `<= 0`、输入不完整、现金流边界估值 stale/carry-forward、FX 不可用或跨 broken
boundary 时 fail closed；删除现有用 `1e-9` 掩盖 binary-float 尾差的判断。fresh re-anchor 后才恢复新窗口。

每个 run 至少验证并记录以下闭合不变量：

- portfolio NAV 等于现金与全部可计量持仓/应计项目之和；
- holding/group/taxonomy market value、P&L 与 contribution 向上汇总到 portfolio；
- cash、cost、realized/unrealized、income、fee、tax 与 FX 影响按同一符号契约闭合；
- external flow neutral TWR 不因注资/赎回本身产生收益；
- 所有残差不超过输出 quantum；超限残差显式失败，不吸收到某一行。

## 6. API 与读写语义

命令 API：

```text
POST /api/calculations/portfolio-daily -> 202 + run_id(s)
GET  /api/calculations/runs/{run_id}   -> status/manifest/publication metadata
```

同 dedupe key 已 queued/running 时返回现有 run；API 不等待 calculator。旧同步
`/api/portfolios/snapshots/daily/refresh` 直接删除，不保留 alias。Platform market-data notification 改为提交
recompute intent。

所有 Portfolio Overview/Holdings/Performance/period/group/calendar/entry GET：

- 只读同一个 current `publication_id`；
- 初次没有 publication 时返回 `calculation_not_ready`；
- 新 run pending 时可以继续返回旧 publication，但必须携带 stale/pending metadata；
- 不调用 ensure/builder，不写数据库，不从 live ledger/quote 重建同形结果；
- response lineage 至少含 publication id、run id、manifest id、methodology version、as-of 和 reliability。

transaction/account/config write 删除 FastAPI `BackgroundTasks`，与 generation/recompute intent 同事务提交。

## 7. 迁移与运维

Portfolio breaking migration：

- 删除 `portfolio_calculation_state` runtime model/table；
- 删除进程内 refresh lock、request polling、dirty/status 双轨与 watermark-current 判定；
- 删除旧三张按 portfolio/date 原地覆盖的 materialization，创建按 run/version 的 NUMERIC outputs；
- 删除 `portfolio_record` 上 NAV/day-change/securities-count 等派生 header 权威字段；
- cache key 改为 immutable publication id/output hash；
- Alembic 历史文件保留，runtime 不保留兼容读写路径。

迁移本身不伪造 publication。发布编排器保持 writers 停止，升级四条 migration chain，启动受管 worker，
显式 enqueue 全组合 rebuild，等待 terminal 状态并运行零告警 post-migration audit；全部通过后才启动 API/Web。
失败时使用既有发布前备份回滚。

worker 必须进入 launchd/systemd 的生成、安装、停止、恢复、健康检查与日志轮转。API readiness 检查 migration
head 与 worker heartbeat；worker 不健康时 GET 仍可读取旧 publication，但 command readiness 明确降级。

## 8. 必须通过的测试

### Unit / property / golden

- canonical decimal/json/hash：row order 固定化，尾零与 `-0` 规范化，revision/policy/config/cutoff 任一变化改变 hash；
- `0.1` 重复累计、fractional quantity、超大/超小金额、HALF_EVEN tie；
- FIFO/average-cost、fee/tax、partial disposal、transfer、split/reverse split/cash-in-lieu；
- direct/inverse/cross FX 与 currency mismatch；
- external-flow-neutral TWR、broken/re-anchor 和 exact contribution roll-up；
- calculator 在所有 live resolver 被设置为 fail 时仍仅凭 manifest 重跑并得到相同 output hash。

golden case 必须来自可手算样例或独立参考实现；只用当前函数自身生成 expected value 无效。

### PostgreSQL integration

- NUMERIC scale、FK/check/state transition、sealed manifest 与 output immutability；
- concurrent enqueue dedupe、`SKIP LOCKED` claim、heartbeat takeover 与 lease fencing；
- fact write + generation + intent 的同事务回滚；
- mid-run transaction/quote/config revision 使旧 run superseded 并创建 replacement；
- retry same manifest 的 output hash 相同；
- 并发 reader 只看到完整 old 或 new publication；failed run 保留 old pointer；
- PostgreSQL JUnit 有 skip、零收集或损坏时 CI 必须失败。

### API / migration / operations

- POST 返回 202，GET run status；业务 GET 断言零写入且不调用 builder/ensure/live fallback；
- 所有组合页面与细分接口绑定同一 publication id；
- 空库升级到全部 head；带旧 materialization 的数据库升级后旧 state/header/path 确实消失；
- worker crash/restart、lease recovery、graceful stop 与发布/恢复 rollback；
- 全套 backend、frontend、PostgreSQL、migration-head 和 database-lifecycle gate。

## 9. 完成定义

只有当首个 Portfolio Daily producer、Decimal 精确内核、sealed manifest、durable worker、atomic publication、
GET 纯读和旧路径删除同时通过上述门禁，Phase 3B 的首个垂直切片才完成。仅新增表、仅记录 fingerprint、
仅把现有同步函数放进后台任务，或保留 live fallback，都不构成阶段完成。
