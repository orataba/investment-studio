# New Machine Restore

本文档用于在新电脑上从私有 GitHub 仓库恢复 `Portfolio Operations Workbench`。

恢复目标不是复制旧机器的运行时目录，而是用 Git 中的代码、配置、NAV 附件，以及从受控私有存储取得的项目级数据库 dump 重建一套可运行环境。

## 0. 前置假设

- 仓库已从私有 GitHub clone 到本机。
- 本机可以访问 GitHub、PyPI/npm registry、Docker/PostgreSQL。
- backend `.env` 不随仓库分发，也不要在仓库内建立文件或软链接。真实值应从受控秘密存储恢复到 `~/.config/orataba/secrets/portfolio-operations-workbench/`，目录权限设为 `0700`、文件权限设为 `0600`；下方手动启动命令通过只解析 namespaced dotenv assignment 的 loader 直接读取这一唯一来源。
- 数据库 dump 与 SHA-256 文件不随仓库分发；必须从批准的加密、受控存储取得，并在本机保持 `0600` 权限。
- PostgreSQL 密码通过交互方式写入当前用户权限为 `0600` 的 `.pgpass`；命令、URL 和 shell history 中不出现明文密码。
- 所有 backend 运行时环境变量统一使用 `PORTFOLIO_OPS_*` 前缀；本地数据库名、用户和密码统一为 `portfolio_ops`。

## 1. 安装系统工具

需要：

- `uv` 管理的 Python 3.12
- Node.js + npm
- Docker Desktop / Colima / Docker Engine
- PostgreSQL client tools: `psql`、`pg_isready`、`pg_restore`

macOS 可参考：

```bash
brew install uv node postgresql@17
```

如果使用 Docker Desktop，确认 Docker daemon 已启动。

## 2. 拉取仓库

```bash
git clone git@github.com:orataba/pm.git portfolio-operations-workbench
cd portfolio-operations-workbench
git status --short --branch
```

确认工作区干净，且 `nav/` 和三个 backend `.env.example` 都存在。外部 runtime env 与数据库恢复制品由受控存储单独提供：

```bash
test -f apps/platform/backend/.env.example
test -f apps/watchlist/backend/.env.example
test -f apps/portfolio/backend/.env.example

export RESTORE_DUMP=/absolute/secure/path/portfolio-operations-workbench.pgdump
export RESTORE_CHECKSUM=/absolute/secure/path/portfolio-operations-workbench.sha256
test -r "$RESTORE_DUMP"
test -r "$RESTORE_CHECKSUM"
```

## 3. 启动 PostgreSQL

```bash
(cd infra/postgres && docker compose up -d)

until pg_isready -h 127.0.0.1 -p 5432 -U portfolio_ops -d portfolio_ops; do
  sleep 1
done
```

这个本地 Docker profile 会创建 `portfolio_ops` 数据库和同名用户。不要把 `.local-pg/` raw data directory 放入 Git。

## 4. 恢复数据库快照

从批准的私有制品存储取得同目录下的 custom-format dump 与 SHA-256 文件。制品只允许包含 `instrument_registry`、`portfolio`、`watchlist` 三个 schema，不得包含 `public`、其他项目 schema 或 PostgreSQL raw data directory。Git 仓库不分发业务数据库快照。

先校验 dump：

```bash
(cd "$(dirname "$RESTORE_DUMP")" && \
  sha256sum -c "$(basename "$RESTORE_CHECKSUM")")
```

macOS 如果没有 `sha256sum`，使用：

```bash
(cd "$(dirname "$RESTORE_DUMP")" && \
  shasum -a 256 -c "$(basename "$RESTORE_CHECKSUM")")
```

恢复：

```bash
PORTFOLIO_OPS_DUMP_CHECKSUM_PATH="$RESTORE_CHECKSUM" \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
CONFIRM_RESTORE=portfolio_ops \
  infra/postgres/restore_project_dump.sh "$RESTORE_DUMP"
```

