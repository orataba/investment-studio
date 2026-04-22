# Yungu Portfolio

这是 `Yungu / Portfolio` app 的工作区。

当前状态：

- `backend/` 与 `frontend/` 都已可运行
- 组合内核当前以 `portfolio / account / transaction` 为数据库主事实
- 持仓、lots、ledger、performance 由内核服务按需推导
- `Holdings / Accounts / Transactions / Performance` 已有真实 API 与页面支撑
  `Transactions` 当前支持 create / update / delete 原始事实；内部转仓仍按成对事实管理
- `Risk` 已有真实工作台，分为 `Current` 与 `Realized` 两部分，分别回答当前风险结构与过去区间的风险路径
- `Taxonomies` 已有真实配置工作台，支持层级 sleeve tree、assignment、`TargetSet`、`default planning taxonomy` 与 `cash_bucket` 维护
- `Research` 已有真实工作台，支持 taxonomy-backed recursive sleeve backtest、run history、artifact viewer、回测指标/曲线/权重变化/调仓建议
- `Review` 已有真实 period review pack 页面
- `Snapshot` 仍保留为下一阶段报告工作面，当前路由会回退到 `Holdings`

## 当前文档

- [../../docs/README.md](../../docs/README.md)
  仓库级文档入口，包含数据库工作流和平台边界说明。
- [docs/01_PMS_REFERENCE_BASELINE.md](./docs/01_PMS_REFERENCE_BASELINE.md)
- [docs/02_PRODUCT_PRD.md](./docs/02_PRODUCT_PRD.md)
- [docs/03_DOMAIN_MODEL.md](./docs/03_DOMAIN_MODEL.md)
- [docs/04_CALCULATION_SPEC.md](./docs/04_CALCULATION_SPEC.md)
- [docs/05_INFORMATION_ARCHITECTURE.md](./docs/05_INFORMATION_ARCHITECTURE.md)

## 开发原则

- 只依赖共享 `asset-core` contract 与 `shared_asset` schema，不通过 `platform` API 取数
- 不复用 `Watchlist` 的业务模型
- 维持 `portfolio / account / transaction / performance / risk / research` 的独立边界
- `Taxonomy / TargetSet` 是组合 planning truth；`Research` 消费这套结构做 sleeve-level backtest，不反向创造另一套目标体系
- 运行时生成的 research artifacts 属于本地工作产物，不作为源码的一部分

## 快速启动

### 1. 后端

```bash
cd /home/shaw/yungu/apps/portfolio/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PYTHONPATH=. alembic upgrade head
uvicorn portfolio_app.main:app --reload --host 127.0.0.1 --port 8001
```

说明：

- 默认数据库连接为 PostgreSQL：`postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu`
- `portfolio` 使用 `portfolio` schema
- 测试使用临时 SQLite，不会污染默认运行库
- research 运行产物默认落在 `backend/research_outputs/`，用于本地查看和回放，已按运行时目录管理
- backend 顶层包名现在是 `portfolio_app`

### 2. 前端

```bash
cd /home/shaw/yungu/apps/portfolio/frontend
npm install
npm run dev
```

说明：

- Vite 默认监听 `http://127.0.0.1:5174`
- `/api` 已代理到 `http://127.0.0.1:8001`

## 重建本地数据库后

如果你刚执行过仓库根目录的 `./infra/postgres/rebuild_local_schemas.sh`：

- `portfolio` schema 只会保留 migration 定义的结构，不再自动注入默认 demo portfolio
- 历史导入的组合数据不会保留
- 需要组合时，显式通过前端 `/portfolios` 页面、`POST /api/portfolios`，或 [backend/scripts/import_real_portfolio_from_csv.py](./backend/scripts/import_real_portfolio_from_csv.py) 创建
  导入脚本现在要求显式传入 `--csv-path` 与 `--portfolio-id`；`--portfolio-name` 不传时会回退到 `portfolio_id`
- 新建 `securities_account` 时，如果不显式选择成本法，系统默认使用 `FIFO`

## 常用校验命令

```bash
cd /home/shaw/yungu/apps/portfolio/backend && pytest
cd /home/shaw/yungu/apps/portfolio/backend && pytest tests/test_taxonomies_api.py tests/test_research_api.py -q
cd /home/shaw/yungu/apps/portfolio/frontend && npm run build
```
