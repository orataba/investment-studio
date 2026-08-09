# Instrument Registry Schema

这里维护 `instrument_registry` schema 的独立 Alembic 入口。

仅在隔离开发数据库中调试这一条迁移链时可运行：

```bash
(cd infra/instrument_registry && alembic upgrade head)
```

数据库目标必须显式配置；`.env.example` 仅是键名模板，backend 目录不创建 `.env` 文件或软链接。迁移只读取显式进程环境或仓库外 secrets，连接 URL 必须与三个 app 使用同一个 canonical PostgreSQL。

发布和新库恢复必须使用仓库根目录的 `infra/scripts/migrate_all.sh`。本机现有真实数据库使用 `infra/launchd/install_local_services.sh`，让它先停写并备份全部四个 schema。当前 Registry head 包含 destructive canonical-NAV cleanup，统一 runner 会先停在其前置 revision、建立 Platform 私有原始证据表，再推进 Registry head。不要用无序的独立 `alembic upgrade head` 绕过该依赖。

Registry 只保存可跨组合复用的市场资产。FCN 和期权合约属于 Portfolio 本地事件会计事实；Registry head 会在确认没有遗留 `fcn`/`option` instrument 或 `contract_id` broker identifier 后删除旧衍生品列，不提供运行时双轨兼容。

环境变量：

- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL`
  运行迁移使用的显式 canonical 数据库连接串。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL`
  仅迁移专用连接串；如设置，必须与 `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL` 保持同一目标。
- `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA`
  canonical schema 固定为 `instrument_registry`；非空配置只能取该值。
