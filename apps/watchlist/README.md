# Portfolio Operations Workbench Watchlist

这是当前 `Portfolio Operations Workbench / Watchlist` app 的工作区。

当前后端主语已经统一到 `instrument`，当前已发布主路径支持 `public_fund / private_fund / etf / equity / index` 五类本地 watchlist/detail 工作面。

当前 app 已经包含：

- `backend/`
  FastAPI + SQLAlchemy + Alembic 的后端实现，运行时目标是显式配置的 canonical PostgreSQL
- `frontend/`
  React + Vite + TypeScript 的终端前端
- `docs/`
  当前工程基线、数据模型和产品框架文档

## 当前范围

当前主路径已经落到下面这条链路上：

1. `Watchlists`
2. `Instrument Detail`
3. `Overview / Quote / Performance / Risk / Price / Exposure / People / Strategy / Documents / Research / Monitoring`
4. `Facts ingest / manual profile / recalc / read model`

其中：

- `Watchlists` 与单资产详情页是当前主界面
- 系统名单固定为 `Index / All 公募 / All 私募` 并同步全部 active 对应资产；股票、ETF 和其他自定义名单只按人工添加维护
- Watchlist 里的公募、私募、ETF 和指数只从共享 Registry 搜索并引用；股票从 Platform 维护的本地 FMP 目录搜索，首次选用时才建立共享身份并加载 EOD
- 当前 watchlist 可用范围是 `public_fund / private_fund / etf / equity / index`；shared registry 可以管理更广的资产类型，但其他类型不会进入 watchlist detail 主链路
- `public_fund / private_fund` 分别进入各自的详情入口，并共享稳定的基金页基础结构；`etf / equity / index` 使用轻量详情工作面，聚焦 `Overview / Performance / Risk / Price`，不强行复用基金特有的 exposure、people、strategy、documents 或 research 录入面
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
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file watchlist "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_WATCHLIST_
: "${PORTFOLIO_OPS_WATCHLIST_DATABASE_URL:?external watchlist.env must set the canonical database URL}"
cd apps/watchlist/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn watchlist_app.main:app --reload --host 127.0.0.1 --port 8000
```

说明：

- 运行时必须由仓库外的 `watchlist.env` 显式设置 `PORTFOLIO_OPS_WATCHLIST_DATABASE_URL`，并与 Platform、Portfolio 指向同一个 canonical PostgreSQL；如迁移需要单独变量，`PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL` 必须保持同一目标
- schema 固定为 canonical `watchlist`；非空的 schema 环境变量只能取该值
- backend 目录只保留 `.env.example` 键名模板，不创建 `.env` 文件或软链接；真实 secrets 只位于仓库外
- 数据库结构只通过仓库根目录的统一迁移链管理。现有本机真实数据库使用 `infra/launchd/install_local_services.sh`，由其停写、备份、迁移和恢复；不要单独运行 Watchlist Alembic。若明确要清空旧数据，使用仓库根目录的 `./infra/postgres/rebuild_local_schemas.sh`
- FastAPI 根路径会重定向到前端 `watchlists`
- backend 顶层包名现在是 `watchlist_app`

### 2. 前端

```bash
cd apps/watchlist/frontend
npm install
npm run dev
```

说明：

- Vite 默认仅监听 `127.0.0.1:5173`；如需远程访问，请通过受控的反向代理显式开放
- `/api` 已代理到 `http://127.0.0.1:8000`

## 常用校验命令

```bash
npm --prefix apps/watchlist/frontend run build
(cd apps/watchlist/frontend && npx tsc --noEmit)
(cd apps/watchlist/backend && pytest)
```

## 当前行为边界

