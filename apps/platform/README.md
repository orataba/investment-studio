# Portfolio Operations Workbench Platform

这是 `Portfolio Operations Workbench` 的平台入口和 `Database Dashboard` app。

当前状态：

- `frontend/` 与 `backend/` 都已可运行
- `Platform` 只负责平台首页、app switcher 和 `Database Dashboard`
- `Platform` 直接维护 `instrument_registry` schema，并在共享市场数据更新后通知 downstream app 刷新物化读模型
- `Platform` 对 `Watchlist` 的入口和 app registry 文案应反映当前真实发布范围：fund-only，Copilot 仅保留 backend extension boundary

## 当前职责

- 展示 `Portfolio Operations Workbench` 平台首页
- 提供到 `Watchlist` 与 `Portfolio` 的入口
- 提供 `Database Dashboard` API 与页面，维护 `Instruments / FX / NAV / market facts`
- 在 `Database Dashboard` 里支持手工录入、CSV/Excel 导入、邮件刷新，并展示选中资产的共享市场数据与净值历史
- 在共享市场数据更新后，触发 `Watchlist` 资产 read model 重算，并触发 `Portfolio` daily snapshots 刷新
- 在前端用 `/api/apps` 暴露 app registry

## 当前不承载

- 其他 app 的核心运行时依赖
- 其他 app 的业务计算；Platform 只发出 market-data update 通知
- 共享 detail 页面
- 共享业务 read model

## 目录

- `frontend/`
  平台 landing / app switcher / Database Dashboard
- `backend/`
  平台 backend，提供健康检查、app registry 与 `Database Dashboard` API

## Instrument Registry Migration

`instrument_registry` schema 的 Alembic 入口在 [infra/instrument_registry](../../infra/instrument_registry/README.md)，不在 `apps/platform/backend`。

跨 app 的数据库和平台边界说明见 [../../docs/README.md](../../docs/README.md)。

前端视觉约束见 [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)；Platform 页面保持白底数据终端风格，不再使用米黄或暖色渐变背景。

## Local NAV Imports

仓库根目录的 `nav/` 只作为本机 NAV / Excel 文件导入暂存目录，供 [backend/scripts/import_coverage_nav_from_folder.py](./backend/scripts/import_coverage_nav_from_folder.py) 读取。

这些文件包含迁移保留的运行数据，当前已作为私有仓库恢复资产提交；后续新增大批量原始材料前先确认是否应进入仓库。

Database Dashboard 的邮件刷新使用显式产品规则匹配发件人、主题、附件名与行级产品信息。非 full-history 刷新会先读取该 instrument 已有最新 NAV 日期，再用 IMAP `SINCE` 缩小邮件搜索范围，并在解析后过滤早于该 NAV 日期的行；若没有历史 NAV，才退回最近邮件窗口。IMAP 连接超时由 `email_imap_timeout_seconds` 控制，避免邮件服务器阻塞整个刷新请求。

Database Dashboard 也支持 Tushare SDK 兼容刷新。将 instrument 的 `Source Mode` 设为 `API`，`API Profile` 设为 `tushare` 后，后端会用 `tushare` Python SDK 调用，并把 SDK 的 `_DataApi__http_url` 指向 `PORTFOLIO_OPS_PLATFORM_TUSHARE_API_URL`，默认值为 `https://fastapic.stockai888.top`。公募 `.OF` 代码通过 `fund_nav` 写入 `official_nav / total_return_nav`，场内基金 `.SH/.SZ` 通过 `fund_daily` 写入 `price/close`，指数 `.SH/.SZ/.CSI/.CNI` 通过 `index_daily` 写入 `price/close`。Tushare token 只从仓库外的 `platform.env` 或显式进程环境读取；backend 目录中的 `.env.example` 仅说明键名，不承载真实值。

后台定时刷新使用 [backend/scripts/refresh_market_data_scheduled.py](./backend/scripts/refresh_market_data_scheduled.py)。默认依次刷新 Tushare 和邮件；成功写入后同步等待 Portfolio 快照刷新，并让 Watchlist 可靠入队后由 worker 异步重算。安装每天 21:00 刷新 timer：

