# Portfolio Operations Workbench Platform

这是 `Portfolio Operations Workbench` 的平台入口和 `Database Dashboard` app。

当前状态：

- `frontend/` 与 `backend/` 都已可运行
- `Platform` 只负责平台首页、app switcher 和 `Database Dashboard`
- `Platform` 直接维护 `instrument_registry` schema；共享市场数据写事务原子失效 Portfolio Daily，并在同一事务写入 durable outbox，由独立 worker 通知 Watchlist 刷新读模型
- `Platform` 对 `Watchlist` 的入口和 app registry 文案应反映当前真实发布范围：fund-only，Copilot 仅保留 backend extension boundary

## 当前职责

- 展示 `Portfolio Operations Workbench` 平台首页
- 提供到 `Watchlist` 与 `Portfolio` 的入口
- 提供 `Database Dashboard` API 与页面，维护 `Instruments / FX / NAV / market facts`
- 在 `Database Dashboard` 里支持手工录入、CSV/Excel 导入、邮件刷新，并展示选中资产的共享市场数据与净值历史
- 在共享市场数据更新后，由 transactional outbox worker 通过 HTTP 触发 `Watchlist` 资产 read model 重算；`Portfolio Daily` 由同库事务触发器原子生成 generation/recompute intent
- 在前端用 `/api/apps` 暴露 app registry

## 当前不承载

- 其他 app 的核心运行时依赖
- 其他 app 的业务计算；Platform 只向仍需 HTTP 协调的 Watchlist 发出 market-data update 通知
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

Database Dashboard 也支持 Tushare SDK 兼容刷新。将 instrument 的 `Source Mode` 设为 `API`，`API Profile` 设为 `tushare` 后，后端会用 `tushare` Python SDK 调用，并把 SDK 的 `_DataApi__http_url` 指向 `PORTFOLIO_OPS_PLATFORM_TUSHARE_API_URL`，默认值为 `https://fastapic.stockai888.top`。公募 `.OF` 代码通过 `fund_nav` 写入 `official_nav / total_return_nav`，场内基金 `.SH/.SZ` 通过 `fund_daily` 写入 `price/close`，指数 `.SH/.SZ/.CSI/.CNI` 通过 `index_daily` 写入 `price/close`。纯开发进程可以读取 Git-ignored backend `.env` 或 shell 环境变量；macOS 后台服务只读取受保护的外部秘密目录，并拒绝仓库内 backend `.env` 文件或软链接。不要把 token 明文写进 Git、提交信息或聊天记录。

后台定时刷新使用 [backend/scripts/refresh_market_data_scheduled.py](./backend/scripts/refresh_market_data_scheduled.py)。默认依次刷新 Tushare 和邮件；成功写入的同一数据库事务会生成 Portfolio durable recompute intent 和 Watchlist 通知 outbox event，随后分别由常驻 worker 异步处理。安装每天 21:00 刷新 timer：

```bash
PYTHON_BIN=/home/shaw/miniconda3/envs/us_sector_rotation/bin/python \
  infra/systemd/install_market_data_refresh_timer.sh
```

服务器部署时在服务器项目目录执行同一个脚本，并把 `PYTHON_BIN` 指向服务器后端运行环境。timer 默认按 `*-*-* 21:00 Asia/Shanghai` 运行，日志追加到 `~/.local/state/portfolio-ops/logs/market-data-refresh.log`。脚本使用 `fcntl` 锁拒绝重叠运行，原子保存最近一次摘要，并先对失败 instrument 做内部重试；默认把单项失败或下游重算失败报告为非零退出状态，由 systemd 的有界重启策略处理。

macOS 通过仓库根目录的统一安全安装器安装同为每日 21:00 的 LaunchAgent。必须显式确认数据库 URL 所指向的目标和本次重建日期：

```bash
CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
  infra/launchd/install_local_services.sh
```

安装器会先停止全部应用与刷新 writer，再执行已校验备份、四条迁移链、Portfolio Daily 全组合发布、Watchlist 全量重建、零失败零告警审计和发布后备份。服务停止后的任一步失败都会保持托管服务停止。数据库发布门禁本身若在变更后失败，会先尝试恢复发布前备份；门禁已经通过、随后前端构建或 LaunchAgent 启动失败时，保留已审计的新数据库并继续停服。

## Downstream Refresh

`Database Dashboard` 更新共享市场数据后，Platform 在写事务内生成 durable outbox event，再由独立 worker 通过后台 HTTP 通知 Watchlist：

Watchlist outbox 的生产域明确限定为 `fund`、`etf`、`index`：这些资产新增行情 revision，或实质变更 quote-selection policy，都会生成带 source lineage 的刷新事件；股票、债券、现金与 FX 不生成该类事件。

- `Watchlist`: 调用 `/api/recalc/bulk`，可靠入队 summary / chart / performance / risk / row read-model 重算。
- `Portfolio`: 不发 HTTP。共享行情/FX 与 Portfolio 事实在同一 PostgreSQL 数据库内，由 `0040` dependency trigger 在写事务中递增精确 scope generation 并写 durable recompute intent。

Watchlist 通知要求 `PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL` 指向正在运行的 backend。通知失败会保留 pending event 并按退避策略重试，不会丢失已提交的共享数据；达到最大尝试次数的 dead event 会让 Platform readiness 失败，等待人工处理。Portfolio 失效与事实写入仍是同一数据库事务，失效失败会让事实写事务整体回滚。

### Dead-letter 恢复

先确认 Watchlist 接收失败的根因已经修复，再使用受控 CLI 恢复单个 dead event。命令要求重复确认同一个 UUID，且只接受当前状态为 `dead` 的记录：

```bash
cd apps/platform/backend
source .venv/bin/activate
python scripts/requeue_market_data_outbox_dead_letter.py \
  --event-id 11111111-1111-4111-8111-111111111111 \
  --confirm-event-id 11111111-1111-4111-8111-111111111111
```

恢复会原地执行 `dead → pending`，不会删除或重建事件；累计 `attempt_count` 和原 `last_error` 审计均保留，默认只给该事件增加 3 次 `max_attempts` 预算。必要时可显式传 `--additional-attempts N`，单次范围为 1–8。`available_at`、`updated_at` 和 `dead_at` 生命周期均由 PostgreSQL 时钟原子更新；event 不存在或不处于 `dead` 状态时命令拒绝修改并以非零状态退出。

## 快速启动

以下命令默认从仓库根目录执行；如果已经在 `apps/platform` 目录，可相应省略路径前缀。

### 1. 后端

```bash
cd apps/platform/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn platform_app.main:app --reload --host 127.0.0.1 --port 8002
```

另开终端启动 market-data outbox worker；它依赖 Watchlist API 已经运行：

```bash
cd apps/platform/backend
source .venv/bin/activate
python scripts/run_market_data_outbox_worker.py \
  --worker-id-prefix manual-platform-market-data-outbox
```

说明：

- 数据库连接通过 `PORTFOLIO_OPS_PLATFORM_DATABASE_URL` 配置；真实 backend `.env` 由本机受限秘密目录提供，不进入 Git
- backend 顶层包名现在是 `platform_app`
- `platform` 运行时默认使用 `instrument_registry` schema
- instrument registry schema 的迁移请去 `infra/instrument_registry`
- `/api/health` 只检查 API 进程存活；`/api/readiness` 还会检查数据库、migration head、worker 的新鲜 heartbeat 与最近成功 poll、dead event 和活动事件最长滞留时间

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
(
  export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=portfolio_ops
  cd infra/instrument_registry
  alembic upgrade head
)
```
