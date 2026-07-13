# Instrument Registry Schema

这里维护 `instrument_registry` schema 的独立 Alembic 入口。

常用命令：

```bash
(
  export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=portfolio_ops
  cd infra/instrument_registry
  alembic upgrade head
)
```

可选环境变量：

- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL`
  运行迁移使用的数据库连接串。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL`
  仅迁移专用连接串，优先级高于 `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL`。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA`
- `PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE`
  PostgreSQL migration 的进程级目标确认；URL 数据库名和实时连接身份均必须与其一致，且不能仅依赖应用 `.env`。
  目标 schema，默认是 `instrument_registry`。

## Canonical FX reference identities

迁移头 `20260713_0010` 建立两个固定的参考身份：

- `fx-usd-hkd`：`fx / HKD / active`，primary ticker 为 `USDHKD`；
- `fx-usd-cny`：`fx / CNY / active`，primary ticker 为 `USDCNY`。

两者的严格 quote policy 均为 `trading/valuation/chart/reference = ["spot"]`、
`total_return = []`。迁移只建立 instrument master identity，不创建 `QuoteSeries`、
`QuoteObservation` 或任何汇率值；真实 spot 数据必须通过正常的行情录入边界写入。

若同名 instrument、币种、生命周期、policy 或 primary ticker 已存在冲突，升级会在写入前
整笔失败，不会替换业务来源配置，也不会把冲突记录自动“修好”。该迁移不可逆，避免在
identity 已被组合或行情引用后由 downgrade 级联删除业务事实。

迁移头 `20260713_0011` 进一步要求 Instrument 与 QuoteSeries 的 `currency` 都是显式三位大写代码。升级前若发现缺失或歧义币种会整笔失败，不会默认成 USD 或自动猜测。