恢复脚本只接受 `PORTFOLIO_OPS_LOCAL_DATABASE_URL` 指定的单一显式目标，不读取另一组
默认 host/port/user/password。它会再次校验 checksum 和目标数据库，停止已安装的 launchd/systemd
应用服务，断开残留连接，并在破坏性操作前把当前三个 schema 备份到
`${XDG_STATE_HOME:-~/.local/state}/portfolio-operations-workbench/postgres-backups/`。
恢复或 Alembic 升级任一步失败时，脚本会自动清理半恢复状态、还原该备份，
然后再启动原先运行的服务。若自动回滚本身失败，服务会保持停止，且日志会
打印需要人工恢复的备份路径。

incoming dump 的 SHA-256 文件是强制输入，没有跳过校验的开关。停服并请求终止
客户端连接后，只要仍有任何其他连接，恢复就会在备份或删除 schema 之前硬失败。
迁移前备份和失败回滚与 launchd 安装器共用同一个 archive/manifest/checksum 原语，
避免两套恢复实现产生行为漂移。

快速核对：

```bash
psql -h 127.0.0.1 -U portfolio_ops -d portfolio_ops -c "
select 'instrument_registry.instrument' as table_name, count(*) from instrument_registry.instrument
union all
select 'portfolio.portfolio_record', count(*) from portfolio.portfolio_record
union all
select 'watchlist.watchlist', count(*) from watchlist.watchlist;
"
```

恢复后不要运行 `./infra/postgres/rebuild_local_schemas.sh`，除非明确要清空恢复数据并重建空 schema。

## 5. 安装 Python 依赖

推荐在仓库根目录使用一个共享 venv，便于本地验证和定时任务复用：

```bash
infra/scripts/sync_python_env.sh
source .venv/bin/activate
```

依赖版本由 `requirements/python.lock` 固定；更新 backend 依赖后需要重新生成并验证该锁文件。

## 6. 安装前端依赖

```bash
npm --prefix apps/platform/frontend install
npm --prefix apps/watchlist/frontend install
npm --prefix apps/portfolio/frontend install
```

## 7. 启动后端

三个终端分别从仓库根目录运行，均使用第 5 步的 venv，并显式、安全地加载对应的外部 secrets 文件。loader 会校验目录/文件所有者和权限，把 dotenv 当数据解析而不会执行其中的 shell 内容，同时拒绝仓库内 `.env` 文件或软链接。

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file platform "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_PLATFORM_
source "$PROJECT_ROOT/.venv/bin/activate"
(cd "$PROJECT_ROOT/apps/platform/backend" && \
  uvicorn platform_app.main:app --host 127.0.0.1 --port 8002 --reload)
```

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file watchlist "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_WATCHLIST_
source "$PROJECT_ROOT/.venv/bin/activate"
(cd "$PROJECT_ROOT/apps/watchlist/backend" && \
  uvicorn watchlist_app.main:app --host 127.0.0.1 --port 8000 --reload)
```

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file portfolio "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_PORTFOLIO_
source "$PROJECT_ROOT/.venv/bin/activate"
(cd "$PROJECT_ROOT/apps/portfolio/backend" && \
  uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001 --reload)
```

## 8. 启动前端

三个终端分别运行：

```bash
npm --prefix apps/platform/frontend run dev -- --host 127.0.0.1 --port 5172
npm --prefix apps/watchlist/frontend run dev -- --host 127.0.0.1 --port 5173
npm --prefix apps/portfolio/frontend run dev -- --host 127.0.0.1 --port 5174
```

Vite 默认仅监听本机回环地址；如需跨设备访问，请通过受控的反向代理显式开放。

访问：

- Platform: `http://127.0.0.1:5172`
- Watchlist: `http://127.0.0.1:5173`
- Portfolio: `http://127.0.0.1:5174`

## 9. 健康检查

```bash
curl --noproxy '*' http://127.0.0.1:8002/api/health
curl --noproxy '*' http://127.0.0.1:8000/api/health
curl --noproxy '*' http://127.0.0.1:8001/api/health
```

