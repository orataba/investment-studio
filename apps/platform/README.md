# Yungu Platform

这是 `Yungu` 的平台入口和 `Database Dashboard` app。

当前状态：

- `frontend/` 与 `backend/` 都已可运行
- `Platform` 只负责平台首页、app switcher 和 `Database Dashboard`
- `Platform` 直接维护 `instrument_registry` schema，并在共享市场数据更新后通知 downstream app 刷新物化读模型
- `Platform` 对 `Watchlist` 的入口和 app registry 文案应反映当前真实发布范围：fund-only，Copilot 仅保留 backend extension boundary

## 当前职责

- 展示 `Yungu` 平台首页
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

这些文件通常包含外部导出的运行数据，目录已加入 `.gitignore`，不再作为源码资产提交。

Database Dashboard 的邮件刷新使用显式产品规则匹配发件人、主题、附件名与行级产品信息。非 full-history 刷新会先读取该 instrument 已有最新 NAV 日期，再用 IMAP `SINCE` 缩小邮件搜索范围，并在解析后过滤早于该 NAV 日期的行；若没有历史 NAV，才退回最近邮件窗口。IMAP 连接超时由 `email_imap_timeout_seconds` 控制，避免邮件服务器阻塞整个刷新请求。

## Downstream Refresh

`Database Dashboard` 更新共享市场数据后，Platform 会通过后台通知刷新 downstream app：

- `Watchlist`: 调用 `/api/recalc/instruments/{instrument_id}/all`，重算 summary / chart / performance / risk / row read models。
- `Portfolio`: 调用 `/api/portfolios/snapshots/daily/refresh`，按受影响资产刷新 daily snapshots；FX 更新会刷新全部组合。

这些通知要求本地 `YUNGU_PLATFORM_WATCHLIST_API_URL` 和 `YUNGU_PLATFORM_PORTFOLIO_API_URL` 指向正在运行的 app backend。通知失败不会回滚共享数据写入；Watchlist 仍保留读路径 stale repair，Portfolio 也会在读路径发现状态过期时修复。

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

说明：

- 数据库连接通过 `YUNGU_PLATFORM_DATABASE_URL` 配置；本机账号和密码只应放在未提交的 `.env` 或 shell 环境里
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

- Vite 默认监听 `http://127.0.0.1:5172`
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
