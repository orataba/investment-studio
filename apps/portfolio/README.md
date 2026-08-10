# Portfolio Operations Workbench Portfolio

这是 `Portfolio Operations Workbench / Portfolio` app 的工作区。

当前状态：

- `backend/` 与 `frontend/` 都已可运行
- 组合内核当前以 `portfolio / account / transaction` 为数据库主事实
- FCN 与期权合约是 Portfolio 本地不可变事实，不注册为共享 Registry instrument；首笔交易原子创建合约，后续事件只引用同一 `derivative_contract_id`
- 持仓、lots、ledger、performance 由内核服务按需推导
- `Holdings / Accounts / Transactions / Performance` 已有真实 API 与页面支撑。
  `Transactions` 支持带幂等保护的 create、强制 row-version 的 update / delete、成对内部转仓、
  交易变更历史，以及 trade / position-effective / settlement / entitlement 日期的独立录入。
- `Risk` 已有真实工作台，包含 rolling annualized volatility / Sharpe、sample 相关性矩阵和 Current Drift；Production Risk Model 的风险窗口固定为 `1M / 3M / 6M / 12M / 24M`，协方差方法和风险贡献模式与 Research 使用同一组严格口径，相关性矩阵只暴露窗口和 scope，不混入协方差模型选择
- `Taxonomies` 已有真实配置工作台，支持 Securities 层级 sleeve tree、instrument assignment、`TargetSet` 与 `default planning taxonomy`。Cash 与 Derivatives 是不可分类、不可移动的系统成员：可以参与 capital / weight target，但不得建立 `target_risk_share`，也不进入 `risk_budget` target completeness。
- `Research` 已有真实工作台，支持基于 planning taxonomy / TargetSet / 当前持仓的递归 target-weight solve、top sleeve bounds、vol target/cap、1W/1M/3M point-in-time target-policy simulation、benchmark 动态比较、风险预算诊断、回撤指标和调仓缺口。资产状态区分 Held / Observed / Former；former instrument 的正目标在未经 PM approval 时标记为人工复核。新 run 只有成功完成后才替换上一轮成功结果；失败 run 会保留上一轮 completed run 供继续查看。
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

