# Portfolio Operations Workbench Database Workflow

## Default Topology

- PostgreSQL database: `portfolio_ops`
- Schemas:
  - `instrument_registry`
  - `portfolio`
  - `watchlist`

## Start PostgreSQL

```bash
(cd infra/postgres && docker compose up -d)
```

The compose port is bound to `127.0.0.1:5432` by default; opt into a broader
network exposure only through an explicit, reviewed deployment override.
On a fresh Docker volume, the image bootstrap superuser is distinct from the
`portfolio_ops` application role. The application role owns the local project
database for Alembic but is explicitly `NOSUPERUSER / NOCREATEDB / NOCREATEROLE`;
the isolated test role is the only application-adjacent role with `CREATEDB`.
Existing Docker volumes are not re-initialized automatically, so audit or
recreate an older volume before relying on these guarantees.

Local connection values come from Git-ignored backend `.env` links to `~/.config/orataba/secrets/portfolio-operations-workbench/`, or from shell environment variables. Do not paste credential-bearing URLs into docs, issues, PR text, commit messages, or chat transcripts.

- host: `127.0.0.1`
- port: `5432`
- database: set by your local environment
- connection URL: set the relevant environment variable locally; do not paste credential-bearing URLs into public docs

## Apply Migrations

Every PostgreSQL migration requires a process-level target confirmation.  The
guard compares the URL database name and the live `current_database()` value
before any schema creation or DDL.  Application `.env` files cannot provide
this authorization.

```bash
export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=portfolio_ops
(cd infra/instrument_registry && alembic upgrade head)
(cd apps/portfolio/backend && PYTHONPATH=. alembic upgrade head)
(cd apps/watchlist/backend && PYTHONPATH=. alembic upgrade head)
```

`migrate_all.sh` is a low-level migration runner, not a production release
entry point. A release must use the fail-closed orchestrator so writer fencing,
one verified three-schema recovery point, rebuilds, audit, and service health
are one workflow:

```bash
CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_RELEASE_DATABASE_URL='postgresql+psycopg://portfolio_ops:REDACTED@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
  infra/scripts/release_database.sh
```

The orchestrator records and stops all managed API/Web/refresh writers, rejects
unconfirmed targets and remaining client connections, creates and validates a
single custom-format backup containing all three schemas, runs all migration
chains, rebuilds Portfolio and Watchlist, requires a zero-failure/zero-warning
JSON audit, creates a verified post-release backup, restores the prior service
set, checks every previously active HTTP service, and—when Portfolio API was
active—strictly serializes every portfolio's current transaction list and each
current transaction's revision history. If any step after the first database
mutation fails, it restores the pre-release backup and leaves services stopped
for operator review. `migrate_all.sh` remains available only as an internal
step and for isolated disposable databases.

## Rebuild Derived State After Breaking Migrations

Migrations change schema and invalidate obsolete projections; they do not hide
that invalidation by translating legacy payloads on read.  After a migration
that marks Portfolio or Watchlist derived state stale, keep application services
stopped and run the canonical rebuild commands against the same explicitly
configured database:

```bash
apps/portfolio/backend/.venv/bin/python \
  apps/portfolio/backend/scripts/rebuild_portfolio_daily_snapshots.py \
  --as-of-date YYYY-MM-DD \
  --all

apps/watchlist/backend/.venv/bin/python \
  apps/watchlist/backend/scripts/rebuild_watchlist_derived_state.py \
  --valuation-date YYYY-MM-DD \
  --all-active \
  --rounds 2
```

Neither CLI accepts a database URL argument: the caller must export the normal
application database/schema variables so target selection cannot diverge from
the migrated environment. Portfolio always rebuilds from the earliest affected
date. Watchlist requires two fixed-order full-universe rounds because peer
cohort fingerprints produced in round one only become globally converged after
every active instrument reads the updated cohort in round two. Any target
failure keeps the command non-zero; a partial run is not release success.

Only restart services after `infra/scripts/audit_live_data.py` passes against a
single repeatable-read database snapshot.

The destructive dump restore wrapper performs a checksum/archive/target
preflight, stops managed local services and refresh writers, retains a
pre-restore schema backup, upgrades, rebuilds both derived-state domains, and
requires the same zero-warning audit before restart. It automatically rolls
back on restore, validation, migration, rebuild, or audit failure:

```bash
PORTFOLIO_OPS_DB_HOST=127.0.0.1 \
PORTFOLIO_OPS_DB_PORT=5432 \
PORTFOLIO_OPS_DB_NAME=portfolio_ops \
PORTFOLIO_OPS_DB_USER=portfolio_ops \
CONFIRM_RESTORE=portfolio_ops \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=YYYY-MM-DD \
  infra/postgres/restore_project_dump.sh
```

