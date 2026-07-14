# Portfolio Operations Workbench Portfolio

这是 `Portfolio Operations Workbench / Portfolio` app 的工作区。

当前状态：

- `backend/` 与 `frontend/` 都已可运行
- 组合内核当前以 `portfolio / account` 与 stable transaction identity + append-only revision/group 为数据库主事实
- 交易与配置是可修订事实；持仓、lots、balances、估值和绩效是 Portfolio Daily sealed run 的不可变发布输出
- `Holdings / Accounts / Transactions / Performance` 已有真实 API 与页面支撑
  `Transactions` 当前支持 create / amend / tombstone delete、History、actor/reason 和 optimistic concurrency；
  `transaction_current` 只投影最新有效版本，内部转仓按同一 mutation 的原子配对事实管理
- `Risk` 已由后端权威 Risk Workspace 支撑，包含 current-static-weight rolling annualized volatility / Sharpe、与 Production Risk Model 一致的相关性矩阵、risk contribution 和 `Allocation Policy Drift`；浏览器只提交窗口、scope、matrix as-of 与 benchmark 参数并展示结果，不再实现收益、覆盖、协方差或风险贡献引擎
- `Taxonomies` 已有真实配置工作台，支持层级 sleeve tree、assignment、`TargetSet`、`default planning taxonomy` 与 `cash_bucket` 维护
- 当前 UI 的资产配置入口统一为 `Allocation Lab`，后端 bounded context 为 `Allocation Research`，不是通用策略研究。它支持基于 planning taxonomy / TargetSet / 当前持仓的递归 target-weight solve、top sleeve bounds、vol target/cap、1W/1M/3M `Policy Replay`、benchmark 动态比较、latest run 结果、风险预算求解诊断、回撤指标和配置缺口。新 run 只有成功完成后才替换上一轮 completed run；失败 run 会保留上一轮成功结果供继续查看。
- `Overview` 已作为组合默认首页，主图按 `Portfolio Value / TWR Index` 两种组合管理口径展示，回撤固定基于 TWR；估值、持仓、归因与分类金额必须绑定同一 `publication_id`，不用正在编辑的 live taxonomy 重分组已发布数字。`Snapshot` 不再作为独立工作面保留

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

- 只依赖共享 `instrument-core`、`calculation-core` contract 与同库 `instrument_registry / calculation_registry` schema，不通过 `platform` API 取数
- 不复用 `Watchlist` 的业务模型
- 维持 `portfolio / account / transaction / performance / risk / allocation_research` 的独立边界
- 除显式归类的 ETF 轮动策略组合外，`Taxonomy` 是普通组合分类、归因、风险聚合和跟踪的标准主轴；其中 planning-enabled taxonomy / `TargetSet` 是 Allocation Research 的 planning truth
- `Allocation Research` 消费 planning 结构求解配置权重，不反向创造另一套目标体系；`external_etf_rotation` 不适用 taxonomy planning、风险预算、TargetSet、Allocation Lab 或 Allocation Policy Drift，仅持有 ETF 的 `standard_taxonomy` 普通组合仍完整使用 taxonomy
- ETF Strategy Live 当前属于外部项目；本 app 不复制其信号、目标、风险预算或 drift 模型
- 运行时生成的 allocation research artifacts 属于本地工作产物，不作为源码的一部分

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
- 普通 DB/API 测试使用从已迁移、已 seed 的 session PostgreSQL template 逐例克隆出的隔离数据库；必须显式提供 `PORTFOLIO_OPS_TEST_POSTGRES_URL`，不会连接默认运行库
- Allocation Research 运行产物默认落在 `backend/allocation_research_outputs/`，用于本地查看和回放，已按运行时目录管理；当前产物以 request、context、target rows、member/leaf targets、solved result groups、solve event、scope solve events、target weight gaps 和 Policy Replay payload 为主
- backend 顶层包名现在是 `portfolio_app`
- 交易、账户和 Allocation Research 快照使用 canonical `instrument_id` / `instrument_name` / `instrument_type`；迁移会规范历史 JSON，运行时不再接受 `asset_id` / `asset_name` / `asset_type` 或 `allowed_asset_types`
- Portfolio migration `20260713_0036` 已把旧 mutable `transaction_record` 转为 stable identity、append-only
  revision/group 和只读 `transaction_current`。quantity/price 使用 `NUMERIC(38,12)`，金额/费用使用
  `NUMERIC(38,8)`，FX rate 使用 `NUMERIC(38,18)`；交易 API 以 plain-decimal string 保持精度。修改和删除
  保留完整 History，过期 revision 写入返回冲突，内部转账两腿原子创建和删除。0036 对旧 binary-float 尾差采用
  显式 round-half-even，并在 migration group 记录受影响字段和 transaction ids；新 API 仍拒绝任何隐式舍入。这是
  Portfolio Daily publication spine 之前的历史账本迁移；当前运行时已由 sealed manifest 绑定精确 revision 和计算依赖。