- 普通市场资产只依赖共享 `instrument-core` contract 与 `instrument_registry` schema，不通过 `platform` API 取数；FCN/期权合约及事件由 Portfolio 自己拥有
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
uvicorn portfolio_app.main:app --reload --host 127.0.0.1 --port 8001
```

说明：

- `PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL` 必须由仓库外的 `portfolio.env` 显式提供，并与 Platform、Watchlist 指向同一个 canonical PostgreSQL；backend 目录只保留 `.env.example` 键名模板，不创建 `.env` 文件或软链接
- schema 固定为 canonical `portfolio`；非空的 schema 环境变量只能取该值
- 现有本机真实数据库使用仓库根目录的 `infra/launchd/install_local_services.sh` 完成停写、四 schema 备份、统一迁移和失败恢复；不要单独运行 Portfolio Alembic
- 测试使用临时 SQLite，不会污染默认运行库
- research 运行产物默认落在 `backend/research_outputs/`，用于本地查看和回放，已按运行时目录管理；当前产物以 request、context、target rows、member/leaf targets、solved result groups、solve event、scope solve events、target weight gaps 和 backtest payload 为主
- backend 顶层包名现在是 `portfolio_app`
- 交易、账户和研究快照使用 canonical `instrument_id` / `instrument_name` / `instrument_type`；迁移会规范历史 JSON，运行时不再接受 `asset_id` / `asset_name` / `asset_type` 或 `allowed_asset_types`
- daily snapshots 已物化到数据库，并显式保存每日 `beginning_nav` / `ending_nav`；`Performance`、`Holdings`、instrument/account contribution 读路径默认复用物化结果。交易、账户或行情变更会用 `refresh_request_id` 把相关组合标记为 stale 并触发刷新。若刷新中又收到新数据，当前计算不会清掉新的 stale 标记，而是串行再跑一轮后才置为 current。组合 summary、Overview 和未显式指定日期的 Holdings 默认使用最新的 fresh complete snapshot：`coverage_state = complete`、`nav` 存在且 `stale_price_flag = false`，避免部分持仓价格/NAV 尚未更新时把组合 as-of 推到更晚日期；FX staleness 保留为质量标记，不单独决定组合资产新鲜度，也不作为资产新鲜度兜底。查询窗口早于组合首个物化日期时，只允许在组合成立前的自然空窗上裁剪；若物化快照缺少交易后历史或 as-of 之前尾部日期，读路径不得返回空结果掩盖缺口。
- `Holdings` 的物化行包含 instrument market profile、analytics scope identity、operational fields 与 `risk_basis` 对齐元数据；动态、物化和 instrument-detail 读路径必须返回同一合同。`risk_basis` 只描述 `risk_eligible=true` 市场持仓的 daily / weekly / monthly return alignment，不是风险指标；频率优先使用 Registry expected frequency，daily 数据按各自 market calendar 校验预期 session。若任一 eligible member 缺少可解析来源频率或存在预期观测缺口，即使所有成员共同缺同一天，Risk 页面和 Forward RC 也必须标记 basis incomplete。Holdings 是一张表，固定分为 Securities、Derivatives、Cash & Settlement 三个区段；总 NAV 包含 market value、event carrying assets、short-option premium liabilities、settled cash 和 pending settlement。
- Short option 行显示 Call/Put 合约数、到期日、行权价、乘数、到期对应标的数量和剩余权利金负债，不分配或校验标的覆盖。期权合约不预设结算方式，只记录 expiry 或 cash settlement；实际实物交割规范化为期权现金结算和交割日市场/参考价的独立股票交易，不建立技术关联。FCN 关闭与接收资产同样为独立交易。
- Analytics scope 由 effective-dated taxonomy selection、configuration revision 和独立 policy record 解析；exact node 优先，再继承最近祖先或 root，unassigned 只使用自身 policy。Policy 同时驱动 Risk/Risk Budget eligibility、performance scope、valuation basis 和 cash-activity disclosure。当前尚未维护 sleeve cash subledger，因此 ordinary-sleeve TWR 明确 unavailable，不能从 total operational return 中过滤衍生品后伪造。

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
- 需要组合时，显式通过前端 `/portfolios` 页面、带必填 `base_currency` 的 `POST /api/portfolios`，或 [backend/scripts/import_real_portfolio_from_csv.py](./backend/scripts/import_real_portfolio_from_csv.py) 创建
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
- FIFO / moving average 只影响 book cost、book realized gain、book unrealized P&L 和 lot 展示；在公允价值覆盖完整的普通市场资产组合中不影响 fair-value based TWR。FCN 和长期权没有公允价值时按 remaining transaction basis carried，相关总组合收益只能称为 operational/carrying-basis return，不能称为完整 fair-value 或 GIPS-informed TWR。
- 普通 dividend / coupon 是 entitlement-date 已实现 Income，不冲减 open-position cost，也不进入 Holdings Unrealized P&L；dividend reinvestment 先确认 Income、再以 reinvested gross amount 建立新 lot cost；只有明确的 return of capital 才冲减 remaining cost basis。
- Performance `Calculation` 使用 `Initial Value + Net External Flow + Period P&L = Final Value` 的期间桥接。资本利得拆分使用期初市值重置后的期间成本，而不是账户 book cost。Calculation 默认视图命名为 `Default`，展示 beginning / average / ending weight 与 realized risk attribution；用户可像 Holdings 一样保存自定义表格视图。Group By 默认是 `None`，内部映射到底层 instrument lines；也支持 instrument type / currency / account / taxonomy，TWR 与 contribution 在后端按对应轴计算，表格可导出 CSV。taxonomy group view 使用区间期末 assignment 解释复盘归因；若 instrument 期末已清仓且期末不再有 active assignment，则使用其区间内有效 assignment 承接历史 P&L，不误归入 Unassigned；cash 作为独立组保留。
- `Holdings` 中普通市场资产的 `Market Value Base` 是 base-currency fair value，现金行使用 settled cash 的 base value。事件型 FCN/长期权使用 `valuation_basis=carried_cost`，期权卖方义务使用 `valuation_basis=premium_liability` 和负的 NAV amount；二者的 `fair_value`、quote identity、Day Change / Day Return 均为空，也不进入 Priced Lines 或 covariance。Forward Risk 只对 effective-dated taxonomy policy 标记 `risk_eligible=true` 的市场资产建 covariance，但权重统一为 `signed market_value_base / total NAV`：base-currency cash 与衍生品作为 0-return capital 稀释组合风险，non-base monetary exposure 缺少 FX returns 时 fail closed。输出披露 `excluded_carrying_value`、`excluded_liability`、coverage ratio 和 excluded rows；衍生品行自身仍是 `N/A / excluded`。没有 eligible risky holding 时组合风险 unavailable，不能报告成实际 0。普通市场资产的 Day change 优先使用拆分和分配处理后的 total-return basis，非 base cash 使用 FX 变动。Instrument 自身 return/volatility/drawdown 只使用 Registry 确认的 total-return series。Holdings group / subtotal 的 volatility 与 risk drawdown 使用各自篮子权重，`Portfolio Total` 使用 total NAV；衍生品和本币现金在所在 scope 内为 0-return capital。所有聚合都不使用成员风险指标的加权平均，也不表示历史实际组合 TWR。
- `moving_average` 在底层按 `account + instrument` 维护一个 rolling average cost bucket；API 为 UI 和转仓审计输出一个 synthetic position lot。
- `Overview`、`Performance` 的经营 TWR index/daily series 按查询窗口重新复合；风险卡片使用独立 `market_risk_daily_return`。衍生品 cash result、FCN coupon/charges 和现金利息完整进入 NAV 与 operational return，但从 market-risk P&L 分子剔除；普通证券 total return/income 与 non-base cash FX 保留。Calculation 的 group volatility / correlation / beta / realized RC 使用 slice 级 `market_risk_daily_return` 与 `market_risk_daily_contribution`，不复用经营字段或资产分类过滤。Overview 的 1M / 3M VOL 和 risk drawdown 使用 market-risk eligible observations，经营收益与风险 benchmark 比较不混链。周末、休市日或仅沿用上一估值的快照不作为观察点，真实交易日即使风险收益为零也保留。两条链使用同一 fresh complete as-of。
- Performance `Calculation` 的 group `period_return` 是 group-level TWR：买入、卖出、分红、兑付和现金转移先识别为组内 capital flow，再用 `total_pnl / (beginning_value + period capital flow in)` 计算收益，避免期内新增仓位或往返交易把资金流误识别为收益。手动 benchmark compare 只在同币种、起点锚点和组合 eligible return date 覆盖完整时展示，不能用 raw-currency 或 stale-filled benchmark 序列兜底。基准序列统一按窗口起点归一化；确认的全收益序列作为 canonical comparator，确认的价格指数也展示 benchmark、自身指标、差值和相对统计，但明确提示组合 TWR 与价格指数之间包含收益口径差异。未确认收益语义的 `close / price / valuation` 序列只作 exploratory 展示，不计算相对指标。
- Holdings 的 group / subtotal / total 行对 market value、cost basis、open lots 等绝对量按组内明细加总；无非零 event exposure 时，day change 与 market-valued position 的 unrealized P&L 同样加总，unrealized return 使用当前非现金 open-position unrealized P&L 除以对应 open-position cost basis。只要 scope 内存在非零 event-valued asset 或 written option obligation，Day Change、Day Return、Unrealized P&L、Unrealized Return 与当前权重 instrument return 为 `N/A`；Vol / risk drawdown 在各自 scope 内把衍生品与本币现金作为 0-return capital，Forward RC 使用 total NAV 权重，但衍生品行自身固定为 `N/A / excluded`。没有普通市场资产或非本币货币风险的纯本币现金/衍生品组合风险 unavailable。
- `Held Max DD` 只在 Holdings instrument row 展示；不同持仓起点的成员不合成 group、subtotal 或 portfolio total。
- Holdings 对 group 字段显式分类：市值/开放成本/未实现金额等绝对量加总，比例重新计算，total return 与风险按当前权重合成，Forward RC 只在同一完整风险模型下加总；Quantity、Avg Cost、Quote、Holding Since、Chart、Coverage 等单标的字段不做分组值。当前权重 return/risk 的市值覆盖必须完整，不静默剔除缺失成员；成员 return currency 不一致且没有 base-currency return series 时同样留空，禁止把不同币种的本地收益直接拼成组合收益。
- `Risk` 的 rolling volatility / Sharpe、相关性矩阵和 Current Drift 是当前总 NAV 权重口径：市场资产使用自身全历史收益窗口，base-currency cash 与衍生品为 0-return capital；non-base monetary exposure 必须有 FX returns。它不被组合成立日或真实持仓起始日截断，也不是 realized attribution；真实期间复盘归入 Performance `Calculation`。Risk 排除 stale carry-forward 非市场观察日；`sample_covariance` 使用样本协方差 `n - 1`。混合频率和稀疏序列使用 Holdings `risk_basis` 对齐 daily / weekly / monthly period，active matrix 的 dates 和 period start/end 必须完全一致，不跨期填充或静默取交集。Production window 从请求 as-of date 回看自然月，并按 `(start EOD, as-of EOD]` 选择收益行，同时校验最小样本、窗口起点、elapsed-day 覆盖和尾部新鲜度。
- `Risk` Correlation Matrix 默认 scope 是 Current Holdings，Full Universe 仅作为显式可选分析。矩阵展示完整覆盖窗口的 sample correlation；成员、period start/end 和日期序列必须完全一致，EWMA、vol shrinkage 和相关性收缩不作为相关性矩阵的用户选项。
- `Risk` Current Drift 在 instrument-scope taxonomy 下把非现金 Holdings rows 与全部 Accounts 的 base-currency cash + pending settlement 合成当前 NAV；证券账户 positions 只通过 Holdings 计入一次。`Risk Target Gap` 只比较 `risk_budget_eligible=true` 的市场风险成员，现金与衍生品不建 risk target share、不进入风险预算目标 100% 分母。Forward covariance 只在 `risk_eligible=true` leaf instruments 上运行一次，权重按 total NAV 计算，taxonomy risk share 按同一 total-portfolio variance 分母加总；excluded exposure 和 cash 同时通过 coverage disclosure 保留。
- `Risk Health` 汇总 Forward Volatility/coverage、SAA 或 TAA 的 risk-budget absolute gap、非现金 Top-3/HHI，以及 Accounts cash + pending settlement；缺失输入不按 0 处理。Benchmark rolling comparison 要求同币种和已确认的 return semantics，价格收益口径会显示分红差异提示。
- `Research` 的当前 target solve 从最末端 sleeve 递归向上求解；scope default 只使用该 scope 自身的默认目标维度，不静默切到另一个维度。多成员 scope 必须存在 active complete `SAA` 或 `TAA` target set；缺失目标、目标加总错误、strict missing-return policy 下出现单成员缺失、`complete_case_drop` 超过覆盖率/尾部新鲜度上限、risk-budget 求解不能满足 `1e-4` risk-share gap 阈值时，run 明确失败或标记 unavailable，不回退到目标权重、等权或旧算法。`risk_budget` target 只包含非现金风险成员且合计 100%，纯现金 scope 没有风险预算。顶层 capital overlay 在风险 sleeve 权重求出后再按目标波动率、波动率上限或总敞口缩放，并把剩余权重放到系统 cash；这部分残余现金是求解输出，不是 risk target。top sleeve bounds 只作用于 root 直接 sleeve，违反上下限时 run 必须失败。Research 历史曲线是 point-in-time target-policy simulation：universe 来自 taxonomy revision 历史，每个 1W / 1M / 3M 决策日使用当时有效的 taxonomy/targets 与当时可见数据重新求解，并披露 configuration version 和 skipped decisions；现金按配置收益复合，交易扣除 commission、sell tax、slippage，并按 implementation delay 执行。若调仓边界上的旧持仓缺少完整 EOD return observation，该次执行会 fail closed、保留旧持仓并将 coverage 标记为 partial。输出包含 execution records、turnover/cost、sleeve contribution reconciliation、替代摩擦 assumptions 的 robustness scenarios 和 rolling temporal holdout OOS windows；当前假设目标完整成交，未模拟拒单、部分成交、流动性容量或 market impact。已完成 run 可以在不重跑 target solve 的情况下切换 benchmark 做共同期比较；`excess_return` 保持组合共同期收益减基准共同期收益，tracking error / information ratio 来自 active return 序列。
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
