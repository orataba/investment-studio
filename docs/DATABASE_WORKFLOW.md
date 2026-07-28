# Portfolio Operations Workbench Database Workflow

## Default Topology

- PostgreSQL database: `portfolio_ops`
- Schemas:
  - `instrument_registry`
  - `platform`
  - `portfolio`
  - `watchlist`

## Start PostgreSQL

```bash
(cd infra/postgres && docker compose up -d)
```

The compose port is bound to `127.0.0.1:5432` by default; opt into a broader
network exposure only through an explicit, reviewed deployment override.

Runtime secrets come only from `~/.config/orataba/secrets/portfolio-operations-workbench/` or explicitly exported process variables; backend directories must not contain `.env` files or links. Use a password-free URL together with a current-user `0600` `.pgpass`. Never place a password-bearing URL in shell commands, docs, issues, PR text, commits, or chat transcripts.

- host: `127.0.0.1`
- port: `5432`
- database: set by your local environment
- connection URL: set the relevant environment variable locally; do not paste credential-bearing URLs into public docs

## Apply Migrations

Use the fail-fast shared entry point for a release or a clean installation:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/portfolio-ops/env" \
  infra/scripts/migrate_all.sh
```

`ENV_ROOT` is optional. When it is set, it must contain `platform.env`,
`portfolio.env`, and `watchlist.env`. Without it, the script uses only the
already-exported process environment and never reads repository-local `.env`.
The Instrument Registry, Platform, Portfolio, and Watchlist migration targets
are all required explicitly before any migration begins; the Registry target
never falls back to the Platform runtime variable.
Already-exported process variables always take precedence over external env-file
entries; this prevents a release or restore command from being silently redirected.
Before Alembic runs, the entry point compares the host, port, and database name
for all four runtime URLs and every explicit `*_ALEMBIC_DATABASE_URL`. Set
`PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE` when the release environment also
needs an exact database-name assertion. A mismatch fails closed, and the
diagnostic output never includes URL usernames or passwords.

The runner owns the dependency order. It first brings Registry to the revision
required by Platform's evidence schema, then migrates `platform`, advances Registry
to its destructive canonical-NAV revision, and finally migrates Portfolio and
Watchlist. Do not replace this with four independent `alembic upgrade head` calls:
the Registry NAV cleanup must not run before Platform can preserve the original
manual/API/email observations.

The destructive dump restore wrapper performs a checksum/archive/target
preflight, stops managed local services, retains a pre-restore schema backup,
and automatically rolls back on restore, validation, or migration failure:

```bash
PORTFOLIO_OPS_DUMP_CHECKSUM_PATH=/secure/path/portfolio-operations-workbench.sha256 \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
CONFIRM_RESTORE=portfolio_ops \
  infra/postgres/restore_project_dump.sh \
  /secure/path/portfolio-operations-workbench.pgdump
```

By default it accepts only loopback or Unix-socket database hosts. Safety
backups live under
`${XDG_STATE_HOME:-~/.local/state}/portfolio-operations-workbench/postgres-backups/`.
The incoming SHA-256 file is mandatory; there is no unverified-restore switch.
After managed services are stopped, the wrapper terminates remaining client
connections and fails closed if any connection remains. Pre-restore backup and
rollback use the same archive/manifest/checksum primitive as the launchd
installer. Schema replacement and backup replay each execute in one PostgreSQL
transaction, so a replay error preserves the pre-attempt schemas. A rollback
failure leaves managed services stopped and retains the recovery state path.

数据库结构只由 Alembic migration 管理。运行时不再保留任何 SQLite bootstrap、迁运或镜像脚本。

## Reset Local Schemas

如果本地数据库已经混入旧 schema、脏数据、半迁移状态，直接重建四套 schema：

```bash
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  ./infra/postgres/rebuild_local_schemas.sh --confirm-destroy-project-schemas
```

说明：

- 这是破坏性命令，会删除 `instrument_registry / platform / portfolio / watchlist` 四个 schema 的全部数据，包括 Platform 的邮箱游标、原始证据与重试状态。
- 必须同时显式提供 `postgresql://` 或 `postgresql+psycopg://` URL 和
  `--confirm-destroy-project-schemas`；脚本没有隐式目标或兼容性 fallback。
- 同一个显式目标会交给 `psql` 和四条 Alembic migration chain；带密码 URL 会先在
  私有临时目录拆成无密码连接参数与 `0600` passfile，不进入子进程 argv，脚本输出也
  只显示数据库名与角色。
- 脚本会先停止当前已加载的托管 LaunchAgent。schema 删除或 migration 开始后若失败，
  服务保持停止，避免在半重建数据库上恢复写入；修复后按错误信息中的 state file 恢复
  原服务集合。
- migration 只重建结构和仓库内定义的 reference rows；业务导入数据、手工录入数据、历史 runtime 数据不会自动恢复。
- `portfolio` schema 重建后默认是空组合状态；需要组合时，显式通过 UI/API 创建，或运行 `apps/portfolio/backend/scripts/import_real_portfolio_from_csv.py --csv-path ... --portfolio-id ...` 导入。
- `watchlist` schema 重建后不会自动注入示例 watchlist、示例标签值或 demo 产品框架赋值。

## Runtime Defaults

- Platform backend uses `platform, instrument_registry, public` search-path order: operational state is private, canonical facts remain shared
- Portfolio backend connects to `portfolio`
- Watchlist backend connects to `watchlist`

Platform pins `database_schema=instrument_registry` and
`operations_database_schema=platform`; its migrations keep their Alembic version
table in `platform`. Portfolio and Watchlist keep their own schema first. One
database instance can therefore host all four project schemas without treating
Platform operational rows as shared market facts.

当前 backend 顶层包名已经拆开：

- `platform_app`
- `portfolio_app`
- `watchlist_app`

## Testing

Tests override the database URL to temporary SQLite databases. That path exists only for fast isolated tests; the repository default runtime target is PostgreSQL.

Each backend should run tests from its own `backend/` directory:

```bash
(cd apps/platform/backend && pytest)
(cd apps/portfolio/backend && pytest)
(cd apps/watchlist/backend && pytest)
```

SQLite fast tests 不会覆盖 PostgreSQL 专属的 cross-schema FK / search_path 行为。
共享资产存储边界的回归验证需要额外跑这两条 PostgreSQL integration tests：

```bash
(cd apps/portfolio/backend && pytest tests/test_postgres_instrument_registry_constraints.py -q)
(cd apps/watchlist/backend && pytest tests/test_postgres_instrument_registry_constraints.py -q)
```

这些测试默认读取 `PORTFOLIO_OPS_TEST_POSTGRES_URL`。
如果测试数据库地址不同，只在当前进程环境或仓库外的测试 secrets 中显式设置 `PORTFOLIO_OPS_TEST_POSTGRES_URL`；backend 目录不读取也不保存 `.env`。
这两条测试会在同一个 PostgreSQL 实例里临时创建并删除独立数据库，所以运行用户还需要能连接 `postgres` 库并具备 `CREATE DATABASE / DROP DATABASE` 权限。

## Repository Hygiene

- 仓库不再保存任何运行时 SQLite 数据文件。
- 如果目录里出现 `*.db / *.sqlite / *.sqlite3`，应视为临时本地产物并删除，而不是提交。
- SQLite 仅保留在测试夹具中，通过 `tmp_path` 动态生成。
- `nav/` 是迁移保留的 NAV 附件图片数据，已经纳入 Git；后续新增大批量原始材料前先判断是否应该进入仓库。
- `node_modules/` 与 frontend `dist/` 只由本地 install/build 生成，不作为提交内容。