- Portfolio migration `20260713_0037` 在生产 PostgreSQL 再增加独立数据库防线：每条 revision 的
  canonical v1 payload hash 由数据库从落库 facts 重算核验；deferred constraint 在 commit 时验证内部转账
  必须一进一出、账户互惠、字段镜像、同组创建/删除、不可 amend 且历史 group 不复用。直接 SQL 也不能绕过。
- Portfolio migration `20260713_0038` 将 0036 基线组中泄漏的实现名 `actor_source=alembic` 精确归一化为
  领域值 `migration`，并在数据库与 service 同时封闭 actor source/type 配对；API 不保留旧值 alias 或展示兜底。
- Portfolio Daily 以 sealed manifest、Decimal kernel 和 lease/fencing worker 生成不可变 daily / holding / balance / lot / contribution outputs；单一 current publication 指针是 Overview、Holdings 与 Performance 的唯一权威读源。交易、账户、taxonomy、行情、FX 或方法依赖变化会在同一事实事务中递增 scope generation 并创建 durable recompute intent。GET 不 ensure、不计算、不写库；无 publication 返回 `calculation_not_ready`，新 generation 尚未发布时可返回旧 publication，但必须披露 stale/pending lineage。
- 已发布 Holding 行与同 run 的 snapshot、cash balance、lot 和 contribution 共享 `publication_id/run_id/manifest_id/fencing_token`；任何聚合都必须在该 publication 内闭合。book P&L coverage、fair-value NAV coverage 与 TWR reliability 独立披露，缺失输入不得被 live quote、旧 payload 或请求时重算兜底。

### 2. 前端

```bash
cd apps/portfolio/frontend
npm install
npm run dev
```

说明：

- Vite 默认仅监听 `127.0.0.1:5174`；如需远程访问，请通过受控的反向代理显式开放
- `/api` 已代理到 `http://127.0.0.1:8001`
- Holdings 的自定义 table view 存在后端 `portfolio_table_view_store` 中，按 `portfolio_id + view_scope` 隔离；浏览器 `localStorage` 只作为首次迁移和后端不可用时的本地 fallback，不在读取失败时回写覆盖后端配置。Performance 不保留已删除的 Calculation table contract。

## 重建本地数据库后

如果你刚执行过仓库根目录的 `./infra/postgres/rebuild_local_schemas.sh`：

- `portfolio` schema 只会保留 migration 定义的结构，不再自动注入默认 demo portfolio
- 历史导入的组合数据不会保留
- 需要组合时，显式通过前端 `/portfolios` 页面、`POST /api/portfolios`，或 [backend/scripts/import_real_portfolio_from_csv.py](./backend/scripts/import_real_portfolio_from_csv.py) 创建
  创建请求必须显式声明 `operating_profile`：一般组合使用 `standard_taxonomy`；由外部 ETF 轮动系统管理配置的组合使用 `external_etf_rotation`
  导入脚本要求显式传入 `--csv-path`、`--portfolio-id` 与 `--operating-profile`；`--portfolio-name` 不传时会回退到 `portfolio_id`
  脚本只提交组合、账户和交易事实；NAV、日变动与持仓数由 durable invalidation 触发 worker 后写入唯一的 Portfolio Daily 精确发布，导入事务内不再临时估值。
- 新建 `securities_account` 时，如果不显式选择成本法，系统默认使用 `FIFO`
- 账户成本法支持 `FIFO` 与 `moving_average`；修改成本法会使 Portfolio Daily publication 失效，并由 worker 按交易事实重算 holdings、lots、balances 和 performance outputs

## 常用校验命令

```bash
(cd apps/portfolio/backend && pytest)
(cd apps/portfolio/backend && pytest tests/test_taxonomies_api.py tests/test_allocation_research_api.py tests/test_phase4_allocation_research_boundary.py -q)
npm --prefix apps/portfolio/frontend run build
```

## 计算层当前口径