```bash
PYTHON_BIN=/home/shaw/miniconda3/envs/us_sector_rotation/bin/python \
  infra/systemd/install_market_data_refresh_timer.sh
```

服务器部署时在服务器项目目录执行同一个脚本，并把 `PYTHON_BIN` 指向服务器后端运行环境。timer 默认按 `*-*-* 21:00 Asia/Shanghai` 运行，日志追加到 `~/.local/state/portfolio-ops/logs/market-data-refresh.log`。脚本使用 `fcntl` 锁拒绝重叠运行，原子保存最近一次摘要，并先对失败 instrument 做内部重试；默认把单项失败或下游重算失败报告为非零退出状态，由 systemd 的有界重启策略处理。macOS 使用仓库根目录的 `infra/launchd/install_local_services.sh` 安装同为每日 21:00 的 LaunchAgent。

## Downstream Refresh

`Database Dashboard` 更新共享市场数据后，Platform 会通过后台通知刷新 downstream app：

- `Watchlist`: 调用 `/api/recalc/instruments/{instrument_id}/all`，重算 summary / chart / performance / risk / row read models。
- `Portfolio`: 调用 `/api/portfolios/snapshots/daily/refresh`，按受影响资产刷新 daily snapshots；FX 更新会刷新全部组合。

这些通知要求本地 `PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL` 和 `PORTFOLIO_OPS_PLATFORM_PORTFOLIO_API_URL` 指向正在运行的 app backend。通知失败不会回滚共享数据写入；Watchlist 仍保留读路径 stale repair，Portfolio 也会在读路径发现状态过期时修复。

## 快速启动

以下命令默认从仓库根目录执行；如果已经在 `apps/platform` 目录，可相应省略路径前缀。

### 1. 后端

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file platform "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_PLATFORM_
: "${PORTFOLIO_OPS_PLATFORM_DATABASE_URL:?external platform.env must set the canonical database URL}"
cd apps/platform/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn platform_app.main:app --reload --host 127.0.0.1 --port 8002
```

说明：

- `PORTFOLIO_OPS_PLATFORM_DATABASE_URL` 必须由仓库外的 `platform.env` 显式提供，并与 Watchlist、Portfolio 指向同一个 canonical PostgreSQL；backend 目录只保留 `.env.example` 键名模板，不创建 `.env` 文件或软链接
- backend 顶层包名现在是 `platform_app`
- `platform` 运行时默认使用 `instrument_registry` schema
- instrument registry schema 的迁移请去 `infra/instrument_registry`

### 2. 前端

```bash
cd apps/platform/frontend
npm install
npm run dev
```

说明：

- Vite 默认仅监听 `127.0.0.1:5172`；如需远程访问，请通过受控的反向代理显式开放
- `/api` 默认代理到 `http://127.0.0.1:8002`

## Frontend Runtime Config

platform frontend 不再把 backend / watchlist / portfolio 地址写死在代码里。

- `VITE_API_BASE_URL`
  当前前端访问 platform backend 的基地址；留空时默认走同源 `/api/*`
- `VITE_DEV_PROXY_TARGET`
  本地 `vite dev` 代理目标；不配时默认转到 `http://127.0.0.1:8002`
- `VITE_PLATFORM_NAME`
  backend app registry 不可用时的前端兜底平台名
- `VITE_WATCHLIST_URL`
- `VITE_WATCHLIST_API_URL`
- `VITE_PORTFOLIO_URL`
- `VITE_PORTFOLIO_API_URL`

其中 `WATCHLIST/PORTFOLIO` 这组变量只在 platform backend 的 `/api/apps` 不可用时作为前端兜底入口使用。

## 常用校验命令

```bash
(cd apps/platform/backend && pytest)
npm --prefix apps/platform/frontend run build
(cd infra/instrument_registry && alembic upgrade head)
```
