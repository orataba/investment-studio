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
cd /home/shaw/yungu/apps/platform/backend
PYTHONPATH=. alembic upgrade head

cd /home/shaw/yungu/apps/portfolio/backend
PYTHONPATH=. alembic upgrade head

cd /home/shaw/yungu/apps/watchlist/backend
PYTHONPATH=. alembic upgrade head
```

数据库结构只由 Alembic migration 管理。运行时不再保留任何 SQLite bootstrap、迁运或镜像脚本。

## Runtime Defaults

- Platform backend connects to `shared_asset`
- Portfolio backend connects to `portfolio`
- Watchlist backend connects to `watchlist`

Each backend sets PostgreSQL `search_path` from its own `database_schema` setting, so one database instance can host all three app schemas without cross-app table collisions.

## Testing

Tests override the database URL to temporary SQLite databases. That path exists only for fast isolated tests; the repository default runtime target is PostgreSQL.

Each backend should run tests from its own `backend/` directory:

```bash
cd /home/shaw/yungu/apps/platform/backend && pytest
cd /home/shaw/yungu/apps/portfolio/backend && pytest
cd /home/shaw/yungu/apps/watchlist/backend && pytest
```

三个 backend 都使用顶层包名 `app`，因此迁移、测试和 `uvicorn` 启动命令都应在各自 backend 目录中执行，或显式设置 `PYTHONPATH=. / --app-dir .`。

## Repository Hygiene

- 仓库不再保存任何运行时 SQLite 数据文件。
- 如果目录里出现 `*.db / *.sqlite / *.sqlite3`，应视为临时本地产物并删除，而不是提交。
- SQLite 仅保留在测试夹具中，通过 `tmp_path` 动态生成。
