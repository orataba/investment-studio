# Calculation Registry Schema

这里维护 `calculation_registry` schema 的独立 PostgreSQL Alembic chain。它只拥有计算范围代际、
run/manifest/job/publication 生命周期、重算意图和 worker 心跳，不拥有任何组合、行情或研究领域的
dependency/output payload。

## 迁移

```bash
(
  export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=portfolio_ops
  cd infra/calculation_registry
  alembic upgrade head
  alembic current --check-heads
  alembic check
)
```

迁移只支持 PostgreSQL，且数据库目标有两次独立校验：连接 URL 中的数据库名必须匹配
`PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE`，建立连接后还会用 `current_database()` 再次确认。
schema 固定为 `calculation_registry`，不会回退到 `public` 或 SQLite。

连接环境变量按优先级为：

1. `PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL`
2. `PORTFOLIO_OPS_CALCULATION_REGISTRY_DATABASE_URL`
3. `alembic.ini` 中的 `sqlalchemy.url`

## Producer dependency 合约

领域 dependency 表留在各 producer schema。每张 dependency 表必须：

- 有非空 UUID `manifest_id`，并以 `ON DELETE RESTRICT` 外键指向
  `calculation_registry.calculation_input_manifest(manifest_id)`；
- 安装 `calculation_registry.guard_manifest_dependency_mutation()` 作为
  `BEFORE INSERT OR UPDATE OR DELETE FOR EACH ROW` trigger；
- 安装 `calculation_registry.reject_truncate()` 作为
  `BEFORE TRUNCATE FOR EACH STATEMENT` trigger；
- 在 seal manifest 之前完成全部写入，并将稳定排序后的逐行内容纳入 canonical manifest hash。

该 trigger 会调用 `assert_manifest_building(manifest_id)` 并对 manifest 行执行 `FOR UPDATE`。
因此 dependency mutation 与 seal 对同一行串行化：seal 提交后不可能再成功插入、修改或删除 dependency。
不要仅在应用层检查 manifest 状态。

seal transaction 必须先调用 `assert_manifest_building(manifest_id)` 取得同一行锁，再在**同一事务**内读取
全部 typed dependencies、计算 canonical hash/counts 并执行 `building -> sealed`。如果先在事务外计算 hash、
最后才 UPDATE manifest，已提交的并发 dependency 可能漏出 hash，属于错误实现。

示例：

```sql
CREATE TRIGGER trg_portfolio_daily_input_manifest_guard
BEFORE INSERT OR UPDATE OR DELETE ON portfolio.portfolio_daily_transaction_input
FOR EACH ROW
EXECUTE FUNCTION calculation_registry.guard_manifest_dependency_mutation();

CREATE TRIGGER trg_portfolio_daily_input_truncate_guard
BEFORE TRUNCATE ON portfolio.portfolio_daily_transaction_input
FOR EACH STATEMENT
EXECUTE FUNCTION calculation_registry.reject_truncate();
```

## Producer output 合约

领域 output 表必须把 `run_id`、实际写入 worker identity 和 `output_fencing_token` 固化为 lineage，且
primary/unique identity 必须包含 `(run_id, output_fencing_token, ...natural key...)`。
它的 `BEFORE INSERT` trigger 必须调用：

```sql
PERFORM calculation_registry.assert_run_output_writable(
    NEW.run_id,
    NEW.output_fencing_token,
    NEW.worker_id
);
```

该函数会同时锁 run/job 行，并要求 run 仍为 `running`、job 仍为 `leased`、owner/token 精确匹配且
lease 尚未过期。output 表还必须用领域 trigger 禁止 UPDATE/DELETE，并安装通用
`reject_truncate()`；不能依赖 worker 在应用层自行判断 lease。

不同 attempt 的 output 永不互相覆盖。旧 worker 部分提交后崩溃时，旧 token 行作为不可变诊断记录保留；
新 worker 用更高 token 写入同一 natural key。`calculation_publication.published_fencing_token` 必须等于
succeeded job 的最终 token，reader 必须以 `(run_id, published_fencing_token)` 精确选择 output。
canonical financial output hash **不包含** fencing token，因此相同 manifest/methodology 的成功重试仍应
得到同一个内容 hash；但旧 token 的 partial rows绝不能进入 closure、hash 或 reader。

## 状态与不可变性

- manifest 只允许 `building -> sealed`；sealed 后禁止 UPDATE/DELETE/TRUNCATE；
- run、job 和 recompute intent 只允许迁移中定义的封闭转换，terminal 行不可再改；
- publication 永久 append-only；current publication 只能原子替换 pointer；
- pointer 写入会锁 scope generation，并拒绝发布 generation 已过期的 run；
- deferred constraint trigger 会在 commit 时再次要求最终 pointer 对应 run 已是 `published`、run/publication
  hash 一致、job 已 succeeded 且 publication token 等于 job token；只切 pointer 的半发布事务无法提交；
- job lease takeover 必须在旧 lease 到期后推进 attempt 和 fencing token；旧 worker 的条件更新会影响零行；
- lease owner/expiry/heartbeat 只表示活动 lease，进入 retry/terminal 状态时必须清空；attempt/token 永久保留；
- migration 末尾检查所有表、约束、索引、函数和 trigger 均已落库且有效，否则整笔回滚。

`calculation_worker_heartbeat` 独立于 job 是否繁忙；API command readiness 应以支持相应
`calculation_kind` 的最新 heartbeat 判定 worker 健康。相同 `worker_id` 可以 upsert；同一 instance 的
heartbeat 只能前进，worker 重启必须使用新 `instance_id` 和更晚的 `started_at`/`heartbeat_at`；旧 instance
晚到的 upsert 会被拒绝，不能覆盖新 instance。

## PostgreSQL invariant tests

```bash
PORTFOLIO_OPS_TEST_POSTGRES_URL='postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops' \
  python -m pytest -q infra/calculation_registry/tests/test_postgres_foundation.py
```

测试会创建并销毁独立数据库，覆盖完整 fenced publication 顺序、terminal lease 清理、半发布 commit
拒绝、旧 attempt partial output 与新 attempt 隔离、publication token 选择、publication immutability，
以及 dependency INSERT 与 manifest seal 的双向真实并发锁竞争。
