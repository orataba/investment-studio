# Portfolio Operations Workbench Platform

Platform 是平台入口、Instrument Registry 管理面和市场数据摄取应用。它拥有共享 canonical 资产事实的写路径，但不是 Watchlist 或 Portfolio 的业务运行时依赖。

## 职责与边界

Platform 负责：

- 平台首页、app switcher 和 Database Dashboard；
- instrument identity、identifier、quote policy、price/NAV/FX 与 corporate-action facts；
- 邮件游标、附件、解析结果、候选路由、重试和原始证据；
- 共享事实更新后的 downstream 重算通知。

Platform 不负责 Watchlist research/read model、Portfolio 账户/交易/计算，也不提供跨应用共享详情页。Watchlist 和 Portfolio 直接读取 Registry；通知丢失时由各自 durable worker 按 source generation 对账恢复。

数据所有权：

| Schema | 内容 |
| --- | --- |
| `instrument_registry` | 可跨应用复用的 canonical 资产与市场事实 |
| `platform` | 邮件和文件摄取的运行状态、原始证据与候选事实 |

`instrument_registry` migration 位于 [infra/instrument_registry](../../infra/instrument_registry/README.md)；Platform Alembic 只管理私有 `platform` schema。发布和恢复必须使用统一迁移入口，不能无序执行四条 migration chain。

## 数据源路由

每个 instrument 持久化一个主源，刷新时不自动 fallback，也不为同一序列双写：

| 对象 | 当前主路径 |
| --- | --- |
| 股票，包括 A/H/美股和已支持欧洲市场 | FMP |
| A 股公募与 A 股 ETF | DataHub Tushare |
| 港股、美股及已支持欧洲 ETF | FMP |
| 指数 | 建档时明确选择 FMP 或 DataHub Tushare |
| 私募基金 NAV | 邮件、受控文件或人工证据 |
| USD 基础 FX 序列 | FMP |

Provider 字段名与收益语义分开维护。`close` 不能自动当作 total return；基金对外只有 `official_nav` 和具备完整 lineage 的 `total_return_nav`。详细事件、复投证据、并发发布和下游重算合同见 [Fund NAV Event Model](../../docs/FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md)。服务器侧 DataHub transport 限制见 [Server Deployment](../../docs/SERVER_DEPLOYMENT.md)。

## 代码定位

```text
apps/platform/
  backend/platform_app/api/        HTTP routes 与 contracts
  backend/platform_app/services/   Registry、供应商、邮件和文件摄取
  backend/platform_app/db/         Platform 私有数据库会话与模型
  backend/scripts/                 受控导入和定时刷新入口
  frontend/src/                    landing 与 Database Dashboard
```

## 本地开发

先按根目录 README 安装依赖，并在仓库外准备 `platform.env`。从仓库根目录启动后端：

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file platform "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_PLATFORM_
: "${PORTFOLIO_OPS_PLATFORM_DATABASE_URL:?platform.env must set the canonical database URL}"
PYTHONPATH="$PROJECT_ROOT/apps/platform/backend:$PROJECT_ROOT/packages/instrument-core/python" \
  "$PROJECT_ROOT/.venv/bin/python" -m uvicorn platform_app.main:app \
  --host 127.0.0.1 --port 8002 --reload
```

启动前端：

```bash
npm --prefix apps/platform/frontend run dev -- --host 127.0.0.1 --port 5172
```

前端默认把 `/api` 代理到 `http://127.0.0.1:8002`。部署时使用同源 API；Watchlist 和 Portfolio 的页面/API 地址由 `/api/apps` 返回，不在组件中写死。

## 验证

```bash
infra/scripts/verify_repository.sh backend platform
infra/scripts/verify_repository.sh frontend platform
```

涉及 Registry migration、cross-schema FK、search path 或 PostgreSQL 连接行为时，再执行根目录 [Database Workflow](../../docs/DATABASE_WORKFLOW.md) 中的 migration-head 和 PostgreSQL integration gates。
