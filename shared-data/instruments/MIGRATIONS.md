# Instrument Data Schema

这里维护 `instrument_data` schema 的独立 Alembic 入口，与共享资产模型同属 `shared-data/instruments`。

新库、升级和恢复统一从仓库根目录运行：

```bash
infra/scripts/migrate_all.sh
```

数据库目标必须显式配置；`shared-data/.env.example` 仅是键名模板，源码目录不创建 `.env` 文件或软链接。迁移只读取显式进程环境或仓库外 secrets，连接 URL 必须与数据处理、Watchlist 和 Portfolio 使用同一个 canonical PostgreSQL。Home 不连接数据库。

本机现有真实数据库使用 `infra/launchd/install_local_services.sh`，让它先停写并备份全部四个 schema。统一 runner 会先建立摄取层的原始证据表，再执行 canonical-NAV cleanup；共享层暂留在 `20260902_0029`，完成业务应用的历史迁移后，才推进共享层 head，将 `instrument_registry` 原地改名为 `instrument_data`。不要无序地独立执行 `alembic upgrade head`。

Instrument Data 只保存可跨组合复用的市场资产。FCN 和期权合约属于 Portfolio 本地事件会计事实；迁移会在确认没有遗留 `fcn`/`option` instrument 或 `contract_id` broker identifier 后删除旧衍生品列，不提供运行时双轨兼容。

环境变量：

- `INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL`
  运行迁移使用的显式 canonical 数据库连接串。
- `INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL`
  仅迁移专用连接串；如设置，必须与 `INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL` 保持同一目标。
- `INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA`
  canonical schema 固定为 `instrument_data`；非空配置只能取该值。历史迁移内部使用旧名，运行时不保留旧 schema 或别名。
