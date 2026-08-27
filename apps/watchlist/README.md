# Portfolio Operations Workbench Watchlist

Watchlist 承载观察池、单资产研究、监控和本地物化 read model。当前主路径支持 `public_fund / private_fund / etf / equity / index`；其他 Registry 类型不会被塞入通用详情页。

## 职责与边界

- `Watchlists`：名单、view、筛选、排序、分组和导出；
- `Instrument Detail`：按资产类型进入基金、ETF、股票或指数工作面；
- `Investment Research`：taxonomy、research profile、逐条 note 和版本历史；
- `Monitoring`：来源新鲜度、字段缺失、重算状态和复核到期；
- `Recalculation`：从 Registry canonical facts 构建 Watchlist-local snapshots 和 rows。

Watchlist 只读取 `instrument_registry`，不修改 canonical identity、quote/NAV/FX 或 corporate actions。行情导入、修订和刷新属于 Platform Database Dashboard。Watchlist taxonomy、研究事实、名单和 read model 只写入 `watchlist` schema，不进入 Registry 或 Portfolio。

关键运行约束：

- 系统名单固定为 `Index / All 公募 / All 私募`；股票、ETF 和自定义名单按显式加入维护；
- 每个 instrument 使用自己的 calculation-series as-of，名单不伪造共同计算日；
- 混合资产名单只暴露对全部当前类型都有定义的字段；
- Group By 只组织视图，不隐式修改 taxonomy 或研究事实；
- canonical recalc 只消费 Registry 中符合 status、currency 和 quote policy 的观测；条件不足时结果为 unavailable；
- Registry 通知用于降低延迟，不是正确性边界；durable worker 会主动对账 source generation；
- 收益、风险和 benchmark 只在合同规定的端点、共同样本及 return semantics 下发布，不做静默回退。

## 文档

| 主题 | 权威文档 |
| --- | --- |
| 资产详情页面、来源和跨应用边界 | [Asset Detail Architecture](./docs/ASSET_DETAIL_ARCHITECTURE.md) |
| Watchlist schema、read model、API 和 recalc | [Data Model and API](./docs/DATA_MODEL_AND_API.md) |
| 公募/私募产品结构、taxonomy 和详情字段 | [Fund Product Framework](./docs/FUND_PRODUCT_FRAMEWORK.md) |
| 定性研究维度、受控词表和评分规则 | [Fund Qualitative Research Framework](./docs/FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md) |
| 收益序列、窗口、共同样本和缺点规则 | [Return Series Contract](./docs/RETURN_SERIES_CONTRACT.md) |
| 仓库架构、数据库和部署 | [Repository Documentation](../../docs/README.md) |
| 前端视觉与交互 | [Frontend Design Baseline](../../docs/FRONTEND_DESIGN_BASELINE.md) |

README 只描述范围和开发入口；字段、公式和 API 细节只在上表对应文档维护。

## 代码定位

```text
apps/watchlist/
  backend/watchlist_app/api/          HTTP routes 与 contracts
  backend/watchlist_app/repositories/ Watchlist 私有持久化
  backend/watchlist_app/services/     recalc、materialization 与业务服务
  backend/alembic/                    watchlist schema migrations
  frontend/src/                       Watchlists、详情和 Monitoring
```

## 本地开发

先按根目录 README 安装依赖，并在仓库外准备 `watchlist.env`。从仓库根目录启动后端：

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file watchlist "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_WATCHLIST_
: "${PORTFOLIO_OPS_WATCHLIST_DATABASE_URL:?watchlist.env must set the canonical database URL}"
PYTHONPATH="$PROJECT_ROOT/apps/watchlist/backend:$PROJECT_ROOT/packages/instrument-core/python" \
  "$PROJECT_ROOT/.venv/bin/python" -m uvicorn watchlist_app.main:app \
  --host 127.0.0.1 --port 8000 --reload
```

启动前端：

```bash
npm --prefix apps/watchlist/frontend run dev -- --host 127.0.0.1 --port 5173
```

前端默认把 `/api` 代理到 `http://127.0.0.1:8000`。真实数据库迁移必须使用根目录统一入口；不要单独升级 Watchlist Alembic chain。

## 验证

```bash
infra/scripts/verify_repository.sh backend watchlist
infra/scripts/verify_repository.sh frontend watchlist
```

修改收益窗口、字段 registry、materialization 或文档合同后，应同时运行 `backend/tests/test_documentation_contract.py`；涉及 cross-schema PostgreSQL 行为时按 [Database Workflow](../../docs/DATABASE_WORKFLOW.md) 运行专项门禁。
