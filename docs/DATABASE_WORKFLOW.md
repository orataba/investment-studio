# Investment Studio Database Workflow

## Default Topology

- PostgreSQL database: `investment_studio`
- Schemas:
  - `identity`
  - `instrument_data`
  - `data_ingestion`
  - `portfolio`
  - `watchlist`
  - `market_data`
  - `market_text`
  - `briefing`

Eight schemas use seven Alembic chains; `market_data` and `market_text` share the market chain. Immutable numerical Parquet and original text objects live outside PostgreSQL under the configured public data root. Restore both metadata and matching objects; see [Market Data Pipeline](MARKET_DATA_PIPELINE.md).

## Start PostgreSQL

```bash
(cd infra/postgres && docker compose up -d)
```

The compose port is bound to `127.0.0.1:5432` by default; opt into a broader
network exposure only through an explicit, reviewed deployment override.

Runtime secrets come only from `~/.config/orataba/secrets/investment-studio/` or explicitly exported process variables; backend directories must not contain `.env` files or links. Use a password-free URL together with a current-user `0600` `.pgpass`. Never place a password-bearing URL in shell commands, docs, issues, PR text, commits, or chat transcripts.

- host: `127.0.0.1`
- port: `5432`
- database: set by your local environment
- connection URL: set the relevant environment variable locally; do not paste credential-bearing URLs into public docs

Identity uses its own versioned Alembic chain. Its first revision adopts the existing unversioned account tables in place only when they match the frozen baseline; it never recreates users, resets credentials or grants business access.

## Apply Migrations

Use the fail-fast shared entry point only for a clean database or an environment
whose writers have already been stopped and whose backup/recovery is managed by
the caller:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio" \
  infra/scripts/migrate_all.sh
```

`ENV_ROOT` is optional. When it is set, it must contain `home.env`, `data.env`,
`portfolio.env`, `watchlist.env`, `market.env`, and `briefing.env`. Without it, the script uses only the
already-exported process environment and never reads repository-local `.env`.
The Identity, Instrument Data, Data Ingestion, Portfolio, Watchlist, Market, and Briefing migration targets
are all required explicitly before any migration begins; the Instrument Data target
never falls back to the ingestion runtime variable. Historical environment-variable
prefixes remain unchanged.
Already-exported process variables always take precedence over external env-file
entries; this prevents a release or restore command from being silently redirected.
Before Alembic runs, the entry point compares the host, port, and database name
for all seven runtime URLs and every explicit `*_ALEMBIC_DATABASE_URL`. Set
`INVESTMENT_STUDIO_MIGRATION_EXPECTED_DATABASE` when the release environment also
needs an exact database-name assertion. A mismatch fails closed, and the
diagnostic output never includes URL usernames or passwords.

The runner owns the dependency order. It first brings the historical
`instrument_registry` schema to the revision required by the ingestion evidence
schema, then builds the historical `platform` schema through `20260823_0007`
and advances the shared asset facts through their canonical-NAV contract.
Ingestion revision `20260904_0008` renames `platform` in place to `data_ingestion`,
before Portfolio and Watchlist migrate. Finally, Instrument Data revisions
`20260904_0030` and `20260904_0031` rename `instrument_registry` to
`instrument_data` and add reference snapshots. The runner then applies the shared Market and Briefing chains. Do not replace this with
independent `alembic upgrade head` calls: NAV cleanup must not run before
ingestion can preserve the original manual/API/email observations.
Revisions `20260904_0032` and `20260904_0009` also rebind the stored trigger
function search paths. PostgreSQL does not rewrite those text settings during a
schema rename; verify actual market-data and NAV writes after upgrading.

For the managed local database, use `infra/launchd/install_local_services.sh`
instead. It stops all managed writers, creates and retains a verified backup of
all eight schemas, runs the ordered migration, restores the backup automatically
if migration, release snapshot refresh, the read-only integrity audit, or deployment fails, and
only resumes service after health checks. The installer manages Studio writers; stop or wait for Regime source writers separately before shared-schema maintenance. PostgreSQL rollback does not restore external Parquet or original-text files.
Clean-cut derivative migrations may reject Alembic downgrade because restoring
schema shape cannot reconstruct the original business facts. Recovery for such
migrations uses the retained pre-migration eight-schema backup, not a forced
downgrade.

The destructive dump restore wrapper performs a checksum/archive/target
preflight, stops managed local services, retains a pre-restore schema backup,
and automatically rolls back on restore, validation, or migration failure:

```bash
INVESTMENT_STUDIO_DUMP_CHECKSUM_PATH=/secure/path/investment-studio.sha256 \
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
CONFIRM_RESTORE=investment_studio \
  infra/postgres/restore_project_dump.sh \
  /secure/path/investment-studio.pgdump
```

By default it accepts only loopback or Unix-socket database hosts. Safety
backups live under
`${XDG_STATE_HOME:-~/.local/state}/investment-studio/postgres-backups/`.
The incoming SHA-256 file is mandatory; there is no unverified-restore switch.
After managed services are stopped, the wrapper terminates remaining client
connections and fails closed if any connection remains. Pre-restore backup and
rollback use the same archive/manifest/checksum primitive as the launchd
installer. Schema replacement and backup replay each execute in one PostgreSQL
transaction, so a replay error preserves the pre-attempt schemas. A rollback
failure leaves managed services stopped and retains the recovery state path.

上述八个分区的数据库结构只由 Alembic migration 管理，不再保留 SQLite 业务库的 bootstrap、迁运或镜像脚本。Regime 自有模型状态与运行目录按子模块部署合同维护。

## Reset Local Schemas

如果本地数据库已经混入旧 schema、脏数据、半迁移状态，明确需要清空时重建八个 schema：

```bash
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql://investment_studio@127.0.0.1:5432/investment_studio' \
  ./infra/postgres/rebuild_local_schemas.sh --confirm-destroy-project-schemas
