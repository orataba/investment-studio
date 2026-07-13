# Portfolio Operations Workbench Portfolio

这是 `Portfolio Operations Workbench / Portfolio` app 的工作区。

当前状态：

- `backend/` 与 `frontend/` 都已可运行
- 组合内核当前以 `portfolio / account` 与 stable transaction identity + append-only revision/group 为数据库主事实
- 持仓、lots、ledger、performance 由内核服务按需推导
- `Holdings / Accounts / Transactions / Performance` 已有真实 API 与页面支撑
  `Transactions` 当前支持 create / amend / tombstone delete、History、actor/reason 和 optimistic concurrency；
  `transaction_current` 只投影最新有效版本，内部转仓按同一 mutation 的原子配对事实管理
- `Risk` 已由后端权威 Risk Workspace 支撑，包含 current-static-weight rolling annualized volatility / Sharpe、与 Production Risk Model 一致的相关性矩阵、taxonomy risk contribution 和 Current Drift；浏览器只提交窗口、scope、matrix as-of 与 benchmark 参数并展示结果，不再实现收益、覆盖、协方差或风险贡献引擎
- `Taxonomies` 已有真实配置工作台，支持层级 sleeve tree、assignment、`TargetSet`、`default planning taxonomy` 与 `cash_bucket` 维护
- 当前 UI 中的 `Research` 严格指 `Allocation Research / Allocation Lab`，不是通用策略研究。它支持基于 planning taxonomy / TargetSet / 当前持仓的递归 target-weight solve、top sleeve bounds、vol target/cap、1W/1M/3M policy replay、benchmark 动态比较、latest run 结果、风险预算求解诊断、回撤指标和配置缺口。新 run 只有成功完成后才替换上一轮 completed run；失败 run 会保留上一轮成功结果供继续查看。
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

## 开发原则

- 只依赖共享 `instrument-core` contract 与 `instrument_registry` schema，不通过 `platform` API 取数
- 不复用 `Watchlist` 的业务模型
- 维持 `portfolio / account / transaction / performance / risk / research` 的独立边界
- 除显式归类的 ETF 轮动策略组合外，`Taxonomy` 是普通组合分类、归因、风险聚合和跟踪的标准主轴；其中 planning-enabled taxonomy / `TargetSet` 是 Allocation Research 的 planning truth
- 当前 `Research` 消费 planning 结构求解配置权重，不反向创造另一套目标体系；显式 ETF 轮动策略组合不强制复用 taxonomy、风险预算、TargetSet 或 policy drift，仅持有 ETF 的普通组合仍使用 taxonomy
- ETF Strategy Live 当前属于外部项目；本 app 不复制其信号、目标、风险预算或 drift 模型
- 运行时生成的 research artifacts 属于本地工作产物，不作为源码的一部分

## 快速启动

以下命令默认从仓库根目录执行；如果已经在 `apps/portfolio` 目录，可相应省略路径前缀。

### 1. 后端

```bash
cd apps/portfolio/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=portfolio_ops PYTHONPATH=. alembic upgrade head
uvicorn portfolio_app.main:app --reload --host 127.0.0.1 --port 8001
```

说明：

- 数据库连接通过 `PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL` 配置；真实 backend `.env` 由本机受限秘密目录提供，不进入 Git
- `portfolio` 使用 `portfolio` schema
- 测试使用临时 SQLite，不会污染默认运行库
- research 运行产物默认落在 `backend/research_outputs/`，用于本地查看和回放，已按运行时目录管理；当前产物以 request、context、target rows、member/leaf targets、solved result groups、solve event、scope solve events、target weight gaps 和 backtest payload 为主
- backend 顶层包名现在是 `portfolio_app`
- 交易、账户和研究快照使用 canonical `instrument_id` / `instrument_name` / `instrument_type`；迁移会规范历史 JSON，运行时不再接受 `asset_id` / `asset_name` / `asset_type` 或 `allowed_asset_types`
- Portfolio migration `20260713_0036` 已把旧 mutable `transaction_record` 转为 stable identity、append-only
  revision/group 和只读 `transaction_current`。quantity/price 使用 `NUMERIC(38,12)`，金额/费用使用
  `NUMERIC(38,8)`，FX rate 使用 `NUMERIC(38,18)`；交易 API 以 plain-decimal string 保持精度。修改和删除
  保留完整 History，过期 revision 写入返回冲突，内部转账两腿原子创建和删除。0036 对旧 binary-float 尾差采用
  显式 round-half-even，并在 migration group 记录受影响字段和 transaction ids；新 API 仍拒绝任何隐式舍入。通用 calculation input manifest
  与 reconciliation run 尚未完成，属于下一阶段。
