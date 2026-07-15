# Instrument Registry Schema

这里维护 `instrument_registry` schema 的独立 Alembic 入口。

常用命令：

```bash
(cd infra/instrument_registry && alembic upgrade head)
```

数据库目标必须显式配置；`.env.example` 仅是键名模板，backend 目录不创建 `.env` 文件或软链接。迁移只读取显式进程环境或仓库外 secrets，连接 URL 必须与三个 app 使用同一个 canonical PostgreSQL。

环境变量：

- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL`
  运行迁移使用的显式 canonical 数据库连接串。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL`
  仅迁移专用连接串；如设置，必须与 `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL` 保持同一目标。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA`
  目标 schema，默认是 `instrument_registry`。
