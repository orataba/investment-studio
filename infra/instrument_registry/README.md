# Instrument Registry Schema

这里维护 `instrument_registry` schema 的独立 Alembic 入口。

常用命令：

```bash
(cd infra/instrument_registry && alembic upgrade head)
```

可选环境变量：

- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL`
  运行迁移使用的数据库连接串。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL`
  仅迁移专用连接串，优先级高于 `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL`。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA`
  目标 schema，默认是 `instrument_registry`。