- Portfolio migration `20260713_0037` 在生产 PostgreSQL 再增加独立数据库防线：每条 revision 的
  canonical v1 payload hash 由数据库从落库 facts 重算核验；deferred constraint 在 commit 时验证内部转账
  必须一进一出、账户互惠、字段镜像、同组创建/删除、不可 amend 且历史 group 不复用。直接 SQL 也不能绕过。
- Portfolio migration `20260713_0038` 将 0036 基线组中泄漏的实现名 `actor_source=alembic` 精确归一化为
  领域值 `migration`，并在数据库与 service 同时封闭 actor source/type 配对；API 不保留旧值 alias 或展示兜底。
- daily snapshots 已物化到数据库，并显式保存每日 `beginning_nav` / `ending_nav`；`Performance`、`Holdings`、instrument/account contribution 读路径默认复用物化结果。交易、账户或行情变更会用 `refresh_request_id` 把相关组合标记为 stale 并触发刷新。若刷新中又收到新数据，当前计算不会清掉新的 stale 标记，而是串行再跑一轮后才置为 current。组合 summary、Overview 和未显式指定日期的 Holdings 默认使用最新的 fresh complete snapshot：`nav_coverage_state = complete`、`nav` 存在且 `stale_price_flag = false`。book P&L coverage 与 TWR reliability 独立披露，成本或历史 FX 归因缺口不得把已完整的 fair-value NAV 降级。查询窗口早于组合首个物化日期时，只允许在组合成立前的自然空窗上裁剪；若物化快照缺少交易后历史或 as-of 之前尾部日期，读路径不得返回空结果掩盖缺口。
- `Holdings` 的物化行包含 instrument market profile 与 `risk_basis` 对齐元数据；读路径命中物化 profile 时不再逐行重建行情趋势和风险字段。`risk_basis` 只描述 Holdings 自身的 daily / weekly / monthly return alignment，不是风险指标；Risk Workspace 会在自己的同一 Session canonical lock 内独立解析并披露 production risk basis，不能把 Holdings 浏览器 payload 当成风险计算事实。Holdings 还会按 settled cash ledger 生成逐币种现金行，market value total 包含非现金市值与 settled cash，pending settlement 仍单独进入 NAV。非现金行的 local/base unrealized P&L 与 unrealized return 由后端成本和估值事实计算；现金行固定为不适用，组合合计只聚合覆盖完整的非现金行。

### 2. 前端

```bash
cd apps/portfolio/frontend
npm install
npm run dev
```

说明：

- Vite 默认仅监听 `127.0.0.1:5174`；如需远程访问，请通过受控的反向代理显式开放
- `/api` 已代理到 `http://127.0.0.1:8001`
- Holdings 与 Performance Calculation 的自定义 table view 存在后端 `portfolio_table_view_store` 中，按 `portfolio_id + view_scope` 隔离；浏览器 `localStorage` 只作为首次迁移和后端不可用时的本地 fallback，不在读取失败时回写覆盖后端配置。

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