- 组合级 TWR 使用 valuation-subperiod time-weighted 口径：外部流入进分母，外部流出加回分子，区间结果几何复合；`deposit / withdrawal` 统一以 cash value / `settlement_date` 进入 NAV、TWR、XIRR、contribution、calendar bucket 和 exact bridge，较早的录入 `trade_date` 不提前形成组合资本或 pending NAV；缺少 value date 的历史输入 fail closed，不回退到 trade date。本系统没有 intraday valuation，因此不声称具有日内时点精度。
- TWR 可靠性独立于 book P&L coverage：正常 calendar carry 与 resolver 明示 stale 分开披露；任何 carried / stale / incomplete valuation 遇到外部现金流都会形成不可跨越的 return boundary，fresh valuation 只负责 re-anchor。跨断点的 return、drawdown 与 contribution fail closed，不把不可用收益或归因伪装成 0。
- FIFO / moving average 只影响 book cost、book realized gain、book unrealized P&L 和 lot 展示；不影响 fair-value based TWR。
- Performance 通过一次 `/performance/report` 读取同一个 fenced publication，展示 method-precision TWR/XIRR/drawdown、portfolio exact bridge、Frongello-linked contribution、monthly/weekly calendar 与 daily audit trail。Risk 的 covariance、risk budget 和 forward risk contribution 不混入 Performance。
- `Holdings` 中的 `Market Value Base` 是 base-currency fair value；现金行使用 settled cash 的 base value。非现金 Day return/change 只使用 canonical `total_return` role 的同一 locked series 前后 complete 点，valuation role 只用于 market value；任一点缺失或状态异常时 return 字段为空，不能用 valuation/chart 补齐。价格/NAV 图表和 sparkline 只使用独立的 canonical `chart` role，不能反向进入 return、volatility、drawdown 或 risk-frequency 推断。Holdings / Taxonomy 对 instrument set 在同一 Session 批量锁定 chart 与 total-return 窗口；partial/rejected/withdrawn 或 late 只会使对应角色 fail closed，不跨角色或跨 basis 兜底。非 base cash 使用 FX 变动。Instrument return / volatility / drawdown 基于未下采样的 total-return series 和 resolved risk frequency；volatility 有窗口起点覆盖和最小样本门槛。Holdings group rows 的 return 使用当前 base-value 权重，volatility / drawdown 基于组 return series 计算，不使用成员风险指标的加权平均。Holdings 主表的 locked `instrument` 首列在横向滚动时保持冻结。
- `moving_average` 在底层按 `account + instrument` 维护一个 rolling average cost bucket；API 为 UI 和转仓审计输出一个 synthetic position lot。
- `Overview` 与 `Performance` 的 TWR index、daily series 和 drawdown 均由同一 published reporting engine 按查询窗口重建；不得复用 inception-to-date 累计值，也不得在浏览器计算。
- Performance 的一年期年化政策属于后端计算权威：少于 365 effective elapsed days 时 annualized TWR 为 null；XIRR 若数学上唯一可解仍保留，但标记为短区间 supplemental、不得作为 annualized headline。浏览器不得解析日期后自行实施阈值。
- Holdings 的 group / subtotal / total 行对 market value、cost basis、day change、unrealized P&L 等绝对量按组内明细加总；unrealized return 使用非现金 P&L 除以非现金成本，并在同一行包含现金时把现金 market value 纳入分母稀释，纯现金行不显示该比例。
- `GET /api/portfolios/{portfolio_id}/risk/workspace` 强制显式 `as_of_date`，并显式接收 rolling lookback、matrix lookback、taxonomy scope、matrix as-of 与 benchmark。它在同一数据库 Session 锁定所需 holding / universe / benchmark 的 canonical `total_return` 和 FX，返回 calculation lineage、adopted quote/FX dependencies、逐 instrument coverage 与 reason codes；缺少 series、非 complete observation、无可用 endpoint、缺失 FX、无当前权重或不完整对齐窗口都 fail closed，不跨 role、basis 或 provider 兜底。
- `Risk` 的 rolling volatility / Sharpe、相关性矩阵和 Allocation Policy Drift 是当前静态权重口径：使用当前非现金持仓权重乘以资产自身 canonical 全历史收益窗口，现金残余隐含为 0 return；它不是 realized attribution，真实持仓期间复盘归入 Performance 的 published contribution 和 exact monetary bridge。daily / weekly / monthly 对齐、窗口覆盖、缺失收益政策、协方差估计、实际观察密度年化和 risk contribution 由 `portfolio_market_data.py`、`risk_model.py` 与 `risk_math.py` 形成的 Production Risk Model 负责；Risk Workspace 不依赖 Allocation Research 或 Policy Replay 实现。`sample_covariance` 才使用 `n - 1`，EWMA 与 shrinkage model 不被浏览器改写。长历史 rolling points 和 matrix as-of options 采用确定性输出上限，限制值写入 calculation lineage，最终所选日期仍使用完整窗口计算。
- `Risk` Correlation Matrix 由 Production Risk Model 的同一 covariance snapshot 归一化得到，因此 sample / EWMA / shrinkage 选择会一致地影响矩阵；页面不再维护独立的 sample-correlation 公式。All Instruments scope 使用 active universe，taxonomy scope 使用当前有风险敞口的 holdings；任一 active member 缺少完整 canonical 输入时整段矩阵 unavailable，不静默丢弃成员。
- `standard_taxonomy` 组合的 Risk Workspace 必须存在 active、planning-enabled、instrument-scoped default taxonomy；不会因组合持有 ETF、名称包含 rotation 或 instrument type 是 ETF 而豁免。Allocation Policy Drift 把非现金 point-in-time holdings 与 deposit-account cash value 合成 planning NAV，并要求每个有敞口的 instrument / cash bucket 都有明确 assignment；未分配 exposure 会使 taxonomy risk contribution 和 drift unavailable。`external_etf_rotation` 仍保留账本、持仓、绩效、rolling risk、相关性和 instrument-level risk contribution，只将 taxonomy planning 与 Allocation Policy Drift 标记为不适用。Holdings cash rows 只服务 Holdings 展示，不重复进入 Risk 分母。
- `Allocation Research` 的当前 target solve 从最末端 sleeve 递归向上求解；scope default 只使用该 scope 自身的默认目标维度，不静默切到另一个维度。多成员 scope 必须存在 active complete `SAA` 或 `TAA` target set；缺失目标、目标加总错误、strict missing-return policy 下出现单成员缺失、`complete_case_drop` 超过覆盖率/尾部新鲜度上限、risk-budget 求解不能满足 `1e-4` risk-share gap 阈值时，run 明确失败或标记 unavailable，不回退到目标权重、等权或旧算法。单成员 scope 只保留数学上唯一确定的 100% 权重，现金 risk budget 为 0。顶层 capital overlay 在风险 sleeve 权重求出后再按目标波动率、波动率上限或总敞口缩放，并把剩余权重放到系统 cash；top sleeve bounds 只作用于 root 直接 sleeve，违反上下限时 run 必须失败。`Policy Replay` 使用同一 Production Risk Model 与 target solve 逻辑逐期重算，支持 1W / 1M / 3M rebalance；组合成员共同历史不足一个风险窗口时返回空 warning，不生成越界日期或短窗口替代结果。每组模拟 metrics 固定为 `allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe`：不足 365 elapsed days 时 `annualized_return` 与 Calmar 为 `null`，期间收益、drawdown、年化波动率仍保留；Sharpe 使用同频收益算术均值年化除以年化波动率，不使用几何年化收益作分子。已完成 run 可以在不重跑 target solve 的情况下切换 benchmark 做共同期比较；`excess_return` 保持组合共同期收益减基准共同期收益，tracking error / information ratio 来自 active return 序列。
- `XIRR / MWRR` 是资金效率补充指标；若数学上不可解，不应降低 TWR 口径的 coverage。
- 绩效方法参考 Portfolio Performance 的账本模型，并吸收 GIPS 的 TWR 优先、外部现金流政策、估值频率和方法一致性原则；本项目不声称 GIPS compliance，详见 [docs/02_GIPS_ALIGNMENT.md](./docs/02_GIPS_ALIGNMENT.md)。

