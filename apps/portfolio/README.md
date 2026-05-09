# Yungu Portfolio

这是 `Yungu / Portfolio` app 的工作区。

当前状态：

- `backend/` 与 `frontend/` 都已可运行
- 组合内核当前以 `portfolio / account / transaction` 为数据库主事实
- 持仓、lots、ledger、performance 由内核服务按需推导
- `Holdings / Accounts / Transactions / Performance` 已有真实 API 与页面支撑
  `Transactions` 当前支持 create / update / delete 原始事实；内部转仓仍按成对事实管理
- `Risk` 已有真实工作台，包含 rolling annualized volatility / Sharpe、相关性矩阵、Current Drift 和 point-in-time Risk Contribution；风险窗口、协方差方法和风险贡献模式与 Research 使用同一组口径选项
- `Taxonomies` 已有真实配置工作台，支持层级 sleeve tree、assignment、`TargetSet`、`default planning taxonomy` 与 `cash_bucket` 维护
- `Research` 已有真实工作台，支持基于 planning taxonomy / TargetSet / 当前持仓的递归 target-weight solve、run history、target weights、member targets、scope solver path、风险预算求解诊断和调仓缺口
- `Review` 已有真实 period review pack 页面
- `Overview` 已作为组合默认首页发布，主图按 `Portfolio Value / TWR Index` 两种组合管理口径展示，回撤固定基于 TWR；页面同时承载 sleeve 结构和 top holdings 总览，`Snapshot` 不再作为独立工作面保留

## 当前文档

- [../../docs/README.md](../../docs/README.md)
  仓库级文档入口，包含数据库工作流和平台边界说明。
- [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)
  当前前端视觉基线，约束白底数据终端、tabs 以下内容节奏和 Portfolio / Fund Detail 的一致性。
- [docs/01_PMS_REFERENCE_BASELINE.md](./docs/01_PMS_REFERENCE_BASELINE.md)
- [docs/02_PRODUCT_PRD.md](./docs/02_PRODUCT_PRD.md)
- [docs/03_DOMAIN_MODEL.md](./docs/03_DOMAIN_MODEL.md)
- [docs/04_CALCULATION_SPEC.md](./docs/04_CALCULATION_SPEC.md)
- [docs/05_INFORMATION_ARCHITECTURE.md](./docs/05_INFORMATION_ARCHITECTURE.md)
- [docs/06_GIPS_ALIGNMENT.md](./docs/06_GIPS_ALIGNMENT.md)

## 开发原则

- 只依赖共享 `asset-core` contract 与 `shared_asset` schema，不通过 `platform` API 取数
- 不复用 `Watchlist` 的业务模型
- 维持 `portfolio / account / transaction / performance / risk / research` 的独立边界
- `Taxonomy / TargetSet` 是组合 planning truth；`Research` 消费这套结构求解当前 target weights，不反向创造另一套目标体系
- 运行时生成的 research artifacts 属于本地工作产物，不作为源码的一部分

## 快速启动

以下命令默认从仓库根目录执行；如果已经在 `apps/portfolio` 目录，可相应省略路径前缀。

### 1. 后端

```bash
cd apps/portfolio/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PYTHONPATH=. alembic upgrade head
uvicorn portfolio_app.main:app --reload --host 127.0.0.1 --port 8001
```

说明：

- 数据库连接通过 `YUNGU_PORTFOLIO_DATABASE_URL` 配置；本机账号和密码只应放在未提交的 `.env` 或 shell 环境里
- `portfolio` 使用 `portfolio` schema
- 测试使用临时 SQLite，不会污染默认运行库
- research 运行产物默认落在 `backend/research_outputs/`，用于本地查看和回放，已按运行时目录管理；当前产物以 target weights、member targets、leaf targets、solve event、scope solve events 和 target weight gaps 为主
- backend 顶层包名现在是 `portfolio_app`
- daily snapshots 已物化到数据库，并显式保存每日 `beginning_nav` / `ending_nav`；`Performance`、`Holdings`、instrument/account contribution 读路径默认复用物化结果。交易、账户或行情变更会用 `refresh_request_id` 把相关组合标记为 stale 并触发刷新。若刷新中又收到新数据，当前计算不会清掉新的 stale 标记，而是串行再跑一轮后才置为 current。

### 2. 前端