- Watchlist 主表从后端取得同一筛选/排序下的完整快照，前端初始渲染 `80` 行并按需继续显示；全选只作用于当前已渲染行。`Download` 导出当前筛选/排序结果的全量行，并保留逐行 `metric_as_of_date`
- Watchlist 顶部 view / Data & Columns / Group By / Filter / Download / row action controls 保持本 app 自己的实现和 class，但视觉 contract 与 Portfolio toolbar controls 一致，不引入跨 app 组件依赖
- Data & Columns 中 `Name` 是默认锁定列，不作为可选字段重复展示；应用 view columns 时必须去重并保留 `instrument_name` 为第一列
- Group By 后只允许对明确可写分组拖动 instrument：Watchlist 内部 instrument taxonomy 和离散 custom attribute。拖放需要调用对应后端写接口同步，不对 read-model / score / bucket 等只读分组做错误兼容
- Watchlist filter 菜单会基于当前 watchlist 的全量行构建选项；执行 add / delete / move 后，filter 选项也会随之刷新
- Watchlist 的 `move` / `copy` 只允许操作 source watchlist 里已经存在的资产，不再把这两个接口当成隐式 `add`
- Watchlist 的收益和风险不使用一个名单级日期统一截断：每行以该 instrument 自己的最新 calculation-series observation 为 as-of。Screener 始终返回行级 `metric_as_of_date`，页脚展示最早至最晚终点；不同终点的收益/风险分组平均会失败关闭。
- 自定义 view 会把展示名称映射成 path-safe 的 slug id；复制 watchlist 时会按相同规则规范化源 view id 并解决命名冲突
- Watchlist 和 Instrument Detail 使用三层产品框架：`Instrument Taxonomy / Research Tags / Monitoring Assessment`；公募、私募、ETF、股票、指数各自使用独立分类树，taxonomy 只存放在 Watchlist schema
- `Peer Category` 仍然保留为外部同类比较口径；公募与私募各自的内部分类已经独立成 Watchlist-local taxonomy tree，二者不再混用
- 公募和私募分类不再依赖固定 `fund_category_l1/l2/l3`；当前使用各类型独立的 taxonomy tree 和派生层级，支持可变深度路径和按层级 group by
- 公募、私募、ETF、指数的产品 taxonomy 由人在详情页明确选择；股票只按 Registry 的 canonical exchange 自动映射到 Watchlist 内的市场/交易所 taxonomy
- Monitoring 的缺失项检查已经改成 taxonomy-aware；不同资产类型和分类叶子只检查适用的 label，不再由所有基金共用一套静态 tag 清单
- 示例基金标签值不再在 migration 或 add-to-watchlist 运行时自动注入；产品框架赋值只来自显式录入和后续真实数据链路
- 后端主语已经统一到 `instrument`，当前只暴露 `/api/instruments/...` 明确接口；旧 `/api/funds/...` 兼容路由已移除
- Instrument Detail 里的 canonical quote/NAV history 现在是只读视图；导入、编辑、刷新共享行情/净值要去 `Database Dashboard`，这里只保留本地 basis / benchmark 设置。派生层必须保留真实 `metric_family / quote_basis / role`，不能把 `close`、`official_nav`、`total_return_nav` 混成一个无来源的 NAV 字段。
- canonical recalc 只消费 Registry 中 `status=complete`、currency 与 instrument master currency 一致的 quote/NAV，并严格使用 Registry `quote_selection_policy` 的顺序。Registry 无有效序列或缺 policy 时明确产出 unavailable；旧 Watchlist `nav_fact` 仅可审计读取，不再参与行情、收益、风险或图表计算。
- Instrument Detail 的 benchmark 选择在 Quote / Performance / Risk 三个工作面共用同一状态；Performance matrix 和 Risk rolling charts 使用同一 benchmark calculation series，不再维护第二套 metric benchmark。Quote / Performance 图表和相对指标只使用双方日期完全相同的共同观测收盘点，起点、终点及每个中间 return period 都保持同一 identity；不再用“各自不晚于目标日的不同收盘”相减。横轴按真实日期比例投影，不按样本序号拉伸。比较矩阵共用最晚共同观测终点，SI 共用最早共同观测起点。Rolling risk chart 支持 1M / 3M / 6M / 12M / 24M / 36M 窗口；benchmark 曲线只在存在完整共同 calculation period 时展示，不补齐缺失序列。
- `return_ytd / return_mtd / return_1w / return_1m / return_3m / return_6m / return_1y / annualized_return / return_3y / return_5y / max_drawdown / current_drawdown / volatility / sharpe_ratio` 当前对适用的公募 / 私募 / ETF / 股票 / 指数可见，并完整投影到 performance snapshot 与 watchlist row read model。公募和私募只使用 `total_return_nav`；ETF、股票和指数按 `quote_selection_policy` 选择 calculation series，read model 同时记录实际 quote basis 与 `total_return / price_return / unknown` 语义，不能把普通 `close` 自动写成 total return。
- 年化收益在首个日历周年前为空；daily 风险路径按 Registry expected frequency 和 market calendar 检查真实 session。休市不算缺点，实际缺少预期观测时只保留可验证的端点收益，路径风险指标不做插值或前端回退。

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

- 以当前仓库实现为准，不保留旧方案讨论稿
- 文档优先描述已经存在的结构、接口和下一步真实缺口
- 新需求先落到当前系统基线，再决定是否扩展数据模型或页面骨架
