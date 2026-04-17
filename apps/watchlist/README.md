# Yungu Watchlist

`/home/shaw/yungu/apps/watchlist` 是当前 `Yungu / Watchlist` app 的工作区。

它由原 `fof` 基线工程迁入，当前已经开始从旧的 fund/watchlist 语境收口到多资产 `instrument` 主语。

当前 app 已经包含：

- `backend/`
  FastAPI + SQLAlchemy + Alembic 的后端实现，默认目标数据库为 PostgreSQL
- `frontend/`
  React + Vite + TypeScript 的终端前端
- `docs/`
  经过清理后的工程基线文档，而不是历史规划稿

## 当前范围

当前主路径已经落到下面这条链路上：

1. `Watchlists`
2. `Instrument Detail`
3. `Quote / Performance / Risk / Exposure / Ratings / People / Strategy / Documents / Research`
4. `Facts ingest / manual profile / recalc / read model / Copilot`

其中：

- `Watchlists` 与单资产详情页是当前主界面
- Watchlist 里的资产新增只允许从平台 `Instruments` 共享库搜索并引用，不再在 Watchlist 内创建资产主档
- `/instruments` 已经是共享资产库入口，但非 fund 资产的 detail overlay 仍在补齐
- `Research / Documents / Monitoring` 仍有部分路由或页面是占位状态
- Copilot 已接入统一 API 协议，但默认 provider 仍为 `stub`

## 目录结构

```text
.
├── backend
│   ├── alembic
│   ├── app
│   │   ├── api
│   │   ├── db
│   │   ├── repositories
│   │   └── services
│   └── tests
├── docs
└── frontend
    └── src
```

## 快速启动

### 1. 后端

```bash
cd /home/shaw/yungu/apps/watchlist/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PYTHONPATH=. alembic upgrade head
uvicorn --app-dir . app.main:app --reload --host 127.0.0.1 --port 8000
```

说明：

- 默认数据库连接为 PostgreSQL：`postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu`
- `watchlist` 使用 `watchlist` schema
- 运行时不再提供任何样本/初始化脚本；数据库结构只通过 `alembic upgrade head` 管理
- FastAPI 根路径会重定向到前端 `watchlists`
- 由于多个 backend 都使用顶层包名 `app`，命令应在当前 backend 目录内执行，或显式使用 `--app-dir .`

### 2. 前端

```bash
cd /home/shaw/yungu/apps/watchlist/frontend
npm install
npm run dev
```

说明：

- Vite 默认监听 `http://127.0.0.1:5173`
- `/api` 已代理到 `http://127.0.0.1:8000`

## 常用校验命令

```bash
cd /home/shaw/yungu/apps/watchlist/frontend && npm run build
cd /home/shaw/yungu/apps/watchlist/frontend && npx tsc --noEmit
cd /home/shaw/yungu/apps/watchlist/backend && pytest
```

## 当前文档

- [docs/INDEX.md](./docs/INDEX.md)
- [docs/CURRENT_SYSTEM_BASELINE.md](./docs/CURRENT_SYSTEM_BASELINE.md)
- [docs/FUND_TERMINAL_V2_DATA_MODEL_AND_API.md](./docs/FUND_TERMINAL_V2_DATA_MODEL_AND_API.md)
- [docs/FUND_TERMINAL_V2_AI_COPILOT.md](./docs/FUND_TERMINAL_V2_AI_COPILOT.md)

## 继续推进时的原则

- 以当前仓库实现为准，不再维护“是否重建”的历史讨论稿
- 文档优先描述已经存在的结构、接口和下一步真实缺口
- 新需求先落到当前系统基线，再决定是否扩展数据模型或页面骨架
