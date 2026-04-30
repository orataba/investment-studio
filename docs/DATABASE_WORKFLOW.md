# Yungu Database Workflow

## Default Topology

- PostgreSQL database: `yungu`
- Schemas:
  - `shared_asset`
  - `portfolio`
  - `watchlist`

## Start PostgreSQL

```bash
cd /home/shaw/yungu/infra/postgres
docker compose up -d
```

Default local credentials:

- host: `127.0.0.1`
- port: `5432`
- database: `yungu`
- user: `yungu`
- password: `yungu`

## Apply Migrations

```bash
cd /home/shaw/yungu/infra/shared_asset
alembic upgrade head

cd /home/shaw/yungu/apps/portfolio/backend
PYTHONPATH=. alembic upgrade head

cd /home/shaw/yungu/apps/watchlist/backend
PYTHONPATH=. alembic upgrade head
```

数据库结构只由 Alembic migration 管理。运行时不再保留任何 SQLite bootstrap、迁运或镜像脚本。

## Reset Local Schemas

如果本地数据库已经混入旧 schema、脏数据、半迁移状态，直接重建三套 schema：

```bash
cd /home/shaw/yungu
./infra/postgres/rebuild_local_schemas.sh
```

说明：

- 这是破坏性命令，会删除 `shared_asset / portfolio / watchlist` 三个 schema 的全部数据。
- 默认连接本地 `postgresql://yungu:yungu@127.0.0.1:5432/yungu`。
- 如果数据库地址不同，可先设置 `YUNGU_LOCAL_POSTGRES_URL`。
- migration 只重建结构和仓库内定义的 reference rows；业务导入数据、手工录入数据、历史 runtime 数据不会自动恢复。
- `portfolio` schema 重建后默认是空组合状态；需要组合时，显式通过 UI/API 创建，或运行 `apps/portfolio/backend/scripts/import_real_portfolio_from_csv.py --csv-path ... --portfolio-id ...` 导入。
- `watchlist` schema 重建后不会自动注入示例 watchlist、示例标签值或 demo 产品框架赋值。

## Runtime Defaults

- Platform backend connects to `shared_asset`
- Portfolio backend connects to `portfolio`
- Watchlist backend connects to `watchlist`

Each backend sets PostgreSQL `search_path` from its own `database_schema` setting, so one database instance can host all three app schemas without cross-app table collisions.

当前 backend 顶层包名已经拆开：

- `platform_app`
- `portfolio_app`
- `watchlist_app`

## Testing

Tests override the database URL to temporary SQLite databases. That path exists only for fast isolated tests; the repository default runtime target is PostgreSQL.

Each backend should run tests from its own `backend/` directory:

```bash
cd /home/shaw/yungu/apps/platform/backend && pytest
cd /home/shaw/yungu/apps/portfolio/backend && pytest
cd /home/shaw/yungu/apps/watchlist/backend && pytest
```

SQLite fast tests 不会覆盖 PostgreSQL 专属的 cross-schema FK / search_path 行为。
共享资产存储边界的回归验证需要额外跑这两条 PostgreSQL integration tests：

```bash
cd /home/shaw/yungu/apps/portfolio/backend && pytest tests/test_postgres_shared_asset_constraints.py -q
cd /home/shaw/yungu/apps/watchlist/backend && pytest tests/test_postgres_shared_asset_constraints.py -q
```

这些测试默认连接本地 `postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu`。
如果测试数据库地址不同，可以设置 `YUNGU_TEST_POSTGRES_URL`。
这两条测试会在同一个 PostgreSQL 实例里临时创建并删除独立数据库，所以运行用户还需要能连接 `postgres` 库并具备 `CREATE DATABASE / DROP DATABASE` 权限。

## Repository Hygiene

- 仓库不再保存任何运行时 SQLite 数据文件。
- 如果目录里出现 `*.db / *.sqlite / *.sqlite3`，应视为临时本地产物并删除，而不是提交。
- SQLite 仅保留在测试夹具中，通过 `tmp_path` 动态生成。
- `nav/` 是本机 NAV / Excel 导入暂存目录，脚本会从这里读取文件，但目录内容不属于源码。
- `node_modules/` 与 frontend `dist/` 只由本地 install/build 生成，不作为提交内容。
