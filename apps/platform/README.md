# Portfolio Operations Workbench Platform

这是 `Portfolio Operations Workbench` 的平台入口和 `Database Dashboard` app。

当前状态：

- `frontend/` 与 `backend/` 都已可运行
- `Platform` 只负责平台首页、app switcher 和 `Database Dashboard`
- `Platform` 直接维护 `instrument_registry` 中的 canonical 市场事实，在私有 `platform` schema 保存数据摄取运行状态，并在共享市场数据更新后通知 downstream app 刷新物化读模型
- `Platform` 对 `Watchlist` 的入口和 app registry 文案应反映当前真实发布范围：fund-only，Copilot 仅保留 backend extension boundary

## 当前职责

- 展示 `Portfolio Operations Workbench` 平台首页
- 提供到 `Watchlist` 与 `Portfolio` 的入口
- 平台首页保持为轻量 app switcher，只提供 `Watchlist / Portfolio / Instrument Registry` 入口，不承载运行状态、摄取队列或数据明细
- 提供 `Database Dashboard` API 和独立的 `Instrument Registry` 管理工作区，维护 `Instruments / FX / NAV / market facts`
- `Instrument Registry` 按“定位 canonical 对象 → 判断数据覆盖与来源状态 → 受控修订或导入 → 生命周期和审计治理”组织；列表只承担发现，单对象检查器承担序列、来源与治理，低频写操作进入独立操作抽屉
- 支持单品种完整 typed series 的筛选、分页和 CSV 下载；非基金数据点按既有 typed key 做 upsert 修订，基金 NAV 继续使用批量导入、分红/拆分证据和 revision chain，不暴露绕过 lineage 的直接编辑
- instrument 的“删除”采用可恢复 archive/restore 语义：默认 downstream discovery 隐藏 archived instrument，但保留 identifiers、market facts 和审计历史；前端不提供 hard delete
- 持久化邮箱目录游标、原始 NAV 证据、附件解析、重试和候选路由，使定时任务可增量、幂等恢复
- 在共享市场数据更新后，触发 `Watchlist` 资产 read model 重算，并触发 `Portfolio` daily snapshots 刷新
- 在前端用 `/api/apps` 暴露 app registry

## 当前不承载

- 其他 app 的核心运行时依赖
- 其他 app 的业务计算；Platform 只发出 market-data update 通知
- 共享 detail 页面
- 共享业务 read model

## 目录

- `frontend/`
  极简 landing / app switcher / Instrument Registry
- `backend/`
  平台 backend，提供健康检查、app registry 与 `Database Dashboard` API

## Database Migrations

`instrument_registry` schema 的 Alembic 入口在 [infra/instrument_registry](../../infra/instrument_registry/README.md)。`apps/platform/backend/alembic` 只管理 Platform 私有 `platform` schema；不要从 Platform migration 修改 Registry canonical 表。

发布和新库安装统一使用仓库根目录的 `infra/scripts/migrate_all.sh`。它会先建立原始证据持久化能力，再执行 Registry 的 canonical NAV 清理；不要无序地对各 migration chain 单独执行 `upgrade head`。

跨 app 的数据库和平台边界说明见 [../../docs/README.md](../../docs/README.md)。

前端视觉约束见 [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)；Platform 页面保持白底数据终端风格，不再使用米黄或暖色渐变背景。

## Local NAV Imports

仓库根目录的 `nav/` 只作为本机 NAV / Excel 文件导入暂存目录，供 [backend/scripts/import_coverage_nav_from_folder.py](./backend/scripts/import_coverage_nav_from_folder.py) 读取。

这些文件包含迁移保留的运行数据，当前已作为私有仓库恢复资产提交；后续新增大批量原始材料前先确认是否应进入仓库。

