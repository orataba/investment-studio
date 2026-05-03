# Yungu Watchlist

这是当前 `Yungu / Watchlist` app 的工作区。

它由原 `fof` 基线工程迁入，当前后端主语已经统一到 `instrument`，但产品可用范围明确收敛为 `fund-only watchlist`。

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
3. `Overview / Quote / Performance / Risk / Exposure / Ratings / People / Strategy / Documents / Research / Monitoring`
4. `Facts ingest / manual profile / recalc / read model`

其中：

- `Watchlists` 与单资产详情页是当前主界面
- Watchlist 里的资产新增只允许从 `Database Dashboard` 共享库搜索并引用，不再在 Watchlist 内创建资产主档
- 当前 watchlist 可用范围是 `fund`；shared registry 可以管理更广的资产类型，但它们不会进入 watchlist detail 主链路
- `Monitoring` 已经是可用工作面；`Research / Documents` 一级路由仍以轻量页为主
- Copilot 后端接口仍保留为后续扩展入口，但当前 UI 默认隐藏，不作为已发布能力

## 目录结构

```text
.
├── backend
│   ├── alembic
│   ├── watchlist_app
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

以下命令默认从仓库根目录执行；如果已经在 `apps/watchlist` 目录，可相应省略路径前缀。

### 1. 后端

```bash
cd apps/watchlist/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PYTHONPATH=. alembic upgrade head
uvicorn watchlist_app.main:app --reload --host 127.0.0.1 --port 8000
```

说明：

- 运行时数据库连接以 `FTV2_DATABASE_URL` 为准；如迁移需要单独连接，可设置 `FTV2_ALEMBIC_DATABASE_URL`
- schema 以 `FTV2_DATABASE_SCHEMA` 为准
- settings 默认值仍指向本地 PostgreSQL，但实际运行应以你当前 `.env` / shell 环境为准
- 数据库结构只通过 `alembic upgrade head` 管理；如果要清空旧数据，使用仓库根目录的 `./infra/postgres/rebuild_local_schemas.sh`
- FastAPI 根路径会重定向到前端 `watchlists`
- backend 顶层包名现在是 `watchlist_app`

### 2. 前端

```bash
cd apps/watchlist/frontend
npm install
npm run dev
```

说明：

- Vite 默认监听 `http://127.0.0.1:5173`
- `/api` 已代理到 `http://127.0.0.1:8000`

## 常用校验命令

```bash
npm --prefix apps/watchlist/frontend run build
(cd apps/watchlist/frontend && npx tsc --noEmit)
(cd apps/watchlist/backend && pytest)
```

## 当前行为边界

- Watchlist 主表当前按页加载，默认每页 `50` 行；页面上的 `Download` 会导出当前筛选/排序结果的全量行，而不是只导出当前页
- Watchlist filter 菜单会基于当前 watchlist 的全量行构建选项，不再只采样前几页；当前页执行 add / delete / move 后，filter 选项也会随之刷新
- Watchlist 的 `move` / `copy` 只允许操作 source watchlist 里已经存在的资产，不再把这两个接口当成隐式 `add`
- 自定义 view 会把展示名称映射成 path-safe 的 slug id；复制 watchlist 时也会清洗 legacy custom view id，避免把不可路由的旧 id 继续扩散
- Watchlist 和 Instrument Detail 已改成三层产品框架：`Fund Taxonomy / Research Tags / Monitoring Assessment`；详情页入口调整为 `Overview`，把基础信息和 `Fund Taxonomy` 放到第一屏
- `Peer Category` 仍然保留为外部同类比较口径；内部基金分类已经独立成 fund taxonomy tree，二者不再混用
- fund 分类不再依赖固定 `fund_category_l1/l2/l3`；当前已经落成 `fund taxonomy tree + derived taxonomy levels`，支持可变深度路径和按层级 group by
- fund taxonomy 不做自动推断或 migration 自动回填；默认未分类，由人在详情页 `Overview -> Fund Taxonomy` 明确选择
- Monitoring 的缺失项检查已经改成 taxonomy-aware；不同分类叶子只检查适用的 label，不再全 fund 共用一套静态 tag 清单
- 示例基金标签值不再在 migration 或 add-to-watchlist 运行时自动注入；产品框架赋值只来自显式录入和后续真实数据链路
- 后端主语已经统一到 `instrument`，当前只暴露 `/api/instruments/...` 明确接口；旧 `/api/funds/...` 兼容路由已移除
- Instrument Detail 里的 canonical NAV history 现在是只读视图；导入、编辑、刷新共享净值要去 `Database Dashboard`，这里只保留本地 basis / benchmark 设置

## 当前文档

- [../../docs/README.md](../../docs/README.md)
  仓库级文档入口，包含数据库工作流和平台边界说明。
- [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)
  当前前端视觉基线，约束白底数据终端、tabs 节奏和 Watchlist / Portfolio 的内容区一致性。
- [docs/INDEX.md](./docs/INDEX.md)
- [docs/CURRENT_SYSTEM_BASELINE.md](./docs/CURRENT_SYSTEM_BASELINE.md)
- [docs/FUND_PRODUCT_FRAMEWORK.md](./docs/FUND_PRODUCT_FRAMEWORK.md)
- [docs/FUND_TERMINAL_V2_DATA_MODEL_AND_API.md](./docs/FUND_TERMINAL_V2_DATA_MODEL_AND_API.md)
- [docs/FUND_TERMINAL_V2_AI_COPILOT.md](./docs/FUND_TERMINAL_V2_AI_COPILOT.md)

## 继续推进时的原则

- 以当前仓库实现为准，不再维护“是否重建”的历史讨论稿
- 文档优先描述已经存在的结构、接口和下一步真实缺口
- 新需求先落到当前系统基线，再决定是否扩展数据模型或页面骨架
