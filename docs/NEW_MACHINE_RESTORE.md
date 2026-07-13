# New Machine Restore

本文档用于在新电脑上从私有 GitHub 仓库恢复 `Portfolio Operations Workbench`。

恢复目标不是复制旧机器的运行时目录，而是用 Git 中的代码、配置、NAV 附件和当前项目级数据库 dump 重建一套可运行环境。

## 0. 前置假设

- 仓库已从私有 GitHub clone 到本机。
- 本机可以访问 GitHub、PyPI/npm registry、Docker/PostgreSQL。
- backend `.env` 不随仓库分发；真实值应从受控秘密存储恢复到 `~/.config/orataba/secrets/portfolio-operations-workbench/`，目录权限设为 `0700`、文件权限设为 `0600`。开发态可临时建立 backend 软链接，但安装 macOS 后台服务前必须移除。
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

确认工作区干净，且 `data/migration/`、`nav/` 和三个 backend `.env.example` 都存在。真实 `.env` 由本机秘密存储单独提供：

```bash
ls data/migration
test -f data/migration/portfolio_ops_2026-07-09_current.pgdump
test -f data/migration/portfolio_ops_2026-07-09_current.sha256
test -f apps/platform/backend/.env.example
test -f apps/watchlist/backend/.env.example
test -f apps/portfolio/backend/.env.example
```

## 3. 启动 PostgreSQL

```bash
(cd infra/postgres && docker compose up -d)

until pg_isready -h 127.0.0.1 -p 5432 -U portfolio_ops -d portfolio_ops; do
  sleep 1
done
```

这个本地 Docker profile 会创建 `portfolio_ops` 数据库、非超级运行角色 `portfolio_ops`
以及独立测试角色；镜像初始化使用的 bootstrap 管理员不会被应用连接。不要把 `.local-pg/`
raw data directory 放入 Git。

## 4. 恢复数据库快照

当前 Git 恢复点是 `2026-07-09 16:53 Asia/Shanghai` 的项目级快照，来自本地 `portfolio_ops` PostgreSQL 数据库，已包含 A 股 ETF 核心池导入、初始行情补数、All Covered watchlist 物化和本地数据库改名清理后的 Portfolio Operations 数据。它只包含 `instrument_registry`、`portfolio`、`watchlist` 三个 schema，不包含 `public` 或其他非项目 schema。

先校验 dump：

```bash
sha256sum -c data/migration/portfolio_ops_2026-07-09_current.sha256
```

macOS 如果没有 `sha256sum`，使用：

```bash
shasum -a 256 -c data/migration/portfolio_ops_2026-07-09_current.sha256
```

恢复：

```bash
PORTFOLIO_OPS_DB_HOST=127.0.0.1 \
PORTFOLIO_OPS_DB_PORT=5432 \
PORTFOLIO_OPS_DB_NAME=portfolio_ops \
PORTFOLIO_OPS_DB_USER=portfolio_ops \
CONFIRM_RESTORE=portfolio_ops \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=YYYY-MM-DD \
  infra/postgres/restore_project_dump.sh
```

恢复包装器不接受数据库 URL；它从上述 `PORTFOLIO_OPS_DB_*` 参数构造连接，并再次核对
实际 `current_database()`。本地默认密码为 `portfolio_ops`；使用其他密码时，从受保护的
进程环境导出 `PORTFOLIO_OPS_DB_PASSWORD`，不要把值写入文档或 Git。

恢复脚本会再次校验 checksum 和目标数据库，停止已安装的 launchd/systemd
应用服务，断开残留连接，并在破坏性操作前把当前三个 schema 备份到
`${XDG_STATE_HOME:-~/.local/state}/portfolio-operations-workbench/postgres-backups/`。
恢复、Alembic 升级、Portfolio/Watchlist 全量重建或零告警审计任一步失败时，脚本会自动清理半恢复状态、还原该备份，
然后再启动原先运行的服务。若自动回滚本身失败，服务会保持停止，且日志会
打印需要人工恢复的备份路径。

快速核对：

```bash
PGPASSWORD=portfolio_ops psql -h 127.0.0.1 -U portfolio_ops -d portfolio_ops -c "
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

三个终端分别运行，均使用第 5 步的 venv：

```bash
source .venv/bin/activate
(cd apps/platform/backend && uvicorn platform_app.main:app --host 127.0.0.1 --port 8002 --reload)
```

```bash
source .venv/bin/activate
(cd apps/watchlist/backend && uvicorn watchlist_app.main:app --host 127.0.0.1 --port 8000 --reload)
```

```bash
source .venv/bin/activate
(cd apps/portfolio/backend && uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001 --reload)
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

如果健康检查失败，先看外部秘密目录中的对应运行时配置、数据库连接和 venv 依赖。

## 10. 刷新快照之后的新数据

数据库 dump 是 `2026-07-09 16:12 Asia/Shanghai` 的恢复点。如果你在更晚日期恢复，恢复完成后可以手动跑一次全量调度入口，让行情、净值和下游物化读模型追到恢复当天：

```bash
PYTHONPATH=/path/to/pm/apps/platform/backend:/path/to/pm/packages/instrument-core/python \
/path/to/pm/.venv/bin/python \
  /path/to/pm/apps/platform/backend/scripts/refresh_market_data_scheduled.py \
  --channel all \
  --updated-by restore \
  --retry-failed-attempts 2 \
  --fail-on-item-failure \
  --require-downstream-success \
  --json
```

将 `/path/to/pm` 替换为新电脑上的仓库绝对路径。

## 11. 重建后台服务与定时任务

macOS 在仓库根目录执行统一安装器。它会安装六个常驻 LaunchAgent，并注册每天
本地时间 `21:00` 的独立行情刷新/自动重算任务；安装过程不会立即触发定时任务：

```bash
CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
  infra/launchd/install_local_services.sh
infra/launchd/status_local_services.sh
```

统一安装器会再次执行安全发布门禁：停止全部应用与刷新 writer，创建已校验的发布前备份，
确认迁移 head，按显式日期重建 Portfolio/Watchlist，通过零失败零告警审计并创建发布后备份，
之后才生成并启动 LaunchAgent。服务停止后的任一步失败都会保持托管服务停止。数据库发布
门禁本身若在变更后失败，会先尝试回滚到发布前备份；门禁已通过、随后前端构建或
LaunchAgent 启动失败时，保留已审计的新数据库并继续停服。

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
