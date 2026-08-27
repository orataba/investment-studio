# Portfolio Operations Workbench Portfolio

Portfolio 承载组合、账户、交易、账本、持仓、绩效、风险、taxonomy 和研究。源交易与账户是事实；ledger postings、lots、daily snapshots、contribution slices 和页面 payload 是可重建派生结果。

## 职责与边界

当前交易主路径支持：

- Registry 中的股票、ETF、公募和私募基金；
- Portfolio-local FCN 与 Option 合约及其已确认生命周期事件；
- Cash、费用、税、利息、换汇和账户内转移；
- Preview/Commit、CSV/Excel 和截图助手共用的交易 command contract。

Portfolio 不写 Registry market facts，也不复用 Watchlist 的名单、taxonomy 或研究模型。普通证券卖空、直接债券、融资、PE/VC capital call、基金份额转换、衍生品 transfer，以及 FCN/Option 的 daily fair value、Greeks 和自动 barrier/行权不在当前支持范围。

关键运行约束：

- FCN 和 Option 是 Portfolio-local immutable contracts；只有 underlying/deliverable 证券引用 Registry；
- 交易的 trade、position-effective、entitlement、settlement 和 snapshot 时钟不能互相替代；
- 所有写路径先保存 canonical facts，再标记最早受影响日期并重建派生读模型；
- daily snapshot 重算的外部入口只写 durable calculation state 并返回 `202`；单线程 worker 合并 generation、保留最早 `dirty_from`，发布前复核 source generation；
- 组合 summary、Overview 和默认 Holdings 共享同一 fresh-complete snapshot 选择规则；
- Taxonomy/TargetSet 是 planning truth，Research 消费它们，不建立第二套目标体系；
- 条件不足的收益、风险和研究结果明确 unavailable，不用旧算法、等权或不完整样本兜底。

## 文档

| 主题 | 权威文档 |
| --- | --- |
| NAV、TWR、IRR、风险、归因、drift、scenario 和 Research solve | [Calculation Spec](./docs/01_CALCULATION_SPEC.md) |
| GIPS-informed 方法及声明边界 | [GIPS Alignment](./docs/02_GIPS_ALIGNMENT.md) |
| Holdings 每个字段和分组聚合 | [Holdings Field Reference](./docs/03_HOLDINGS_FIELD_REFERENCE.md) |
| 交易创建、修改、删除、CSV/Excel 与核对 | [Transaction Operations](./docs/04_TRANSACTION_OPERATIONS.md) |
| Portfolio 文档维护入口 | [Portfolio Documentation](./docs/README.md) |
| 外部 JSON Preview/Commit 对接 | [Transaction Import API Guide](../../docs/TRANSACTION_IMPORT_API_GUIDE.md) |
| 截图助手 Harness 和权限边界 | [Portfolio Copilot Harness](../../docs/PORTFOLIO_COPILOT_HARNESS.md) |
| 数据表与字段 | [Portfolio Database Dictionary](../../docs/PORTFOLIO_DATABASE_DICTIONARY.md) |
| 仓库架构、数据库和部署 | [Repository Documentation](../../docs/README.md) |

README 不维护公式或字段摘要；发生冲突时以上 canonical 合同和当前实现必须在同一改动中校正。

## 代码定位

```text
apps/portfolio/
  backend/portfolio_app/api/       HTTP routes 与 contracts
  backend/portfolio_app/services/  交易、账本、计算、风险和研究
  backend/portfolio_app/db/        portfolio schema models/session
  backend/alembic/                 portfolio migrations
  backend/scripts/                 受控导入、截图 Harness 和运维入口
  frontend/src/                    Portfolio 工作台
```

## 本地开发

先按根目录 README 安装依赖，并在仓库外准备 `portfolio.env`。从仓库根目录启动后端：

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file portfolio "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_PORTFOLIO_
: "${PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL:?portfolio.env must set the canonical database URL}"
PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/packages/instrument-core/python" \
  "$PROJECT_ROOT/.venv/bin/python" -m uvicorn portfolio_app.main:app \
  --host 127.0.0.1 --port 8001 --reload
```

启动前端：

```bash
npm --prefix apps/portfolio/frontend run dev -- --host 127.0.0.1 --port 5174
```

前端默认把 `/api` 代理到 `http://127.0.0.1:8001`。真实数据库迁移必须使用根目录统一入口；不要单独升级 Portfolio Alembic chain。重建空 schema 后不会自动生成 demo portfolio，需通过 UI/API 显式创建或使用受控导入脚本。

## 验证

```bash
infra/scripts/verify_repository.sh backend portfolio
infra/scripts/verify_repository.sh frontend portfolio
```

修改 Holdings 字段时运行文档合同测试；修改 migration、cross-schema FK、search path、事务或并发行为时按 [Database Workflow](../../docs/DATABASE_WORKFLOW.md) 运行 PostgreSQL 专项门禁。
