# New Machine Restore

本 runbook 用 Git 中的代码和受控外部状态，在新机器上恢复一套可运行的 Investment Studio。它不复制旧机器的虚拟环境、依赖目录、PostgreSQL raw data directory 或用户级服务文件。

## 1. 需要的外部状态

仓库以外必须取得：

- `home.env`（仅登录与导航）、`data.env`（后台数据维护）、`watchlist.env`、`portfolio.env`、`market.env`、`briefing.env`，以及报告生成所需的 DeepSeek 配置；
- PostgreSQL custom-format dump；
- 与 dump 同目录的 SHA-256 文件；
- 数据供应商、邮箱和数据库凭据。
- `~/.local/share/investment-studio/` 下的 Watchlist 上传文档与 Portfolio 计算产物；
- 与数据库元数据匹配的公共数据目录（规范 Parquet、raw、文本原文及包回执），具体范围见 [Market Data Pipeline](./MARKET_DATA_PIPELINE.md)。

此 runbook 恢复 Studio 管理的业务与共享数据。Regime 自有模型数据库、runtime 和部署仍由子模块维护；它读取及写入的公共市场资料归 Studio 管理。恢复共享分区前须另外停止或等待 Regime source writer。Mac 和云端的 Portfolio/Watchlist 私有事实独立，不能用公共数据同步或云端 dump 覆盖本机私有账本。

当前完整 dump 包含 `identity`、`instrument_data`、`data_ingestion`、`portfolio`、`watchlist`、`market_data`、`market_text`、`briefing` 八个 schema。迁移前的备份可分别使用旧名 `instrument_registry`、`platform` 代替前两个分区，恢复后会运行原地改名迁移；同一分区的新旧名不能同时存在。缺少整个摄取分区的制品无法恢复抓取游标和原始证据，不是有效恢复制品。

Secrets 放在受控目录，目录权限 `0700`、文件权限 `0600`。backend 目录不得出现 `.env` 或软链接。数据库密码写入当前用户的 `0600` `.pgpass`，不进入连接 URL 或 shell history。

## 2. 安装工具并取得代码

需要 uv、Python 3.12、Node.js/npm、PostgreSQL client tools，以及本机或容器化 PostgreSQL。macOS 可使用：

```bash
brew install uv node postgresql@17
git clone --recurse-submodules REPOSITORY_URL investment-studio
cd investment-studio
git status --short --branch
```

安装锁定依赖：

```bash
infra/scripts/sync_python_env.sh
npm --prefix shared-data/instruments/ts ci
npm --prefix home/frontend ci
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/briefing/frontend ci
```

## 3. 准备 PostgreSQL

仓库的本地 profile 会创建 `investment_studio` database 和 role：

```bash
(cd infra/postgres && docker compose up -d)
pg_isready -h 127.0.0.1 -p 5432 -U investment_studio -d investment_studio
```

也可以使用现有 PostgreSQL，但数据库目标、角色和 `.pgpass` 必须在运行恢复脚本前显式准备好。

## 4. 恢复数据库与公共数据文件

先确认制品权限和 checksum 文件名：

```bash
export RESTORE_DUMP=/absolute/secure/path/investment-studio.pgdump
export RESTORE_CHECKSUM=/absolute/secure/path/investment-studio.sha256
chmod 600 "$RESTORE_DUMP" "$RESTORE_CHECKSUM"
(cd "$(dirname "$RESTORE_DUMP")" && \
  shasum -a 256 -c "$(basename "$RESTORE_CHECKSUM")")
```

再调用唯一恢复入口：

```bash
INVESTMENT_STUDIO_DUMP_CHECKSUM_PATH="$RESTORE_CHECKSUM" \
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
CONFIRM_RESTORE=investment_studio \
  infra/postgres/restore_project_dump.sh "$RESTORE_DUMP"
```

脚本会复核 checksum、archive 和目标，停止已安装的写服务，备份现有八个 schema，断开残留连接，恢复 dump，并依赖感知地升级全部 migration。失败时自动恢复操作前备份；自动恢复失败时保持服务停止并保留 recovery path。

在恢复服务前，将匹配的公共数据文件恢复到 `market.env` 指定的数据目录，并核对数据集批次、原文版本及文件可读性；单独恢复 PostgreSQL 不能恢复完整市场资料。

恢复成功后不要运行 `rebuild_local_schemas.sh`，除非明确要删除刚恢复的业务数据并重建空 schema。恢复与破坏性重建的完整边界见 [Database Workflow](./DATABASE_WORKFLOW.md)。

## 5. 安装运行服务

macOS 使用统一安装器完成迁移复核、前端构建、八个 API/前端 LaunchAgent、每日刷新、数据审计和健康检查：

```bash
infra/harness/install.sh
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  infra/launchd/install_local_services.sh
infra/launchd/status_local_services.sh
```

研究运行时使用 Node 24、pnpm 11 或 12，安装器按提交内的依赖锁安装固定 DSH；
运行研究时直接启动当前版本的已安装程序，不临时联网安装依赖。

调度、日志和卸载见 [macOS Local Service](./LOCAL_MACOS_SERVICE.md)。Linux 服务器不要复制上述 launchd 步骤，按 [Server Deployment](./SERVER_DEPLOYMENT.md) 安装 user-systemd 服务。只做交互式开发时，按对应 app README 启动对应后端和前端，无需先安装常驻服务。

## 6. 恢复后追数

安装器加载晚间结算任务时会根据 durable run state 判断是否需要补跑；盘后行情和盘前资料任务只注册日历计划。首次上线须先完成项目参考资料采集，再启动研究。若净值数据明显早于当前可用日期，可手工触发同一个受控结算任务：

```bash
launchctl kickstart "gui/$UID/com.orataba.investment-studio.market-data-refresh"
```

各市场行情和研究资料的对应任务、时区与手动追数边界见 Local macOS Service；Linux 的六组 timer/service 见 Server Deployment。行情和结算完成后由 Data 作业通知 Watchlist 和 Portfolio；durable workers 也会主动对账漏通知。

## 7. 验收

至少完成：

```bash
infra/scripts/verify_repository.sh static
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  .venv/bin/python infra/scripts/audit_live_data.py --fail-on-warning --json
```

恢复入口和托管服务安装器已经在停写、备份与回滚边界内应用 migration；live-data audit 会核对六条
migration chain 的版本。不要对刚恢复的业务库裸跑 `migration-heads`：该门禁会执行 upgrade，专项 migration
验证应按 [Database Workflow](./DATABASE_WORKFLOW.md) 使用独立测试数据库。

并确认：

- 八个 schema 的七条 migration chain 均处于当前 head，公共目录与原文文件可读；
- 八个 API/前端服务健康，Home、Watchlist、Portfolio、Briefing 页面可打开；Regime 按自身恢复流程独立验收；
- secrets、dump、checksum、日志、运行数据库和依赖目录均未进入 Git；
- 最近刷新状态成功，或明确记录仍需追数的供应商/邮箱原因；
- 恢复后的 Portfolio、账户、交易和 Watchlist 数量来自业务库，而不是 demo seed。

需要验证整套开发环境时，再运行 `infra/scripts/verify_repository.sh all-local`；PostgreSQL integration tests 必须使用独立测试数据库，不能复用刚恢复的业务库。