By default it accepts only loopback or Unix-socket database hosts. Safety
backups live under
`${XDG_STATE_HOME:-~/.local/state}/portfolio-operations-workbench/postgres-backups/`.

数据库结构只由 Alembic migration 管理。运行时不再保留任何 SQLite bootstrap、迁运或镜像脚本。

## Reset Local Schemas

如果本地数据库已经混入旧 schema、脏数据、半迁移状态，直接重建三套 schema：

```bash
CONFIRM_REBUILD_DATABASE=portfolio_ops ./infra/postgres/rebuild_local_schemas.sh
```

说明：

- 这是破坏性命令，会删除 `instrument_registry / portfolio / watchlist` 三个 schema 的全部数据。
- `CONFIRM_REBUILD_DATABASE` 必须与实时连接返回的数据库名完全一致；脚本随后把同一连接显式传给三套 Alembic，禁止 drop 与 migrate 指向不同数据库。
- 默认读取 `PORTFOLIO_OPS_LOCAL_POSTGRES_URL`。
- 如果数据库地址不同，先在 backend `.env` 或 shell 环境里设置 `PORTFOLIO_OPS_LOCAL_POSTGRES_URL`。
- migration 只重建结构和仓库内定义的 reference rows；业务导入数据、手工录入数据、历史 runtime 数据不会自动恢复。
- `portfolio` schema 重建后默认是空组合状态；需要组合时，显式通过 UI/API 创建，或运行 `apps/portfolio/backend/scripts/import_real_portfolio_from_csv.py --csv-path ... --portfolio-id ...` 导入。
- `watchlist` schema 重建后不会自动注入示例 watchlist、示例标签值或 demo 产品框架赋值。

## Runtime Defaults

- Platform backend connects to `instrument_registry`
- Portfolio backend connects to `portfolio`
- Watchlist backend connects to `watchlist`

Each backend sets PostgreSQL `search_path` from its own `database_schema` setting, so one database instance can host all three app schemas without cross-app table collisions.

当前 backend 顶层包名已经拆开：

- `platform_app`
- `portfolio_app`
- `watchlist_app`

## Testing

Tests override the database URL to temporary SQLite databases. That path exists only for fast isolated tests; the repository default runtime target is PostgreSQL.

仓库统一验证入口是 `infra/scripts/verify_repository.sh`；CI 运行方式和固定版本见
[CI.md](./CI.md)。依赖安装与验证分离，脚本不会在测试中修改锁文件或安装未锁定依赖。

Each backend should run tests from its own `backend/` directory:

```bash
(cd apps/platform/backend && pytest)
(cd apps/portfolio/backend && pytest)
(cd apps/watchlist/backend && pytest)
```

SQLite fast tests 不会覆盖 PostgreSQL 专属的 cross-schema FK / search_path 行为。
共享资产存储边界、append-only 行情修订和交易账本约束的回归验证使用：

```bash
infra/scripts/verify_repository.sh postgres-integration all
```

这些测试默认使用仅供本地测试的
`postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops`；
也可以在 shell 中通过 `PORTFOLIO_OPS_TEST_POSTGRES_URL` 显式覆盖。不要把测试连接放进
backend runtime `.env`，也不要让 API 使用测试角色。

这两条测试会临时创建并删除随机命名的独立数据库。macOS 本地服务安装器会创建
`portfolio_ops_test` 角色；Docker 新数据卷会通过 `infra/postgres/init` 创建它。该角色
只有 `LOGIN + CREATEDB`，不是超级用户，也不能创建角色；运行时 `portfolio_ops` 不获得
`CREATEDB`。如果现有数据库早于这项配置，先运行：

```bash
infra/launchd/bootstrap_local_database.sh
```

单独运行 pytest 时，连接不可用可能按测试夹具语义跳过；统一验证入口会解析 JUnit 报告，任何
skip 或零收集都直接失败。角色缺失或权限错误也不能作为“环境不可用”静默通过。

## Repository Hygiene

- 仓库不再保存任何运行时 SQLite 数据文件。
- 如果目录里出现 `*.db / *.sqlite / *.sqlite3`，应视为临时本地产物并删除，而不是提交。
- SQLite 仅保留在测试夹具中，通过 `tmp_path` 动态生成。
- `nav/` 是迁移保留的 NAV 附件图片数据，已经纳入 Git；后续新增大批量原始材料前先判断是否应该进入仓库。
- `node_modules/` 与 frontend `dist/` 只由本地 install/build 生成，不作为提交内容。
