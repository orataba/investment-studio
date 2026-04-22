# Yungu Platform

这是 `Yungu` 的平台入口和 shared asset ops app。

当前状态：

- `frontend/` 与 `backend/` 都已可运行
- `Platform` 只负责平台首页、app switcher 和 shared asset ops
- `Platform` 直接维护 `shared_asset` schema，但不是 `Watchlist` / `Portfolio` 的运行时数据中转层

## 当前职责

- 展示 `Yungu` 平台首页
- 提供到 `Watchlist` 与 `Portfolio` 的入口
- 提供 shared asset ops API 与 dashboard，维护 `Instruments / FX / market facts`
- 在前端用 `/api/apps` 暴露 app registry

## 当前不承载

- 其他 app 的核心运行时依赖
- 跨 app 业务编排
- 共享 detail 页面
- 共享业务 read model

## 目录

- `frontend/`
  平台 landing / app switcher / shared asset ops dashboard
- `backend/`
  平台 backend，提供健康检查、app registry 与 shared asset ops API

## Shared Asset Migration

`shared_asset` schema 的 Alembic 入口在 [infra/shared_asset](../../infra/shared_asset/README.md)，不在 `apps/platform/backend`。

跨 app 的数据库和平台边界说明见 [../../docs/README.md](../../docs/README.md)。

## 快速启动

### 1. 后端

```bash
cd /home/shaw/yungu/apps/platform/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn platform_app.main:app --reload --host 127.0.0.1 --port 8002
```

说明：

- 默认数据库连接是 `postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu`
- backend 顶层包名现在是 `platform_app`
- `platform` 运行时默认使用 `shared_asset` schema
- shared schema 的迁移请去 `infra/shared_asset`

### 2. 前端

```bash
cd /home/shaw/yungu/apps/platform/frontend
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
cd /home/shaw/yungu/apps/platform/backend && pytest
cd /home/shaw/yungu/apps/platform/frontend && npm run build
cd /home/shaw/yungu/infra/shared_asset && alembic upgrade head
```