Database Dashboard 的邮件刷新使用显式产品规则匹配发件人、主题、附件名与行级产品信息。配置的所有邮箱目录（包括仍承载被跟踪基金的 `INBOX`）分别以 `UIDVALIDITY + UID` 保存高水位；正常运行只发现新 UID，首次建游标和显式 full-history 都由 `PORTFOLIO_OPS_PLATFORM_EMAIL_HISTORY_START_DATE` 限定历史边界，本地当前从 `2025-12-26`（含）开始，以覆盖周频产品计算 YTD 所需的年末最后一个可用净值。IMAP 先用 `SINCE` 在服务端缩小范围，本地再按邮件头日期校验；更早的已保存 header 会带 `BeforeHistoryStartDate` 原因进入 ignored，不下载正文。附件内嵌的净值日期也受同一边界约束，更早的行保留为 rejected 原始证据，不参与路由、发布或重算。系统先批量抓取轻量 header；主题中的 active fund 名称或标识也可触发候选正文抓取，但最终入库仍要求精确行身份或单目标正向规则。再只为候选邮件抓取正文和附件。附件按 SHA-256 去重，解析结果再按 parser 版本缓存；工作簿会汇总所有 worksheet 并保留 sheet 来源。不支持的候选附件、超限附件和空解析都会进入可监控的 unsupported/dead-letter，而不是静默跳过。短暂错误进入有界重试，永久错误进入 dead-letter，不通过反复全量扫箱掩盖故障。

邮件历史边界同时约束邮件来源基金的全部耐久原始证据，包括迁移保留的旧 Registry 快照。每次任务会主动发现并原子重建仍含越界日期的当前投影，因此收窄边界不需要手工删库，审计证据也不会丢失。

同一邮箱的扫描由数据库有时限租约串行化，批次间续租；游标完成和失败写入均受租约 token 与 `UIDVALIDITY` 围栏保护。API 与定时任务重叠时，后到任务快速失败且不会启动第二次整箱扫描；进程崩溃后租约到期可自动接管。

邮箱文件夹是覆盖和提速配置，不是基金 universe。日频、周频是 freshness/SLA 属性，也不能决定是否扫描某个目录。要跟踪的目录必须全部写入 `PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_FOLDERS`，其中应保留 `INBOX`；归档到产品专用目录的同一附件会由内容哈希去重。

未注册基金的有效解析行会作为 `unmatched` 原始证据留在邮件盘点中，不能按 skipped 丢弃。管理员以精确代码或精确基金名补建 Registry 主档后，下次邮件任务只重放这些已落库的有效路由，不重新下载或解析历史邮箱；invalid 仍拒绝，ambiguous 仍等待人工消歧，系统不根据模糊名称自动建档。

本地当前应配置为 `INBOX,jingxuan1,junheng,yungu3`。首次全量盘点可显式运行 full-history；它从最新 UID 向历史边界回填，建立各目录游标后每日任务只处理新增 UID。

邮件或文件中解析出的值先作为 Platform 私有原始证据保存。Registry 对基金只接受 `official_nav`（单位净值）和 `total_return_nav`（分红再投资复权累计净值）。单位净值加历史现金分红的普通累计净值不得写成 `total_return_nav`；缺少可信供应商复权序列，或缺少完整分红/再投资信息时，累计净值保持 unavailable/NA。系统不以单位净值、现金累计值或交易价格兜底制造回报曲线。

Database Dashboard 也支持 Tushare SDK 兼容刷新。将 instrument 的 `Source Mode` 设为 `API`，`API Profile` 设为 `tushare` 后，所有调用都通过 [tushare_client.py](./backend/platform_app/services/tushare_client.py) 初始化 `pro`，并把 SDK 的 `_DataApi__http_url` 指向 `PORTFOLIO_OPS_PLATFORM_TUSHARE_API_URL`，默认值为 `https://ttx.dailyfetch.top/`；`pro_bar` 也统一由该模块按 `ts.pro_bar(api=pro, ...)` 调用。公募 `.OF` 代码通过 `fund_nav` 写入 `official_nav / total_return_nav`，场内基金 `.SH/.SZ` 通过 `fund_daily` 写入 `price/close`，指数 `.SH/.SZ/.CSI/.CNI` 通过 `index_daily` 写入 `price/close`。Tushare token 只从仓库外的 `platform.env` 或显式进程环境读取；backend 目录中的 `.env.example` 仅说明键名，不承载真实值。

