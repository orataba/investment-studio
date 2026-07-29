# Portfolio Operations Workbench Portfolio

这是 `Portfolio Operations Workbench / Portfolio` app 的工作区。

当前状态：

- `backend/` 与 `frontend/` 都已可运行
- 组合内核当前以 `portfolio / account / transaction` 为数据库主事实
- 持仓、lots、ledger、performance 由内核服务按需推导
- `Holdings / Accounts / Transactions / Performance` 已有真实 API 与页面支撑。
  `Transactions` 支持带幂等保护的 create、强制 row-version 的 update / delete、成对内部转仓、
  交易变更历史，以及 trade / position-effective / settlement / entitlement 日期的独立录入。
- `Risk` 已有真实工作台，包含 rolling annualized volatility / Sharpe、sample 相关性矩阵和 Current Drift；Production Risk Model 的风险窗口固定为 `1M / 3M / 6M / 12M / 24M`，协方差方法和风险贡献模式与 Research 使用同一组严格口径，相关性矩阵只暴露窗口和 scope，不混入协方差模型选择
- `Taxonomies` 已有真实配置工作台，支持层级 sleeve tree、assignment、`TargetSet`、`default planning taxonomy` 与 `cash_bucket` 维护。现金可以参与 capital / weight target，但不得建立 `target_risk_share`，也不进入 `risk_budget` target completeness。
- `Research` 已有真实工作台，支持基于 planning taxonomy / TargetSet / 当前持仓的递归 target-weight solve、top sleeve bounds、vol target/cap、1W/1M/3M rebalance backtest、benchmark 动态比较、风险预算诊断、回撤指标和调仓缺口。资产状态区分 Held / Observed / Former；former instrument 的正目标在未经 PM approval 时标记为人工复核。新 run 只有成功完成后才替换上一轮成功结果；失败 run 会保留上一轮 completed run 供继续查看。
- `Overview` 已作为组合默认首页发布，主图按 `Portfolio Value / TWR Index` 两种组合管理口径展示，回撤固定基于 TWR；页面同时承载 sleeve 结构和 top holdings 总览，`Snapshot` 不再作为独立工作面保留

## 当前文档

- [../../docs/README.md](../../docs/README.md)
  仓库级文档入口，包含数据库工作流和平台边界说明。
- [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)
  当前前端视觉基线，约束白底数据终端、tabs 以下内容节奏和 Portfolio / Fund Detail 的一致性。
- [docs/01_CALCULATION_SPEC.md](./docs/01_CALCULATION_SPEC.md)
  Portfolio 当前 canonical 计算口径。
- [docs/02_GIPS_ALIGNMENT.md](./docs/02_GIPS_ALIGNMENT.md)
  GIPS-informed 绩效方法治理边界。
- [docs/03_HOLDINGS_FIELD_REFERENCE.md](./docs/03_HOLDINGS_FIELD_REFERENCE.md)
  Holdings 全字段的行级公式、分组类型、缺失条件与跨 Watchlist 数值一致性合同。
- [docs/04_TRANSACTION_OPERATIONS.md](./docs/04_TRANSACTION_OPERATIONS.md)
  Transactions 工作台、录入事实、日期、基金金额份额、幂等与修改审计的操作合同。
- [docs/archive/2026-07-15_OPTIMIZATION_HANDOFF_COMPLETED.md](./docs/archive/2026-07-15_OPTIMIZATION_HANDOFF_COMPLETED.md)
  2026-07-15 优化轮次的历史执行记录；仅用于追溯，不定义当前产品合同。

## 开发原则

- 只依赖共享 `instrument-core` contract 与 `instrument_registry` schema，不通过 `platform` API 取数
- 不复用 `Watchlist` 的业务模型
- 维持 `portfolio / account / transaction / performance / risk / research` 的独立边界
- `Taxonomy / TargetSet` 是组合 planning truth；`Research` 消费这套结构求解当前 target weights，不反向创造另一套目标体系
- 运行时生成的 research artifacts 属于本地工作产物，不作为源码的一部分