## 读路径与性能约束

- 核心 performance、holdings 和 contribution GET 只能读取同一 immutable current publication；不得在请求中调用 builder、ensure、后台任务或 live ledger/quote fallback。
- output schema 或计算口径变化必须提升明确版本并生成新 run；历史 output 保留取证，不能翻译为新 schema 或原地覆盖。
- 交易、账户、taxonomy、共享行情或 FX 变化必须通过 PostgreSQL trigger 在同一事实事务中写 generation/recompute intent；当前 runtime 不保留旧请求标识、HTTP Portfolio refresh 或读路径 repair 双轨。
- 数值精度按业务用途分层：会计事实和金额字段有固定 scale，非终止运算与财富链使用 Decimal50/HALF_EVEN method value，对外收益、权重和归因使用 18 位 published value。方法舍入必须显式留存 evidence，禁止以随历史长度增长的无界小数 TEXT 前缀伪装“更精确”。
- portfolio 存在性校验应保持轻量，不应触发 workspace rollup、行情 profile 或全窗口 performance 重算。
- `Overview / Risk` 这类组合页面应避免同一窗口内重复拉取 shared instrument detail 或重复重建 performance 中间结果。
- 前端文案保持专业终端口径：loading 统一为 `Loading`，空态压缩为短句，字段/分组选项和 table view 不展示解释性备注。需要保留的诊断只限错误、校验失败和会影响用户判断的不可用状态。