- 组合级 TWR 使用日频 time-weighted 口径：外部流入进分母，外部流出加回分子，区间结果几何复合；`deposit / withdrawal` 统一以 cash value / `settlement_date` 进入 NAV、TWR、IRR、contribution、calendar bucket 和 Calculation bridge，较早的录入 `trade_date` 不提前形成组合资本或 pending NAV；缺少 value date 的历史输入 fail closed，不回退到 trade date。
- TWR 可靠性独立于 book P&L coverage：正常 calendar carry 与 resolver 明示 stale 分开披露；任何 carried / stale / incomplete valuation 遇到外部现金流都会形成不可跨越的 return boundary，fresh valuation 只负责 re-anchor。跨断点的 return、drawdown 与 contribution fail closed，不把不可用收益或归因伪装成 0。
- FIFO / moving average 只影响 book cost、book realized gain、book unrealized P&L 和 lot 展示；不影响 fair-value based TWR。
- Performance `Calculation` 使用 `Initial Value + Net External Flow + Period P&L = Final Value` 的期间桥接。资本利得拆分使用期初市值重置后的期间成本，而不是账户 book cost。Calculation 默认视图命名为 `Default`，展示 beginning / average / ending weight 与 realized risk attribution；用户可像 Holdings 一样保存自定义表格视图。Group By 默认是 `None`，内部映射到底层 instrument lines；也支持 instrument type / currency / account / taxonomy，TWR 与 contribution 在后端按对应轴计算，表格可导出 CSV。taxonomy group view 使用区间期末 assignment 解释复盘归因；若 instrument 期末已清仓且期末不再有 active assignment，则使用其区间内有效 assignment 承接历史 P&L，不误归入 Unassigned；cash 作为独立组保留。
- `Holdings` 中的 `Market Value Base` 是 base-currency fair value；现金行使用 settled cash 的 base value。非现金 Day return/change 只使用 canonical `total_return` role 的同一 locked series 前后 complete 点，valuation role 只用于 market value；任一点缺失或状态异常时 return 字段为空，不能用 valuation/chart 补齐。价格/NAV 图表和 sparkline 只使用独立的 canonical `chart` role，不能反向进入 return、volatility、drawdown 或 risk-frequency 推断。Holdings / Taxonomy 对 instrument set 在同一 Session 批量锁定 chart 与 total-return 窗口；partial/rejected/withdrawn 或 late 只会使对应角色 fail closed，不跨角色或跨 basis 兜底。非 base cash 使用 FX 变动。Instrument return / volatility / drawdown 基于未下采样的 total-return series 和 resolved risk frequency；volatility 有窗口起点覆盖和最小样本门槛。Holdings group rows 的 return 使用当前 base-value 权重，volatility / drawdown 基于组 return series 计算，不使用成员风险指标的加权平均。Holdings 主表的 locked `instrument` 首列在横向滚动时保持冻结。
- `moving_average` 在底层按 `account + instrument` 维护一个 rolling average cost bucket；API 为 UI 和转仓审计输出一个 synthetic position lot。
- `Overview`、`Performance` 的 TWR index、daily series 和 drawdown 均按查询窗口重新复合；不得复用 inception-to-date 的累计 TWR 作为区间曲线。Overview 的组合收益、benchmark 和 1M / 3M VOL 均以组合 fresh complete as-of 截止，不使用浏览器日期或系统日期。Overview 的组合 YTD 必须存在年初锚点，年内成立且缺少年初锚点时显示 unavailable，由 `Since Inception` 承接成立以来收益。Overview 的 1M / 3M VOL 使用组合 eligible daily TWR 的 trailing 年化波动率，并要求完整窗口历史。
- Performance `Calculation` 的 group `period_return` 是 group-level TWR：买入、卖出、分红、兑付和现金转移先识别为组内 capital flow，再用 `total_pnl / (beginning_value + period capital flow in)` 计算收益，避免期内新增仓位或往返交易把资金流误识别为收益。Performance benchmark compare 是后端权威计算：同一数据库 Session 内读取物化组合 TWR 并锁定 benchmark canonical `total_return` role；只有真实 total-return basis、同币种、可靠起点锚点及全部 eligible return dates 精确覆盖时才输出 benchmark、difference、tracking error、information ratio、beta、capture 和图形 index。浏览器不得从 chart/price points 重建指标；不满足条件时整组相对指标为 null，并返回 coverage、reliability、lineage fingerprint 与 method version。
- Performance 的一年期年化发布政策也属于后端计算权威：`PerformanceSummary.history_reliability` 返回 observed elapsed/calendar span、365 日阈值、eligibility、reason codes、sample label 与说明；不足一年时 API 本身将 annualized TWR、IRR / MWRR 与 Calmar 置为 `null`，浏览器仍按 eligibility fail closed 展示，不得解析日期、计算跨度或自行实施阈值。Benchmark comparison 按实际比较 boundary 至最后一个 aligned observation 生成独立的 `history_reliability`，不得借用组合全历史资格。
- Holdings 的 group / subtotal / total 行对 market value、cost basis、day change、unrealized P&L 等绝对量按组内明细加总；unrealized return 使用非现金 P&L 除以非现金成本，并在同一行包含现金时把现金 market value 纳入分母稀释，纯现金行不显示该比例。
- `GET /api/portfolios/{portfolio_id}/risk/workspace` 强制显式 `as_of_date`，并显式接收 rolling lookback、matrix lookback、taxonomy scope、matrix as-of 与 benchmark。它在同一数据库 Session 锁定所需 holding / universe / benchmark 的 canonical `total_return` 和 FX，返回 calculation lineage、adopted quote/FX dependencies、逐 instrument coverage 与 reason codes；缺少 series、非 complete observation、无可用 endpoint、缺失 FX、无当前权重或不完整对齐窗口都 fail closed，不跨 role、basis 或 provider 兜底。
- `Risk` 的 rolling volatility / Sharpe、相关性矩阵和 Current Drift 是当前静态权重口径：使用当前非现金持仓权重乘以资产自身 canonical 全历史收益窗口，现金残余隐含为 0 return；它不是 realized attribution，真实持仓期间复盘归入 Performance `Calculation`。daily / weekly / monthly 对齐、窗口覆盖、缺失收益政策、协方差估计、实际观察密度年化和 risk contribution 全部复用 Research / Production Risk Model 的同一数学内核；`sample_covariance` 才使用 `n - 1`，EWMA 与 shrinkage model 不被浏览器改写。长历史 rolling points 和 matrix as-of options 采用确定性输出上限，限制值写入 calculation lineage，最终所选日期仍使用完整窗口计算。
- `Risk` Correlation Matrix 由 Production Risk Model 的同一 covariance snapshot 归一化得到，因此 sample / EWMA / shrinkage 选择会一致地影响矩阵；页面不再维护独立的 sample-correlation 公式。All Instruments scope 使用 active universe，taxonomy scope 使用当前有风险敞口的 holdings；任一 active member 缺少完整 canonical 输入时整段矩阵 unavailable，不静默丢弃成员。
- 普通组合的 Risk Workspace 必须存在 active、planning-enabled、instrument-scoped default taxonomy；不会因组合持有 ETF、名称包含 rotation 或 instrument type 是 ETF 而豁免。Current Drift 把非现金 point-in-time holdings 与 deposit-account cash value 合成 planning NAV，并要求每个有敞口的 instrument / cash bucket 都有明确 assignment；未分配 exposure 会使 taxonomy risk contribution 和 drift unavailable。Holdings cash rows 只服务 Holdings 展示，不重复进入 Risk 分母。
- `Research` 的当前 target solve 从最末端 sleeve 递归向上求解；scope default 只使用该 scope 自身的默认目标维度，不静默切到另一个维度。多成员 scope 必须存在 active complete `SAA` 或 `TAA` target set；缺失目标、目标加总错误、strict missing-return policy 下出现单成员缺失、`complete_case_drop` 超过覆盖率/尾部新鲜度上限、risk-budget 求解不能满足 `1e-4` risk-share gap 阈值时，run 明确失败或标记 unavailable，不回退到目标权重、等权或旧算法。单成员 scope 只保留数学上唯一确定的 100% 权重，现金 risk budget 为 0。顶层 capital overlay 在风险 sleeve 权重求出后再按目标波动率、波动率上限或总敞口缩放，并把剩余权重放到系统 cash；top sleeve bounds 只作用于 root 直接 sleeve，违反上下限时 run 必须失败。Research backtest 使用同一 Production Risk Model 与 target solve 逻辑逐期重算，支持 1W / 1M / 3M rebalance；组合成员共同历史不足一个风险窗口时返回空 backtest warning，不生成越界日期或短窗口替代结果。每组回测 metrics 固定为 `research-backtest-metrics.v2.history-gated-arithmetic-sharpe`：不足 365 elapsed days 时 `annualized_return` 与 Calmar 为 `null`，期间收益、drawdown、年化波动率仍保留；Sharpe 使用同频收益算术均值年化除以年化波动率，不使用几何年化收益作分子。已完成 run 可以在不重跑 target solve 的情况下切换 benchmark 做共同期比较；`excess_return` 保持组合共同期收益减基准共同期收益，tracking error / information ratio 来自 active return 序列。
- `IRR / MWROR` 是资金效率补充指标；若数学上不可解，不应降低 TWR 口径的 coverage。
- 绩效方法参考 Portfolio Performance 的账本模型，并吸收 GIPS 的 TWR 优先、外部现金流政策、估值频率和方法一致性原则；本项目不声称 GIPS compliance，详见 [docs/02_GIPS_ALIGNMENT.md](./docs/02_GIPS_ALIGNMENT.md)。

## 读路径与性能约束

- daily snapshots、holding snapshots 和 contribution slices 是可重建读模型；核心 performance、holdings 和 contribution 读路径应优先复用物化结果。
- 物化读模型的 payload 或计算口径变化必须提升 `calculation_version` 并触发重建；不要在核心读路径长期保留旧 slice schema 的兼容分支。
- 交易、账户、共享行情或 FX 变化必须先写源事实，再通过 `refresh_request_id` 标记受影响组合 stale，并由后台刷新串行重建。
- portfolio 存在性校验应保持轻量，不应触发 workspace rollup、行情 profile 或全窗口 performance 重算。
- `Overview / Risk` 这类组合页面应避免同一窗口内重复拉取 shared instrument detail 或重复重建 performance 中间结果。
- 前端文案保持专业终端口径：loading 统一为 `Loading`，空态压缩为短句，字段/分组选项和 table view 不展示解释性备注。需要保留的诊断只限错误、校验失败和会影响用户判断的不可用状态。
