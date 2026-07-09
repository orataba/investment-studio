# New Machine Restore

本文档用于在新电脑上从私有 GitHub 仓库恢复 `Portfolio Operations Workbench`。

恢复目标不是复制旧机器的运行时目录，而是用 Git 中的代码、配置、NAV 附件和数据库 dump 重建一套可运行环境。

## 0. 前置假设

- 仓库已从私有 GitHub clone 到本机。
- 本机可以访问 GitHub、PyPI/npm registry、Docker/PostgreSQL。
- backend `.env` 已随私有仓库提交，无需从旧机器单独复制；不要在聊天、issue、PR 描述或提交信息中粘贴 `.env` 的密钥值。
- 所有 backend 运行时环境变量统一使用 `PORTFOLIO_OPS_*` 前缀；本地数据库名、用户和密码统一为 `portfolio_ops`。

## 1. 安装系统工具

需要：

- Python 3.12+
- Node.js + npm
- Docker Desktop / Colima / Docker Engine
- PostgreSQL client tools: `psql`、`pg_isready`、`pg_restore`

macOS 可参考：

```bash
brew install python node postgresql@16
```

如果使用 Docker Desktop，确认 Docker daemon 已启动。

## 2. 拉取仓库

```bash
git clone git@github.com:orataba/pm.git
cd pm
git status --short --branch
```

确认工作区干净，且 `data/migration/`、`nav/`、三个 backend `.env` 文件都存在：

```bash
ls data/migration
test -f apps/platform/backend/.env
test -f apps/watchlist/backend/.env
test -f apps/portfolio/backend/.env
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

先校验 dump：

```bash
sha256sum -c data/migration/portfolio_ops_2026-07-08.sha256
```

macOS 如果没有 `sha256sum`，使用：

```bash
shasum -a 256 -c data/migration/portfolio_ops_2026-07-08.sha256
```

恢复：

```bash
PGPASSWORD=portfolio_ops \
pg_restore --clean --if-exists --no-owner --no-acl \
  -h 127.0.0.1 -U portfolio_ops -d portfolio_ops \
  data/migration/portfolio_ops_2026-07-08.pgdump
```

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
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e packages/instrument-core/python
pip install -e apps/platform/backend
pip install -e apps/watchlist/backend
pip install -e apps/portfolio/backend
```

如果后续给某个 backend 单独建 `.venv`，要确保该 venv 也安装了 `packages/instrument-core/python`。

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
npm --prefix apps/platform/frontend run dev -- --host 0.0.0.0 --port 5172
npm --prefix apps/watchlist/frontend run dev -- --host 0.0.0.0 --port 5173
npm --prefix apps/portfolio/frontend run dev -- --host 0.0.0.0 --port 5174
```

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

如果健康检查失败，先看对应 backend 的 `.env`、数据库连接和 venv 依赖。

## 10. 补齐快照之后的新数据

数据库 dump 是 `2026-07-08` 的恢复点。恢复完成后，建议手动跑一次全量调度入口，让行情、净值和下游物化读模型追到当前：

```bash
PYTHONPATH=/path/to/pm/apps/platform/backend:/path/to/pm/packages/instrument-core/python \
/path/to/pm/.venv/bin/python \
  /path/to/pm/apps/platform/backend/scripts/refresh_market_data_scheduled.py \
  --channel all \
  --updated-by restore \
  --retry-failed-attempts 2 \
  --fail-on-item-failure \
  --json
```

将 `/path/to/pm` 替换为新电脑上的仓库绝对路径。

## 11. 重建定时任务

Linux / WSL 使用 user systemd：

```bash
PYTHON_BIN="$PWD/.venv/bin/python" \
  infra/systemd/install_market_data_refresh_timer.sh

systemctl --user list-timers portfolio-ops-market-data-refresh.timer --all
systemctl --user cat portfolio-ops-market-data-refresh.service portfolio-ops-market-data-refresh.timer
```

默认时间是每天 `08:00 Asia/Shanghai`。如果要改时间：

```bash
ON_CALENDAR="Mon..Fri 08:00 Asia/Shanghai" \
PYTHON_BIN="$PWD/.venv/bin/python" \
  infra/systemd/install_market_data_refresh_timer.sh
```

macOS 不能直接运行 Linux `systemd --user` unit。先用前台命令确认刷新脚本稳定，再按需改成 `launchd`、Homebrew service、Docker job，或继续在 Linux/WSL/服务器上跑定时任务。

## 12. 最小验证清单

```bash
bash -n infra/systemd/install_market_data_refresh_timer.sh
git diff --check
(cd apps/platform/backend && pytest)
(cd apps/watchlist/backend && pytest)
(cd apps/portfolio/backend && pytest)
npm --prefix apps/platform/frontend run build
npm --prefix apps/watchlist/frontend run build
npm --prefix apps/portfolio/frontend run build
```

如果只是确认恢复能打开，至少完成数据库恢复、三个 health endpoints、三个前端页面访问、一次 `refresh_market_data_scheduled.py`。