`H11001.CSI` 是显式例外：Registry 必须同时配置
`source_api_fallback_profile=csindex` 与 `source_api_fallback_code=H11001`，
系统才会在 Tushare `index_daily` 没有新行时调用中证指数官网的
`/perf/index-perf` 公共接口。备用源只写官方 `close`，provider 记为
`csindex:index-perf`；接口没有 OHLCV 时保持缺失，不根据收盘值合成 K 线。
端点与超时可分别通过 `PORTFOLIO_OPS_PLATFORM_CSINDEX_API_URL` 和
`PORTFOLIO_OPS_PLATFORM_CSINDEX_TIMEOUT_SECONDS` 覆盖。

指数的 provider 字段名与收益口径分开管理。`close` 只说明行情字段，不能自动等同于全收益；Database Dashboard 的 `Index Return Semantics` 必须按指数公司代码说明标记为 `Price return`、`Total return` 或 `Unknown`。例如普通沪深300代码与其全收益衍生代码是两条不同指数。该标记进入共享 Registry，Portfolio 再据此决定基准比较口径。

后台定时刷新使用 [backend/scripts/refresh_market_data_scheduled.py](./backend/scripts/refresh_market_data_scheduled.py)。默认依次刷新 Tushare、邮件，再用 Registry 精确查询只重建方法版本落后的当前基金净值投影；最后同步等待 Portfolio 刷新响应，并让 Watchlist 可靠入队后由 worker 异步重算。投影协调不访问行情源或邮箱，也可用 `--channel projection` 单独执行。安装每天 21:00 刷新 timer：

```bash
PYTHON_BIN=/home/shaw/miniconda3/envs/us_sector_rotation/bin/python \
  infra/systemd/install_market_data_refresh_timer.sh
```

服务器部署时在服务器项目目录执行同一个脚本，并把 `PYTHON_BIN` 指向服务器后端运行环境。timer 默认按 `*-*-* 21:00 Asia/Shanghai` 运行，日志追加到 `~/.local/state/portfolio-ops/logs/market-data-refresh.log`。脚本使用 `fcntl` 锁拒绝重叠运行，原子保存最近一次摘要，并先对失败 instrument 做内部重试；默认把单项失败或下游重算失败报告为非零退出状态，由 systemd 的有界重启策略处理。macOS 使用仓库根目录的 `infra/launchd/install_local_services.sh` 安装在加载时先运行、并每日 21:00 再运行的 LaunchAgent。

## Downstream Refresh

`Database Dashboard` 更新共享市场数据后，Platform 会通过后台通知刷新 downstream app：

- `Watchlist`: 调用 `/api/recalc/bulk` 将受影响基金写入持久化、按基金去重的重算队列。
- `Portfolio`: 调用 `/api/portfolios/snapshots/daily/recalculations` 持久化合并重算请求并立即返回；同一组合串行计算，FX 更新会使全部组合失效并由 worker 重算。

这些通知要求本地 `PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL` 和 `PORTFOLIO_OPS_PLATFORM_PORTFOLIO_API_URL` 指向正在运行的 app backend。通知失败不会回滚共享数据写入；两个 worker 都会按 Registry source generation 做有界主动对账，因此进程重启或通知丢失后仍会自动补算。完整事件、因子和并发合同见 [FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md](../../docs/FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md)。

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
- `platform` 运行时固定使用 `platform, instrument_registry, public` search-path 顺序
- `database_schema=instrument_registry` 代表 canonical facts，`operations_database_schema=platform` 代表私有 ingestion state
- instrument registry schema 的迁移请去 `infra/instrument_registry`，Platform 私有 schema 的迁移位于 `apps/platform/backend/alembic`

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
infra/scripts/migrate_all.sh
```