## 快速启动

以下命令默认从仓库根目录执行；如果已经在 `apps/portfolio` 目录，可相应省略路径前缀。

### 1. 后端

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
portfolio_ops_load_env_file \
  "$(portfolio_ops_runtime_env_file portfolio "$RUNTIME_ENV_ROOT")" \
  PORTFOLIO_OPS_PORTFOLIO_
: "${PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL:?external portfolio.env must set the canonical database URL}"
cd apps/portfolio/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PYTHONPATH=. alembic upgrade head
uvicorn portfolio_app.main:app --reload --host 127.0.0.1 --port 8001
```

说明：

- `PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL` 必须由仓库外的 `portfolio.env` 显式提供，并与 Platform、Watchlist 指向同一个 canonical PostgreSQL；backend 目录只保留 `.env.example` 键名模板，不创建 `.env` 文件或软链接
- `portfolio` 使用 `portfolio` schema
- 测试使用临时 SQLite，不会污染默认运行库
- research 运行产物默认落在 `backend/research_outputs/`，用于本地查看和回放，已按运行时目录管理；当前产物以 request、context、target rows、member/leaf targets、solved result groups、solve event、scope solve events、target weight gaps 和 backtest payload 为主
- backend 顶层包名现在是 `portfolio_app`
- 交易、账户和研究快照使用 canonical `instrument_id` / `instrument_name` / `instrument_type`；迁移会规范历史 JSON，运行时不再接受 `asset_id` / `asset_name` / `asset_type` 或 `allowed_asset_types`
- daily snapshots 已物化到数据库，并显式保存每日 `beginning_nav` / `ending_nav`；`Performance`、`Holdings`、instrument/account contribution 读路径默认复用物化结果。交易、账户或行情变更会用 `refresh_request_id` 把相关组合标记为 stale 并触发刷新。若刷新中又收到新数据，当前计算不会清掉新的 stale 标记，而是串行再跑一轮后才置为 current。组合 summary、Overview 和未显式指定日期的 Holdings 默认使用最新的 fresh complete snapshot：`coverage_state = complete`、`nav` 存在且 `stale_price_flag = false`，避免部分持仓价格/NAV 尚未更新时把组合 as-of 推到更晚日期；FX staleness 保留为质量标记，不单独决定组合资产新鲜度，也不作为资产新鲜度兜底。查询窗口早于组合首个物化日期时，只允许在组合成立前的自然空窗上裁剪；若物化快照缺少交易后历史或 as-of 之前尾部日期，读路径不得返回空结果掩盖缺口。
- `Holdings` 的物化行包含 instrument market profile 与 `risk_basis` 对齐元数据；读路径命中物化 profile 时不再逐行重建行情趋势和风险字段。`risk_basis` 只描述当前非现金持仓的 daily / weekly / monthly return alignment，不是风险指标；频率优先使用 Registry expected frequency，daily 数据按各自 market calendar 校验预期 session。若 active risk holding 缺少可解析来源频率或存在预期观测缺口，即使所有成员共同缺同一天，Risk 页面和 Forward RC 也必须标记 basis incomplete。Holdings 还会按 settled cash ledger 生成逐币种现金行，market value total 包含非现金市值与 settled cash，pending settlement 仍单独进入 NAV。

### 2. 前端

```bash
cd apps/portfolio/frontend
npm install
npm run dev
```

说明：

- Vite 默认仅监听 `127.0.0.1:5174`；如需远程访问，请通过受控的反向代理显式开放
- `/api` 已代理到 `http://127.0.0.1:8001`
- Holdings 与 Performance Calculation 的自定义 table view 仅持久化在后端 `portfolio_table_view_store` 中，并按 `portfolio_id + view_scope` 隔离。空 store 使用规范系统默认；读取失败时界面明确标记 table view unavailable，且不会读取或回写浏览器旧配置。列宽与 Performance 时间窗口等纯界面偏好仍可独立保存在浏览器中。

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