```

说明：

- 这是破坏性命令，会删除 `identity / instrument_data / data_ingestion / portfolio / watchlist / market_data / market_text / briefing` 八个 schema 的全部数据，包括摄取游标、公共数据目录、原文版本索引、PIT 投影和日报周报记录；外部原始文件不会由 migration 恢复。
- 必须同时显式提供 `postgresql://` 或 `postgresql+psycopg://` URL 和
  `--confirm-destroy-project-schemas`；脚本没有隐式目标或兼容性 fallback。
- 同一个无密码显式目标会交给 `psql` 和七条 Alembic migration chain；密码只通过
  当前用户权限为 `0600` 的 `.pgpass` 提供。带密码 URL 会在任何数据库变更前被拒绝。
- 脚本会先停止当前已加载的托管 LaunchAgent。schema 删除或 migration 开始后若失败，
  服务保持停止，避免在半重建数据库上恢复写入；修复后按错误信息中的 state file 恢复
  原服务集合。
- migration 只重建结构和仓库内定义的 reference rows；业务导入数据、手工录入数据、历史 runtime 数据不会自动恢复。
- `portfolio` schema 重建后默认是空组合状态；需要组合时，显式通过 UI/API 创建，或运行 `apps/portfolio/backend/scripts/import_real_portfolio_from_csv.py --csv-path ... --portfolio-id ...` 导入。
- `watchlist` schema 重建后不会自动注入示例 watchlist、示例标签值或 demo 产品框架赋值。

## Canonical Runtime Schemas

- Data maintenance uses `data_ingestion, instrument_data, public` search-path order: operational state is private, canonical facts remain shared
- Portfolio backend connects to `portfolio`
- Watchlist backend connects to `watchlist`
- Shared numeric and text metadata use `market_data` and `market_text`
- Briefing owns generated reports in `briefing` and reads shared market evidence
- Home owns account/session state in `identity`; it does not read business records

Data maintenance pins `database_schema=instrument_data` and
`operations_database_schema=data_ingestion`; its migrations keep their Alembic version
table in `data_ingestion`. Portfolio and Watchlist keep their own schema first. One
database instance can therefore host all eight project schemas without treating
ingestion operational rows as shared market facts. These schema names are fixed
contracts rather than deployment customization points.

`data_ingestion` was named `platform` before revision `20260904_0008`.
The rename preserves rows, object identities, foreign keys and grants; it does
not merge databases or move canonical facts. The existing
`platform_alembic_version` table stays unchanged to preserve migration history. Old schema references belong
only in historical migrations and backup/rollback support, not runtime settings.

当前 backend 顶层包名已经拆开：

- `home_api`：登录、导航与 `identity` 账号数据库
- `studio_data`：后台数据维护 CLI
- `portfolio_app`
- `watchlist_app`
- `studio_market`：共享数值、文本、PIT 与数据包
- `briefing_app`：日报和周报

## Testing

普通 backend 测试使用临时 SQLite，只覆盖快速、隔离的逻辑；统一入口见根目录 README。SQLite 不会覆盖
PostgreSQL 专属的 migration、cross-schema FK、search path、constraint、并发和事务行为。

`migration-heads` 会先调用 `migrate_all.sh`，然后对七条 Alembic chain 执行 `current --check-heads`。
它不是只读源码检查，必须指向明确创建的可丢弃测试数据库：

```bash
MIGRATION_TEST_URL='postgresql+psycopg://migration_test_role@127.0.0.1:5432/investment_studio_migration_test'
INVESTMENT_STUDIO_HOME_DATABASE_URL="$MIGRATION_TEST_URL" \
INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL="$MIGRATION_TEST_URL" \
INVESTMENT_STUDIO_DATA_DATABASE_URL="$MIGRATION_TEST_URL" \
INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL="$MIGRATION_TEST_URL" \
INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL="$MIGRATION_TEST_URL" \
INVESTMENT_STUDIO_MARKET_DATABASE_URL="$MIGRATION_TEST_URL" \
INVESTMENT_STUDIO_BRIEFING_DATABASE_URL="$MIGRATION_TEST_URL" \
  infra/scripts/verify_repository.sh migration-heads
```

密码只放在测试用户的 `0600` `.pgpass`。该数据库不能承载业务数据，并且不能有运行中的业务 writer。

完整 PostgreSQL integration gate 使用另一项显式变量：

```bash
INVESTMENT_STUDIO_TEST_POSTGRES_URL='postgresql+psycopg://test_role@127.0.0.1:5432/investment_studio_test_control' \
  infra/scripts/verify_repository.sh postgres-integration all
```

Portfolio / Watchlist suites 会在同一个 PostgreSQL 实例里临时创建并删除独立数据库，因此测试角色需要
`CREATE DATABASE / DROP DATABASE`；Data suite 同样创建独立测试数据库。控制库必须由测试角色拥有且不含业务数据；源码目录不读取或保存测试 `.env`。

## Repository Hygiene

- 仓库不再保存任何运行时 SQLite 数据文件。
- 如果目录里出现 `*.db / *.sqlite / *.sqlite3`，应视为临时本地产物并删除，而不是提交。
- SQLite 仅保留在测试夹具中，通过 `tmp_path` 动态生成。
- `nav/` 是迁移保留的 NAV 附件图片数据，已经纳入 Git；后续新增大批量原始材料前先判断是否应该进入仓库。
- `node_modules/` 与 frontend `dist/` 只由本地 install/build 生成，不作为提交内容。