如果健康检查失败，先检查外部 secrets 目录中对应的 runtime env、数据库连接和 venv 依赖；不要在 backend 目录补建 `.env`。

## 10. 刷新快照之后的新数据

恢复点以取得的批准制品及其校验记录为准。恢复完成后，如果数据库中的最新行情日期早于当前可用数据日期，可以手动跑一次全量调度入口，让行情、净值和下游物化读模型追到恢复当天：

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file platform "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_PLATFORM_
PYTHONPATH="$PROJECT_ROOT/apps/platform/backend:$PROJECT_ROOT/packages/instrument-core/python" \
"$PROJECT_ROOT/.venv/bin/python" \
  "$PROJECT_ROOT/apps/platform/backend/scripts/refresh_market_data_scheduled.py" \
  --channel all \
  --updated-by restore \
  --retry-failed-attempts 2 \
  --fail-on-item-failure \
  --require-downstream-success \
  --json
```

## 11. 重建后台服务与定时任务

macOS 在仓库根目录执行统一安装器。它会安装六个常驻 LaunchAgent，并注册每天
本地时间 `21:00` 的独立行情刷新/自动重算任务；安装过程不会立即触发定时任务：

```bash
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  infra/launchd/install_local_services.sh
infra/launchd/status_local_services.sh
```

安装器只使用这里显式指定、且已经在前述步骤创建并恢复完成的数据库。迁移前会先
停止旧服务并保留一份经过 archive/checksum 校验的三个项目 schema 备份；后续迁移、
构建、plist 安装或健康检查失败时会先回滚数据库与旧 plist，再恢复原服务集合。
自动回滚失败时服务保持停止。

安装器会直接、安全地解析权限为 `0600` 的
`~/.config/orataba/secrets/portfolio-operations-workbench/platform.env`，同时供
Platform API 和定时任务使用；无需为了 launchd 另复制一份 token 或邮件密码。
launchd 路径会拒绝三个 backend 目录中的 `.env` 文件或软链接，避免外部配置被
Pydantic 的第二 dotenv 来源补入。

睡眠期间错过的日历触发会在唤醒时合并补跑一次；注销或关机时用户级
LaunchAgent 没有加载，因此不会在重新登录时追补。调度时间、锁、最近运行摘要
与日志说明见 [LOCAL_MACOS_SERVICE.md](./LOCAL_MACOS_SERVICE.md)。

Linux / WSL 使用 user systemd：

```bash
PYTHON_BIN="$PWD/.venv/bin/python" \
  infra/systemd/install_market_data_refresh_timer.sh

systemctl --user list-timers portfolio-ops-market-data-refresh.timer --all
systemctl --user cat portfolio-ops-market-data-refresh.service portfolio-ops-market-data-refresh.timer
```

默认时间是每天 `21:00 Asia/Shanghai`。如果要改时间：

```bash
ON_CALENDAR="Mon..Fri 22:00 Asia/Shanghai" \
PYTHON_BIN="$PWD/.venv/bin/python" \
  infra/systemd/install_market_data_refresh_timer.sh
```

## 12. 最小验证清单

```bash
bash -n infra/systemd/install_market_data_refresh_timer.sh
bash -n infra/launchd/install_local_services.sh
bash infra/tests/test_launchd_control_local_services.sh
bash infra/tests/test_launchd_plist_generation.sh
bash infra/tests/test_launchd_market_data_refresh_runner.sh
bash infra/tests/test_systemd_market_data_refresh_timer.sh
git diff --check
(cd apps/platform/backend && pytest)
(cd apps/watchlist/backend && pytest)
(cd apps/portfolio/backend && pytest)
npm --prefix apps/platform/frontend run build
npm --prefix apps/watchlist/frontend run build
npm --prefix apps/portfolio/frontend run build
```

如果只是确认恢复能打开，至少完成数据库恢复、三个 health endpoints、三个前端页面访问。恢复日期晚于快照日期时，再补跑一次 `refresh_market_data_scheduled.py`。