## 计算层当前口径

- 组合级 TWR 使用日频 true time-weighted 口径：外部流入进分母，外部流出加回分子，区间结果几何复合。
- 已有组合的显式 Performance 区间统一为 close-to-close：`start_date` 和 `end_date` 都是 EOD 边界，收益链接 `(start_date, end_date]`。MTD / QTD / YTD 锚定上月末 / 上季末 / 上年 `12-31`；普通起始日交易已进入期初状态，不重复计为区间 flow 或 P&L。请求起点正好等于 funded-segment start（首次入金，或组合 NAV 真正归零后的再次入金）时，保留该日 BOD-to-EOD 子期间；导入式 opening balance 则作为无首日收益的 EOD anchor。留存的无收益现金仍属于组合 NAV，不触发重启；后续新增 instrument 也不触发该例外。
- FIFO / moving average 只影响 book cost、book realized gain、book unrealized P&L 和 lot 展示；不影响 fair-value based TWR。
- 普通 dividend / coupon 是 entitlement-date 已实现 Income，不冲减 open-position cost，也不进入 Holdings Unrealized P&L；dividend reinvestment 先确认 Income、再以 reinvested gross amount 建立新 lot cost；只有明确的 return of capital 才冲减 remaining cost basis。
- Performance `Calculation` 使用 `Initial Value + Net External Flow + Period P&L = Final Value` 的期间桥接。资本利得拆分使用期初市值重置后的期间成本，而不是账户 book cost。Calculation 默认视图命名为 `Default`，展示 beginning / average / ending weight 与 realized risk attribution；用户可像 Holdings 一样保存自定义表格视图。Group By 默认是 `None`，内部映射到底层 instrument lines；也支持 instrument type / currency / account / taxonomy，TWR 与 contribution 在后端按对应轴计算，表格可导出 CSV。taxonomy group view 使用区间期末 assignment 解释复盘归因；若 instrument 期末已清仓且期末不再有 active assignment，则使用其区间内有效 assignment 承接历史 P&L，不误归入 Unassigned；cash 作为独立组保留。
- `Holdings` 中的 `Market Value Base` 是 base-currency fair value；现金行使用 settled cash 的 base value。Day change 优先使用拆分和分配处理后的 total-return basis，缺失时才回退到 valuation basis，并把该日收益应用到 as-of date 当前持仓规模；非 base cash 使用 FX 变动。Instrument `1W / 1M / 3M / 6M / MTD / YTD / 1Y Return`、volatility 和 drawdown 只使用 Registry 确认的 total-return series，按策略顺序锁定单一 basis；不能用普通价格序列或 sampled price chart 替代。Portfolio 与 Watchlist 各自实现，但同一 instrument、as-of 和窗口必须得到相同 total return。Holdings group / subtotal / total 的 return 使用当前 base-value 权重，volatility / drawdown 基于当前权重合成的组 return series 计算，不使用成员风险指标的加权平均，也不表示历史实际组合 TWR。Holdings 主表的 locked `instrument` 首列在横向滚动时保持冻结；只有当前视图需要非默认 chart 或组合波动率 / 回撤时才请求 full return-series payload。
- `moving_average` 在底层按 `account + instrument` 维护一个 rolling average cost bucket；API 为 UI 和转仓审计输出一个 synthetic position lot。
- `Overview`、`Performance` 的 TWR index、daily series 和 drawdown 均按查询窗口重新复合；不得复用 inception-to-date 的累计 TWR 作为区间曲线。Overview 曲线只绘制 `return_observation_eligible=true` 的真实市场观察点；周末、休市日或其他仅沿用上一估值的会计快照可以保留在 daily snapshot 中，但不得作为 NAV / TWR 图表点。真实交易日即使收益为零也必须保留。Overview 的组合收益、benchmark 和 1M / 3M VOL 均以组合 fresh complete as-of 截止，不使用浏览器日期或系统日期。Overview 的组合 YTD 必须存在年初锚点，年内成立且缺少年初锚点时显示 unavailable，由 `Since Inception` 承接成立以来收益。Overview 的 1M / 3M VOL 使用组合 eligible daily TWR 的 trailing 年化波动率，并要求完整窗口历史。
- Performance `Calculation` 的 group `period_return` 是 group-level TWR：买入、卖出、分红、兑付和现金转移先识别为组内 capital flow，再用 `total_pnl / (beginning_value + period capital flow in)` 计算收益，避免期内新增仓位或往返交易把资金流误识别为收益。手动 benchmark compare 只在同币种、起点锚点和组合 eligible return date 覆盖完整时展示，不能用 raw-currency 或 stale-filled benchmark 序列兜底。基准序列统一按窗口起点归一化；确认的全收益序列作为 canonical comparator，确认的价格指数也展示 benchmark、自身指标、差值和相对统计，但明确提示组合 TWR 与价格指数之间包含收益口径差异。未确认收益语义的 `close / price / valuation` 序列只作 exploratory 展示，不计算相对指标。
- Holdings 的 group / subtotal / total 行对 market value、cost basis、day change、unrealized P&L 等绝对量按组内明细加总；unrealized return 始终使用当前非现金 open-position unrealized P&L 除以对应非现金 open-position cost basis，现金既没有 unrealized P&L，也不进入该比例分母，纯现金行不显示该比例。
- `Held Max DD` 只在 Holdings instrument row 展示；不同持仓起点的成员不合成 group、subtotal 或 portfolio total。
- Holdings 对 group 字段显式分类：市值/开放成本/未实现金额等绝对量加总，比例重新计算，total return 与风险按当前权重合成，Forward RC 只在同一完整风险模型下加总；Quantity、Avg Cost、Quote、Holding Since、Chart、Coverage 等单标的字段不做分组值。当前权重 return/risk 的市值覆盖必须完整，不静默剔除缺失成员；成员 return currency 不一致且没有 base-currency return series 时同样留空，禁止把不同币种的本地收益直接拼成组合收益。
- `Risk` 的 rolling volatility / Sharpe、相关性矩阵和 Current Drift 是当前权重口径：使用当前非现金持仓权重乘以资产自身全历史收益窗口，不被组合成立日或真实持仓起始日截断。它不是 realized attribution；若需要真实持仓期间复盘，归入 Performance `Calculation`。Risk 排除仅由 stale price carry-forward 得到的非市场观察日；`sample_covariance` 使用样本协方差 `n - 1`，不使用总体协方差。混合频率和稀疏序列必须使用 Holdings workspace 的 `risk_basis` 对齐元数据解析 daily / weekly / monthly calculation basis，再按目标 period 的最后有效观测对齐，不跨期前向填充；active return matrix 的 dates 和 period start/end 必须完全一致，Rolling Risk 也不得用交集日期静默丢样本。Production window 从请求 as-of date 回看自然月，并按 `(start EOD, as-of EOD]` 选择收益行；它不是固定天数或交易日数量窗口，也不包含结束于 start boundary 的前一区间收益。窗口同时校验最小样本、窗口起点、至少 80% elapsed-day 覆盖和尾部新鲜度。区间风险贡献归入 Performance `Calculation` 的 realized risk attribution columns。
- `Risk` Correlation Matrix 默认 scope 是 Current Holdings，Full Universe 仅作为显式可选分析。矩阵展示完整覆盖窗口的 sample correlation；成员、period start/end 和日期序列必须完全一致，EWMA、vol shrinkage 和相关性收缩不作为相关性矩阵的用户选项。
- `Risk` Current Drift 在 instrument-scope taxonomy 下把非现金 Holdings rows 与全部 Accounts 的 base-currency cash + pending settlement 合成当前 NAV；证券账户 positions 只通过 Holdings 计入一次。现金只参与 capital / weight drift；一个直接 cash bucket 会与当前汇总现金比较。`Risk Target Gap` 只比较承担风险的非现金成员，现金不建 risk target share、不进入风险预算 100% 分母。Forward covariance 只在 leaf instruments 上运行一次，taxonomy risk share 按同一组合 variance 分母加总；只有 base-currency cash 可视为零风险，non-base cash 和非本币本地收益在没有 FX total-return 时保持 unavailable。
- `Risk Health` 汇总 Forward Volatility/coverage、SAA 或 TAA 的 risk-budget absolute gap、非现金 Top-3/HHI，以及 Accounts cash + pending settlement；缺失输入不按 0 处理。Benchmark rolling comparison 要求同币种和已确认的 return semantics，价格收益口径会显示分红差异提示。
- `Research` 的当前 target solve 从最末端 sleeve 递归向上求解；scope default 只使用该 scope 自身的默认目标维度，不静默切到另一个维度。多成员 scope 必须存在 active complete `SAA` 或 `TAA` target set；缺失目标、目标加总错误、strict missing-return policy 下出现单成员缺失、`complete_case_drop` 超过覆盖率/尾部新鲜度上限、risk-budget 求解不能满足 `1e-4` risk-share gap 阈值时，run 明确失败或标记 unavailable，不回退到目标权重、等权或旧算法。`risk_budget` target 只包含非现金风险成员且合计 100%，纯现金 scope 没有风险预算。顶层 capital overlay 在风险 sleeve 权重求出后再按目标波动率、波动率上限或总敞口缩放，并把剩余权重放到系统 cash；这部分残余现金是求解输出，不是 risk target。top sleeve bounds 只作用于 root 直接 sleeve，违反上下限时 run 必须失败。Research backtest 使用同一 Production Risk Model 与 target solve 逻辑逐期重算，支持 1W / 1M / 3M rebalance；组合成员共同历史不足一个风险窗口时返回空 backtest warning，不生成越界日期或短窗口替代结果。已完成 run 可以在不重跑 target solve 的情况下切换 benchmark 做共同期比较；`excess_return` 保持组合共同期收益减基准共同期收益，tracking error / information ratio 来自 active return 序列。
- `IRR / MWROR` 是资金效率补充指标；若数学上不可解，不应降低 TWR 口径的 coverage。
- 绩效方法参考 Portfolio Performance 的账本模型，并吸收 GIPS 的 TWR 优先、外部现金流政策、估值频率和方法一致性原则；本项目不声称 GIPS compliance，详见 [docs/02_GIPS_ALIGNMENT.md](./docs/02_GIPS_ALIGNMENT.md)。

## 读路径与性能约束

- daily snapshots、holding snapshots 和 contribution slices 是可重建读模型；核心 performance、holdings 和 contribution 读路径应优先复用物化结果。
- `performance.py` 只保留跨模块绩效编排；`valuation_fx`、`return_chain`、`period_metrics`、`attribution` 与 `holdings_market_profile` 是各自职责的唯一实现，生产调用方直接依赖对应模块，不再通过同名转发层。
- 物化读模型的 payload 或计算口径变化必须提升 `calculation_version` 并触发重建；不要在核心读路径长期保留旧 slice schema 的兼容分支。
- 交易、账户、共享行情或 FX 变化必须先写源事实，再通过 `refresh_request_id` 标记受影响组合 stale，并由后台刷新串行重建。
- portfolio 存在性校验应保持轻量，不应触发 workspace rollup、行情 profile 或全窗口 performance 重算。
- `Overview / Risk` 这类组合页面应避免同一窗口内重复拉取 shared instrument detail 或重复重建 performance 中间结果。
- 前端文案保持专业终端口径：loading 统一为 `Loading`，空态压缩为短句，字段/分组选项和 table view 不展示解释性备注。需要保留的诊断只限错误、校验失败和会影响用户判断的不可用状态。
