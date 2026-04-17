# Yungu Portfolio

这是 `Yungu / Portfolio` app 的工作区。

当前状态：

- `backend/` 与 `frontend/` 都已可运行
- 组合内核当前以 `portfolio / account / transaction` 为数据库主事实
- 持仓、lots、ledger、performance 由内核服务按需推导
- `Snapshot / Holdings / Accounts / Transactions / Performance` 已有真实 API 与页面支撑
- `Risk` 已有基于 holdings / accounts / performance 的 current-state 工作台
- `Taxonomies` 已有真实配置工作台，支持层级 sleeve tree、assignment、`TargetSet`、`default planning taxonomy` 与 `cash_bucket` 维护
- `Research` 已有真实工作台，支持 taxonomy-backed recursive sleeve backtest、run history、artifact viewer、回测指标/曲线/权重变化/调仓建议
- `Review` 与 `Snapshot` 仍保留下一阶段要补完的工作面，不作为本阶段的完成项

## 当前文档

- [docs/01_PMS_REFERENCE_BASELINE.md](./docs/01_PMS_REFERENCE_BASELINE.md)
- [docs/02_PRODUCT_PRD.md](./docs/02_PRODUCT_PRD.md)
- [docs/03_DOMAIN_MODEL.md](./docs/03_DOMAIN_MODEL.md)
- [docs/04_CALCULATION_SPEC.md](./docs/04_CALCULATION_SPEC.md)
- [docs/05_INFORMATION_ARCHITECTURE.md](./docs/05_INFORMATION_ARCHITECTURE.md)

## 开发原则

- 只依赖平台共享的最小 `asset-core`
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
uvicorn --app-dir . app.main:app --reload --host 127.0.0.1 --port 8001
```

说明：

- 默认数据库连接为 PostgreSQL：`postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu`
- `portfolio` 使用 `portfolio` schema
- 测试使用临时 SQLite，不会污染默认运行库
- research 运行产物默认落在 `backend/research_outputs/`，用于本地查看和回放，已按运行时目录管理

### 2. 前端

```bash
cd /home/shaw/yungu/apps/portfolio/frontend
npm install
npm run dev
```

说明：

- Vite 默认监听 `http://127.0.0.1:5174`
- `/api` 已代理到 `http://127.0.0.1:8001`

## 常用校验命令

```bash
cd /home/shaw/yungu/apps/portfolio/backend && pytest
cd /home/shaw/yungu/apps/portfolio/backend && pytest tests/test_taxonomies_api.py tests/test_research_api.py -q
cd /home/shaw/yungu/apps/portfolio/frontend && npm run build
```