```bash
cd apps/portfolio/frontend
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
- 账户成本法支持 `FIFO` 与 `moving_average`；修改成本法会按交易事实重算 holdings、lots、ledger postings 和 snapshots

## 常用校验命令

```bash
(cd apps/portfolio/backend && pytest)
(cd apps/portfolio/backend && pytest tests/test_taxonomies_api.py tests/test_research_api.py -q)
npm --prefix apps/portfolio/frontend run build
```

## 计算层阶段性状态

截至 `2026-05-08`：

- 组合级 TWR 使用日频 true time-weighted 口径：外部流入进分母，外部流出加回分子，区间结果几何复合。
- FIFO / moving average 只影响 book cost、book realized gain、book unrealized P&L 和 lot 展示；不影响 fair-value based TWR。
- Performance `Calculation` 使用 `Initial Value + Net External Flow + Period P&L = Final Value` 的期间桥接。资本利得拆分使用期初市值重置后的期间成本，而不是账户 book cost。Calculation 默认视图命名为 `Default`，展示 realized risk attribution；用户可像 Holdings 一样保存自定义表格视图。Group By 默认是 `None`，内部映射到底层 instrument lines；也支持 asset type / currency / account / taxonomy，TWR 与 contribution 在后端按对应轴计算，表格可导出 CSV。
- `moving_average` 在底层按 `account + asset` 维护一个 rolling average cost bucket；API 为 UI 和转仓审计输出一个 synthetic position lot。
- `Overview`、`Performance`、`Review` 的 TWR index、daily series 和 drawdown 均按查询窗口重新复合；不得复用 inception-to-date 的累计 TWR 作为区间曲线。
- `Risk` 的 rolling volatility / Sharpe 输入来自 `daily_twr` simple return 序列，并排除仅由 stale price carry-forward 得到的非市场观察日。相关性矩阵和风险贡献使用 as-of date + lookback covariance 的单点风险口径；混合频率和稀疏序列先解析 daily / weekly / monthly calculation basis，再按目标 period 的最后有效观测对齐，不跨期前向填充，用共同有效日期和实际观察密度年化 covariance。区间风险贡献归入 Performance `Calculation` 的 realized risk attribution columns。
- `Research` 的当前 target solve 从最末端 sleeve 递归向上求解；scope default 只使用该 scope 自身的默认目标维度，不静默切到另一个维度。顶层 capital overlay 在风险 sleeve 权重求出后再按目标波动率或总敞口缩放，并把剩余权重放到现金；若共同有效收益不足两期，进入 insufficient-history 诊断，不用稀疏异步价格硬算风险。
- `IRR / MWROR` 是资金效率补充指标；若数学上不可解，不应降低 TWR 口径的 coverage。
- 绩效方法参考 Portfolio Performance 的账本模型，并吸收 GIPS 的 TWR 优先、外部现金流政策、估值频率和方法一致性原则；本项目不声称 GIPS compliance，详见 [docs/06_GIPS_ALIGNMENT.md](./docs/06_GIPS_ALIGNMENT.md)。

## 加载速度排查记录

2026-05-02 对 Portfolio 页面加载链路做了一次只读排查。当前本地 portfolio 数据量不大：`portfolio` 2 条、`transaction` 39 条、`taxonomy_node` 28 条、`target_set_line` 168 条；但共享资产库已有 `instrument_market_data` 约 27,439 条。因此加载慢主要不是 portfolio 私有表过大，而是页面首屏并行触发多条重计算链路，每条链路又独立重放交易、重建 position lots、读取 shared asset 行情。

2026-05-03 已完成 daily snapshot / holding snapshot / contribution slice 的物化读模型，核心 performance 和 holdings 读路径不再每次从零生成全窗口 daily snapshots。下面记录保留为历史排查背景；后续性能工作重点转为增量刷新、shared asset detail 缓存和 Review/Risk 多接口结果复用。

主要慢点：

- `get_portfolio()` 不是轻量存在性查询。它会实时汇总 NAV 和证券数，内部调用 account workspace / pricing map；很多接口只是校验 portfolio 是否存在，也会触发这段重算。实测单次约 `0.5s - 1.8s`。
- `build_daily_portfolio_snapshots()` 按日期窗口逐日重放交易，并在每天重新派生 ledger postings、position lots、估值和 FX。`/performance` 依赖它，portfolio `3` 实测 7 天约 `3.8s`，30 天约 `15s`。
- `build_period_calculation_report()` 和 `build_period_boundary_holdings_report()` 会为起止边界再次构造 snapshot，其中 `_build_single_date_snapshot()` 为了一个日期也会从历史开始生成 daily snapshots。portfolio `3` 实测 7 天分别约 `25s` 和 `26s`。
- `build_contribution_report()` 先生成 daily snapshots，又在日期循环中反复重建 group end states / position lots。portfolio `3` 实测 7 天约 `11s`。
- shared asset 行情读取被重复放大。profile 显示慢链路的大头在 `list_registry_instruments()` / instrument detail 序列化和 market data 扫描上，而不是 portfolio 表查询本身。

页面层的放大点：

- `Overview` 首屏会并行请求 workspace summary、holdings、taxonomy catalog，并另起 performance 请求。
- `Risk` 会并行请求 holdings、accounts、taxonomy，再请求 performance 和 contribution。
- `Review` 一次加载 performance、calculation、contribution、boundary holdings、taxonomy、research workbench，其中多个接口都会触发上述重计算。

后续优化优先级应先放在：把 portfolio 存在性查询与实时 rollup 分离；缓存或物化 daily snapshots / position lots；避免同一页面内重复拉 shared asset detail；让 Review / Risk 这类页面复用同一批 performance 中间结果。
