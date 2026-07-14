# Canonical FX Integration

## 已落地的计算边界

Portfolio 的 FX consumer 边界位于 `portfolio_app/services/canonical_fx.py`，并固定为：

- consumer policy version：`portfolio_fx_consumer.v1`
- freshness mode：`calendar_day_carry_forward`
- maximum age：5 calendar days
- quote role：`valuation`
- canonical identity：`metric_family=fx`、`quote_basis=spot`

底层 resolver 位于 Instrument Core 的 `canonical_fx.py`。它支持 identity、direct、inverse 与有序 USD-cross，并在调用方已有 SQLAlchemy Session 内锁定 source legs。窗口创建后，任意日期的 `rate_at()` 都是纯内存、零 SQL 的确定性计算。

以下 Portfolio consumer 已完成替换：

- daily snapshots、current holdings、period boundary holdings；
- performance、period calculation、contribution、group、calendar 与 entries；
- account workspace 的 cash、pending settlement 与 position base valuation；
- ledger position valuation使用同一 canonical valuation quote state，不再读取 flat instrument detail。

生产计算路径不再读取 Platform flat FX payload，也不保留旧的 rate map、detail cache、任意历史最近值或默认本币兜底。

## 同一计算快照

一次计算先从组合、账户、交易、持仓事实收集完整且去重的 currency pair，再在同一 Session 中锁定 canonical valuation quote 与 canonical FX book。所有日循环、分组和账本聚合只读取该内存快照。

增量 daily snapshot 虽然可以只重新渲染近期日期，但累计 income、fees、flows 与 realized gains 会重放历史交易。因此 FX book 的开始日期自动扩展到计算终点之前最早可达的 transaction effective date；不能用近期 seed date 截断累计账务所需汇率。

## 事实币种与数值规则

Portfolio persisted fact currency 必须已经是支持集合内的 canonical uppercase 值。计算层不会 trim、uppercase、猜测 instrument currency，亦不会用 portfolio base currency 替代缺失值。非 canonical 事实会在估值前抛出领域 data-integrity error，并由 API 映射为明确的 422 或 refresh failure。

采用的 FX rate 始终保持 `Decimal`；金额使用 `Decimal(str(amount)) * rate` 后才在现有 API 数值边界转为 float。`Transaction.fx_rate` 是成交事实，不是市场估值 FX，二者不能互相覆盖或兜底。

## 路径、修订与时效语义

- identity：rate 为 Decimal `1`，effective as-of 等于 requested as-of。
- direct：采用直接维护腿。
- inverse：对直接维护腿取倒数，inversion 进入 dependency fingerprint。
- USD-cross：按固定顺序记录 base leg 与 quote leg；任一腿失败，整个结果 unavailable。
- 第 5 个 calendar day 内允许 carry，结果为 resolved / qualified；第 6 天起 unavailable。
- 最新 applicable revision 为 partial、rejected 或 withdrawn 时，必须阻断更早 complete revision。
- missing、late 或 unavailable 不得回退到旧 complete observation、另一条 series、`date.today()`、1.0 或手工成交汇率。

短期 calendar carry 与 resolver-explicit stale 必须分开表达。前者可形成 qualified TWR link；有 external flow 时仍必须断链。不能仅因 effective observation date 早于 requested date，就把合法周末、节假日或基金发布节奏统一标成 stale。

## 性能边界

锁窗查询量与 calendar day 数量无关：

- 单一 source leg：1 次 instrument identity 批量读取、1 次 series selection、1 次 observation window 读取，共 3 次 SELECT；
- 两个唯一 source legs：固定上限 5 次 SELECT；
- 窗口创建后的 `rate_at()`：0 次 SQL。

Portfolio integration test 已验证同一 HKD cash calculation 从 5 个快照日扩展到 90 个快照日时，FX 查询均固定为 3 次 SELECT。

## Fail-closed 输出

无法采用 FX 时：

- 当前 cash、pending settlement 或 position market value 的 base value 为 null；
- 相关 valuation coverage 进入 partial / unavailable；
- 外部现金流日无法可靠换算时，TWR link 断开；
- 历史 FX 归因不完整时，currency-gain 与依赖它的 total P&L 保持 null。

fair-value NAV coverage、book P&L coverage 与 TWR reliability 是三个不同状态。Portfolio Daily exact output 分别保存对应 coverage/reason contract；旧 materialization 已由 `0039` 删除，不存在 runtime compatibility translation。

每个 sealed run 的 typed input manifest 按 currency pair 保存 canonical identity/direct/inverse/cross path、逐 leg observation/revision/payload lineage、effective rate、derivation residual 与 consumer policy；相同事实窗口必须产生相同 canonical manifest hash，任何 source revision 或路径变化都会改变 hash。该 lineage 只证明本次计算依赖，不把 unavailable FX 伪造成 resolved。

## 验证

```bash
uv run --project apps/portfolio/backend --extra test pytest -q \
  apps/portfolio/backend/tests/test_canonical_fx_foundation.py \
  apps/portfolio/backend/tests/test_ledger_read_performance.py \
  apps/portfolio/backend/tests/test_portfolio_daily_capture_fx.py
```
