# Shared Asset Schema

这里维护 `shared_asset` schema 的独立 Alembic 入口。

常用命令：

```bash
(cd infra/shared_asset && alembic upgrade head)
```

可选环境变量：

- `YUNGU_SHARED_ASSET_DATABASE_URL`
  运行迁移使用的数据库连接串。
- `YUNGU_SHARED_ASSET_ALEMBIC_DATABASE_URL`
  仅迁移专用连接串，优先级高于 `YUNGU_SHARED_ASSET_DATABASE_URL`。
- `YUNGU_SHARED_ASSET_SCHEMA`
  目标 schema，默认是 `shared_asset`。
