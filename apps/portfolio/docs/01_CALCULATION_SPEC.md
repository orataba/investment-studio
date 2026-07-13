# PMS 正式版计算口径规格

> Scope note: 本文中的 `Research` 专指 `Allocation Research / Allocation Lab`。planning
> taxonomy 仍是普通组合分类、归因和跟踪的标准主轴；其 planning 扩展、TargetSet、风险预算、
> target solve 与 policy drift 是资产配置领域概念，不能作为显式 ETF 轮动策略组合的通用接口；
> 仅持有 ETF 的普通组合仍使用 taxonomy。
> 文中的 research backtest 后续统一更名为
> `Policy Replay`；在完成一次性不兼容命名重构前，现有代码名暂时保留。

关联文档：

- [`02_GIPS_ALIGNMENT.md`](./02_GIPS_ALIGNMENT.md)

## 1. 文档目标

这份文档用于锁定正式版 PMS 的核心计算口径。

目标是确保：

- 同一个指标在 CLI / API / UI / export 中口径一致；
- 账本、绩效、风控、风险预算之间的分母和权重语义不冲突；
- 后续实现不会因为“看起来差不多”的公式导致结果漂移。

除非某个接口明确声明不同口径，否则本文档中的定义视为默认 canonical 口径。

### 1.1 参考继承原则

这份规格中的默认继承关系如下：

- **PP 负责**：
  - TWR
  - IRR / MWROR
  - Statement of Assets 风格的 purchase value / cost basis
  - open / closed trades 的 lot matching 逻辑
- **本项目扩展负责**：
  - benchmark-relative analytics
  - risk system
  - target risk budget gap
  - scenario P&L
- **GIPS-informed 方法治理负责**：
  - TWR 优先于 MWR 作为默认绩效呈现口径
  - external cash flow policy 的稳定定义
  - 外部现金流日期估值、子期间收益几何链接和方法一致性
  - coverage / stale / unavailable 边界披露

换句话说：

- 涉及 `持仓、成本、交易匹配、收益率` 的 canonical 口径，尽量先向 PP 靠拢；
- 涉及 `风控、风险预算、buy-side period analysis` 的部分，再由本项目扩展。
- 涉及绩效呈现政策、外部现金流治理和方法一致性时，采用 GIPS-informed 原则，但不声称本项目或任意组合 GIPS compliant。

### 1.2 事实底座与派生原则

正式版计算层按三类输入理解：

#### 事实输入

- `Transactions`
- `Quotes`
- `FX`
- `Benchmark series`
- `Corporate Actions / Events`

#### 必要配置输入

- `Portfolio / Account / Instrument metadata`
- `Benchmark definition / portfolio benchmark assignment`
- `Portfolio taxonomies / target sets / alert-rule config`

#### 派生结果

- `Positions`
- `Lots`
- `Trades`
- `Snapshots`
- `Performance`
- `Risk`
- `Research`

规则：

- `Transactions` 是原始账本；
- `Trades` 是 lot matching 后的分析视图；
- `Quotes`、`FX`、`benchmark series` 是共享市场数据，不属于单个组合私有；
- `Snapshots` 是由 Analytics 从事实层生成的派生产物；
- 页面展示不得绕过这些输入直接手填结果字段。

### 1.2.1 Transaction 版本化事实契约

Portfolio migration `20260713_0036` 已把交易事实从 mutable current-state row 切换为稳定身份、
append-only revision 和只读 current projection。canonical 契约如下：

- `transaction_identity_record` 持有稳定 `transaction_id + portfolio_id`。同一业务交易的 amend/delete
  不创建新身份，已删除身份也不会释放或复用。
- `transaction_revision_group_record` 是一次业务 mutation 的原子边界。它保存 `source_kind`、
  `change_reason`、actor type/id/display/source、`recorded_at`，以及可选 request/idempotency/source ref；
  一个 group 可以包含多笔 revision，但同一 transaction 在一个 group 中最多变更一次。
- `transaction_revision_record` 只追加 `baseline / create / amend / delete`。revision number 从 1 连续递增，
  amend/delete 必须声明紧邻的 predecessor；identity、group 和 revision 行都禁止原地 update/delete。
- 每个非 tombstone revision 保存 canonical payload hash。相同 payload 的 amend 被视为 no-op 并拒绝，
  不能通过生成无意义版本伪造修改历史。
- `transaction_current` 是只读数据库 view：每个 identity 只投影最新且非 tombstone 的 revision，并携带
  当前 revision/group、actor、reason 和 payload hash。Positions、lots、ledger postings、snapshots、
  performance 与 risk 的当前态计算只读取该投影，不扫描旧 revision 叠加经济事实。

交易数值在数据库与 API 边界保持 Decimal，不以 binary float 作为账本事实：

| 字段 | 数据库类型 | API quantum |
|---|---|---|
| `quantity`, `price` | `NUMERIC(38,12)` | `0.000000000001` |
| `gross_amount`, `counter_amount`, `fees`, `taxes` | `NUMERIC(38,8)` | `0.00000001` |
| `fx_rate` | `NUMERIC(38,18)` | `0.000000000000000001` |

HTTP create/update/internal-transfer 请求必须用 plain-decimal JSON string 传入这些字段；JSON number、科学计数法、
非有限值或超过相应 scale 而需要舍入的值都拒绝。响应和 History snapshot 同样序列化为普通十进制字符串。
计算服务可以在明确的数值计算边界转换类型，但不得把转换后的 float 回写为交易事实。

审计与并发规则：

- 每次写入都必须形成带 actor 和非空 reason 的 revision group。当前人工 API 要求
  `actor_type=user`、`actor_source=client_asserted`；create/internal-transfer 未填写 reason 时使用明确的初始记录原因，
  amend/delete 必须由调用方填写至少 3 个字符的原因。service/migration actor 只允许可信内部路径使用。
- amend/delete 必须同时提交读取时的 `expected_revision_id` 与 `expected_revision_number`。任一不匹配即返回
  `409 transaction_revision_conflict`，并提供 actual revision；调用方必须重新读取 current/history、人工复核后重试，
  不能 last-write-wins。事实未改变的 amend 返回 `422 transaction_revision_no_op`。
- delete 追加 canonical tombstone revision，tombstone 不带经济事实 payload；`transaction_current` 随即不再投影该身份，
  但 identity、此前所有 revision、actor、reason 和 deletion revision 永久保留。tombstone 后不能恢复或继续 amend。
- internal transfer 的 `transfer_out` 与 `transfer_in` 共用 `transfer_group_id`，在同一个 revision group 内原子创建；
  不能单腿 edit。删除任一腿时必须把完整 pair 在同一 mutation 中追加 delete revisions，不能留下半笔转账。
- PostgreSQL migration `20260713_0037` 把上述两项从 service 约定提升为数据库提交约束：数据库从落库 facts
  按 canonical v1 规则重算并核验 payload hash；deferred transfer constraint 在 commit 检查一进一出、账户互惠、
  镜像字段、同组创建/删除、禁止 amend 和历史 group 不复用。SQLite 仍是本地/测试后端，由同一 service batch
  guard 执行这些跨行规则，不被表述为生产账本的独立写入权威。
- migration `20260713_0038` 规定 actor source 只有 `client_asserted / authenticated_principal /
  trusted_service / migration` 四种，并与 `user / service / migration` actor type 严格配对。0036 基线组的
  `alembic` 实现名只在迁移内按完整来源指纹改正为 `migration`；运行时、API 和视图均不接受或映射旧值。

`20260713_0036` 将旧 `transaction_record` 的每一行捕获为 revision 1 `baseline`，按 portfolio 建立 migration
group，验证数量、链元数据和 payload hash 后删除旧表并创建 `transaction_current`。迁移只会把旧 API 自动填入、
且恰好等于 `trade_date` 的不适用 entitlement/acquisition date 归一化为 `NULL`。旧列是 binary float 时，迁移会
显式使用 round-half-even 归一化到上表声明的 Decimal scale，并把每个受影响字段的数量和 transaction ids 写入
migration group `source_ref`；这项历史迁移规则不放宽新 API 的“禁止隐式舍入”边界。其他不安全差异 fail closed。
该迁移有意不可逆，恢复必须依赖迁移前备份，不能把 revision/tombstone 压回 mutable row。

0036 已保证交易修订后把相关组合计算状态标记为 stale；精确记录每次 valuation/performance/risk 采用哪些
transaction revision 的通用 calculation input manifest，以及 reconciliation run，仍是下一阶段，不能从当前
dirty boundary 或 snapshot fingerprint 推断为已实现。

### 1.3 正确性与失败策略

计算层不保留为了“让结果看起来可用”的兼容分支。任何 canonical 指标必须满足其输入、配置、覆盖率和数学条件；条件不满足时，结果应显式进入 `partial`、`unavailable`、`comparator missing`、`insufficient-history` 或失败状态。

严格规则：

- 不用目标权重、等权、历史旧算法或另一维度 target 替代 risk-budget 求解结果；
- 不用 stale price、跨 period forward fill 或不同长度持有期收益补齐 covariance、correlation、Sharpe、target-volatility overlay 或 risk contribution；
- 不在 `SAA` / `TAA`、`weight` / `risk_budget`、benchmark / target / alert rule 之间静默互相替代；
- 单成员 scope 允许输出唯一确定的 100% 权重；现金成员在 covariance-based risk budget 中允许 `target_risk_share = 0`。除此之外，未配置目标不是默认等权目标；
- UI、API、export 必须展示结果状态与 coverage / solver 诊断，不能把失败条件包装成正常结果。

## 2. 总体约定

### 2.1 估值时点

正式版默认使用 **end-of-day valuation**：

- 当日价格、FX、benchmark level 都视为当日收盘或该日最终可用估值；
- 当日 `MVB` 等于上一估值日的 `MVE`；
- 当日 `MVE` 为当日收盘后的组合总市值与现金合计。
- 物化 daily snapshot 必须同时保存 `beginning_nav` 与 `ending_nav`；任意用户查询区间 `[start_date, end_date]` 的 `initial value` 使用 `start_date` 当天的 `beginning_nav`，语义等同于上一估值日 EOD，`final value` 使用 `end_date` 的 `ending_nav`。

### 2.1.1 全球 EOD 规则

全球多资产组合的 daily snapshot 采用以下 canonical 规则：

- 每个 `Portfolio` 必须定义 `valuation_timezone` 和 `valuation_cutoff_policy`；
- 组合日度结果按 `as_of_date` 归档，`as_of_date` 表示**市场日**，不是实际计算发生的墙上时间；
- 单个资产优先使用其本地市场在该 `as_of_date` 的最新官方收盘价或该日最终可用估值；
- 组合绝对口径快照只有在该 `as_of_date` 所需市场和 FX 数据满足覆盖率阈值后，才能标记为 `complete`；
- benchmark 相关区块的 `complete / partial / unavailable` 由 benchmark coverage 单独决定，不反向阻塞绝对口径 snapshot；
- 组合 summary、Overview 和未显式指定日期的 Holdings 默认展示 latest fresh complete `as_of_date`，而不是当前本地时钟下尚未收齐数据的“今天”。fresh complete 只读取 `nav_coverage_state = complete`、`nav` 存在且 `stale_price_flag = false`；book P&L coverage 与 TWR reliability 不参与日期选择。它要求当前持仓资产价格/NAV 都没有 stale carry-forward。`stale_fx_flag` 是独立质量标记，不单独把资产新鲜度日期向前推，也不能作为资产新鲜度兜底。

示例：

- 上海团队在 `2026-04-15` 早晨拿到美国市场 `2026-04-14` 的收盘数据后，该结果归属于 `as_of_date = 2026-04-14`，而不是 `2026-04-15`。

### 2.1.2 FX 与 benchmark 对齐规则

在同一个 `as_of_date` 的快照中，若需要计算 benchmark-relative 结果：

- FX 必须使用同一 `source_ref` / cut 的 EOD 数据；
- benchmark 必须对齐到同一个 `as_of_date`；
- 系统不得把 `T` 日股票收盘与 `T+1` 日 FX 或 benchmark 静默混用。

### 2.1.3 Valuation Basis vs Total-Return Basis

共享层允许同一资产同时维护多种 quote basis，例如：

- `fund`: `official_nav` 与 `total_return_nav`
- `equity`: `close` 与 `adjusted_close`

这里必须区分两种用途：

- `valuation` role：服务组合 statement、持仓市值、NAV、ledger-driven performance
- `total_return` role：只服务 return-labelled 指标、波动率、回撤、相关性、协方差、risk contribution 与 calculation-frequency 推断
- `chart` role：只服务价格/NAV 图表与 sparkline 展示，不得进入任何收益或风险计算

规范如下：

- 组合账面估值不得静默切到 total-return basis；若分红或派息已作为交易/现金流入账，再用复权价会造成双算；
- fund 的 return/risk 序列只允许 policy 选中的 canonical `total_return_nav` 等真实 total-return basis；缺失时必须 unavailable，不得改用 `official_nav`；
- equity 的 return/risk 序列只允许 policy 选中的 canonical `adjusted_close` 等真实 total-return basis；缺失时必须 unavailable，不得改用 `close`；
- total-return 的当前点与前一点必须来自同一个 locked `quote_series_id`、相同 basis、币种和 complete revision；preferred series 历史不足、当前 revision 为 partial/rejected/withdrawn、或任一点缺失时，return-labelled 字段必须为空，不能从低优先级 series、valuation 或 chart 拼接；
- chart / sparkline 必须只解析 policy 的 canonical `chart` role 并展示实际采用的 quote basis；不能优先读取 total-return、valuation、reference，也不能在 chart series 失败后扫描其他 basis；
- Holdings、Taxonomy 和 instrument chart API 对同一 instrument set 在一个数据库 Session 内锁定所需角色窗口，再批量派生各区间图表和 risk basis。窗口内出现任何 current partial/rejected/withdrawn revision，或期末超过显式 freshness 上限时，对应角色整段 fail closed；另一个角色只有在自身窗口完整时才可继续使用；
- calculation-frequency 只根据 fully usable canonical `total_return` observation dates 推断。任一参与风险计算的 instrument 不存在、少于两个观察或窗口不完整时，`risk_basis.coverage_status = incomplete` 并列出 instrument 与 reason，不得由 chart/valuation/reference 或默认 daily 隐去缺口；
- 图表下采样只发生在 canonical chart window 已锁定之后；收益、波动率和回撤始终使用未下采样的 canonical total-return points。历史长度只能影响返回行数，不能使 SQL 查询次数按 calendar day 线性增长。

Portfolio valuation consumer 对 carry-forward 采用按 canonical `instrument_type` 分组的显式时效政策：

- `fund` 的官方 NAV 允许最多 45 calendar days，以覆盖月频披露；
- `equity / etf / index / bond / fx / cash / other` 归入 daily-market policy，最多允许 5 calendar days；
- policy type、consumer policy version、canonical instrument type 与 resolver dependency 的 `max_age_days` 必须随 valuation quote 返回；
- 类型只读取 canonical instrument record，不根据 ticker、taxonomy、持仓用途或行情形状推断；
- 超过上限的观察保留 series / observation / revision lineage，但估值 value、market value 与 NAV 必须 fail closed。长假超过 5 天而系统又没有交易日历时宁可 unavailable，不得把缺数静默解释为休市。

### 2.2 组合基准货币

每个 `Portfolio` 必须有 `base_currency`。

所有组合级指标默认以 `base_currency` 表达，包括：

- NAV
- market value
- P&L
- benchmark-relative return
- scenario P&L

### 2.3 正负号约定

默认采用“从组合视角”记账：

- 买入证券：现金减少，证券头寸增加；
- 卖出证券：现金增加，证券头寸减少；
- `deposit`：外部资金流入组合；
- `withdrawal`：外部资金流出组合；
- 收费 / 税费：组合价值下降；
- `market_value`、`NAV`、`cash balance` 默认正值展示；
- 损失类结果如 `drawdown`、`scenario_return` 可以为负。

对 `Transaction` 事实层的输入约定：

- `quantity`、`gross_amount`、`fees`、`taxes` 在存储层默认使用非负 magnitude；
- 方向由 `transaction_type` 与派生 `LedgerPosting` 的 posting role 决定，而不是在输入 payload 上混用正负号表达买卖或流入流出；
- 导入器、手工录入与 API 都必须遵守同一套 magnitude contract。

### 2.4 外部现金流与内部现金流

绩效口径中必须区分：

- **External cash flow**
- **Internal portfolio flow**

默认分类如下：

#### External cash flows

- deposit
- withdrawal
- 组合对投资人真实分配的现金

#### Internal portfolio flows

- buy / sell
- account-to-account transfer（同一 portfolio 内，使用 `transfer_in / transfer_out + transfer_group_id`）
- dividend / coupon 由持仓证券支付到组合现金账户
- maturity_redemption / principal return 由发行人支付到组合现金账户
- fee / tax 作为组合内部费用损益

说明：

- 证券分红、票息对组合来说是收益，不是 external flow。
- 同一 portfolio 内部不同账户之间的现金划转，不改变组合级绩效。
- `deposit` / `withdrawal` 在首版保留给组合边界现金流，不再与内部转账混用。
- `transfer_in` / `transfer_out` 在首版只表示 `internal_portfolio` scope 的成组内部迁移，必须通过 `transfer_group_id` 关联，并显式携带 `transfer_object_type = cash | position`。
- 对证券类交易，`account_id` 表示 `securities_account`；现金腿优先取 `settlement_cash_account_id`，若为空则回退到账户的 `default_settlement_cash_account_id`。
- `deposit` / `withdrawal` 必须记入 `deposit_account`；`buy` / `sell` / `dividend` / `coupon` / `maturity_redemption` 默认记入 `securities_account`。
- 若 `transfer_object_type = cash`，只更新 `deposit_account` 现金账本；若 `transfer_object_type = position`，只更新 `securities_account` 持仓账本。
- account-level cash / position ledger 必须通过 `Transaction -> LedgerPosting` 的确定性展开生成；`deposit_account` 账本是派生视图，不要求用户为同一结算再录入第二条现金交易。
- 证券现金腿的 settled cash 进入账户账本的业务日期是 `LedgerPosting.effective_date = settlement_date`；证券头寸 posting 的 `effective_date = trade_date`。
- `deposit` / `withdrawal` 的组合资本边界固定为 cash value date，即 `settlement_date`。该日期缺失时必须 fail closed，不能回退到 `trade_date`。在 value date 之前，该 external flow 不进入 settled cash、pending settlement、NAV、TWR、IRR、contribution、calendar bucket 或 period bridge；原始 `trade_date` 只作为指令/录入日期保留。
- 外部资本流的输入边界同样 fail closed：HTTP create / update 请求必须显式提交非空 `settlement_date`；批量写入的每条事实和导入流程也必须显式提供该字段。普通证券交易与内部转账继续遵循各自的成交、结算规则。
- 上述规则只约束组合边界 external flow。证券交易仍在 `trade_date` 确认头寸，并在结算前通过 pending receivable/payable 留在 NAV 中。
- `opening_balance` 是 bootstrap event，不属于正常运行期的 external / internal recurring flow。

### 2.4.1 opening_balance 处理

- `opening_balance` 只允许出现在组合导入起点或 inception 边界；
- 在 TWR 与 `Delta` 口径中，`opening_balance` 用于建立期初 `MVB` / 起始持仓，不作为区间内 external flow 重复计入；
- 在 IRR / MWROR 中，若测量窗口从导入起点开始，`opening_balance` 视为期初初始投入；若测量窗口开始于其后日期，则它落在窗口外，不再重复记为现金流。
- `opening_balance + position` 必须进一步 materialize 成 opening lots；`gross_amount` 解释为 imported remaining cost basis，而不是 bootstrap 日市值。
- `opening_balance` 的 `trade_date` / `settlement_date` 表示导入边界；历史 lot 的 acquisition date 必须落在 opening lots 上，而不是伪造到 bootstrap transaction 的成交日期里。
- 若导入源提供历史 lots，则必须逐 lot 保留 `acquisition_date` 与 remaining cost basis；若只能提供聚合头寸，则只能生成单个 `synthetic_opening` lot，并明确其后的 FIFO / realized P&L 解释从该 synthetic lot 起算。

### 2.5 权重口径

正式版默认只保留一套显式权重：

- `portfolio_weight = market_value / total_nav`

其中：

- `total_nav` 包含现金及其他组合资产净额；
- 排除现金后的归一化若需要使用，必须在具体分析指标处单独说明，不能默认复用为另一套通用 `weight` 字段。
- 若前端要展示排除现金或 invested-only 视角，必须显式命名为 `invested_only_weight` / `ex_cash_weight` 一类分析字段，而不是继续叫 `weight`。

### 2.6 年化约定

默认采用以下年化约定：

- Return / IRR：`ACT/365.25`
- Daily volatility / tracking error：优先使用实际有效收益观察密度推断 `periods_per_year`
- Weekly volatility：`52`
- Monthly volatility：`12`

若观察频率是稳定交易日频，`periods_per_year` 通常接近 `252`；若存在节假日、缺价或非交易日 carry-forward，系统必须记录并使用实际有效收益观察密度，避免把无市场观察的 0 return 当作风险样本。

协方差 / 相关性计算必须先构造 active return matrix。默认 `strict` missing-return policy 要求矩阵中的每个收益日期对所有 active series 都完整；任何单成员缺失都进入 coverage / missing 诊断，不得被补成 0 return，也不得用 pairwise dates 拼出一个每个 entry 样本不同的 covariance matrix。`sample_covariance` 使用完整对齐样本的样本协方差估计量，分母为 `n - 1`，且至少需要两个完整 return observations；不得用总体协方差 `n` 分母作为普通样本风险估计。年化必须使用完整样本 index 的实际有效收益观察密度。Research 的 `ewma_vol_shrinkage_corr_covariance` 也必须在 missing-return policy 处理后的完整窗口上估计 EWMA volatility 与 shrinkage correlation。

### 2.6.1 计算频率与节假日

Holdings / Risk / Research 中所有 forward-looking covariance / risk contribution 计算共享组合级 `Production Risk Model`。该模型存储在 `portfolio_record.risk_policy_json`，包括 covariance model、lookback days、calculation frequency、missing-return policy 和 contribution mode。Production risk window 只允许 `1M / 3M / 6M / 12M / 24M`，分别映射到 `30 / 90 / 180 / 366 / 730` days；不接受任意天数窗口，也不为 legacy 任意 lookback 做静默兼容。它是 forward RC、风险预算偏离、risk-budget solve 和 target-volatility overlay 的唯一生产风险模型来源；Research settings 中保留的 lookback/frequency 字段只作为 run setup 表单输入，不得成为第二套风险模型来源。Performance 中基于实际历史组合路径的 realized attribution 仍然独立保留，不和 forward RC 混用。

Risk 与 Research 的 covariance / correlation / risk contribution 必须先确定一个 target calculation frequency，再把各资产 NAV/price series 对齐到该频率：

- `auto` 规则：全部资产可判定为日频时使用 `daily`；日频和周频混合时使用 `weekly`；存在月频资产时使用 `monthly`。样本很短且间隔不规则时，不得把缺失观测误判为周频或月频，除非所有间隔都一致落在对应频率区间。
- Research 允许用户通过组合级 `Production Risk Model` 显式选择 `daily` / `weekly` / `monthly`，但不能选择高于数据支持的频率。例如日频+周频混合不能强制按日频计算。
- 对齐规则：每个资产在目标 period 内只取最后一个有效观测点；不得跨目标 period 前向填充生成假 NAV。若某资产缺少某个目标 period，该资产该 period 的 return 为 missing。
- 节假日规则：若标准资产在某个交易所共同节假日都没有更新，则该日期不会进入共同收益样本；若只有单个资产缺失，而其他资产在该目标 period 有观测，则这是该资产的缺失数据，不应被当作 0 return 或 stale return。
- Missing-return policy：默认 `strict`，任何 active member 在目标 period 缺失都使该风险/研究样本不可解。Research 可以由用户显式选择 `complete_case_drop`，但只能删除含缺失成员的整行，并受缺失行比例 `10%`、latest complete row 尾部新鲜度上限（日频 `5` 天、周频 `14` 天、月频 `62` 天）和最小完整观测数约束；结果必须暴露 rows before / after、dropped rows、latest complete date 与 trailing staleness。
- 周频 period end 使用自然周五；若 as-of date 落在周中，则最后一个未完整周以 as-of date 作为 capped period end。月频使用自然月末，同样以 as-of date cap 最后一个 period。
- 若未来传入显式交易日日历，日频对齐应以日历校验 holiday vs missing：共同非交易日不生成样本；日历交易日缺价必须进入 coverage / missing 诊断，而不是隐式填值。
- Portfolio Risk 的 production `risk_basis` 由后端 Risk Workspace 在自己的 canonical `total_return` + FX Session lock 中解析，不消费 Holdings 浏览器 payload。它必须覆盖全部参与风险计算的非现金持仓，并且 `resolved_frequency` 只能是 `daily` / `weekly` / `monthly`；缺失、非法或来源频率不完整时，Risk section 进入 unavailable，不得从 Holdings profile、calculation groups 或默认 daily 兜底。Holdings response 中同名 `risk_basis` 仅服务 Holdings 自身展示。
- Holdings `Forward RC` 和 Risk 页 Current Drift 是当前权重口径：用当前持仓权重与资产自身历史收益窗口估计当前组合风险，语义上等同于“当前组合如果在历史窗口内一直以当前权重持有”。因此这些指标不得被 portfolio inception、holding start date 或 materialized contribution slices 截断。真实成立以来/真实持仓期间的 realized attribution 留在 Performance `Calculation`。Risk 页不再提供单独的 point-in-time Risk Contribution 表；风险预算偏离只在 Current Drift 中展示。
- Risk 页 Rolling Risk 使用所选 rolling lookback，但 covariance model、calculation frequency、missing-return policy 和年化约定来自组合级 `Production Risk Model` / Research 数学内核；浏览器不提供另一套 sample/EWMA/shrinkage 公式。凡是用于 risk-budget drift、rebalance trigger、Research solve 或 Holdings Forward RC 的 RC 相关指标继续使用同一生产模型，不能在页面内硬编码 decay、shrinkage、lookback 或 contribution mode。
- Risk 页 Correlation Matrix 由同一 Production Risk Model covariance snapshot 归一化得到，只额外接受 lookback、scope 和 matrix as-of 参数；因此 sample / EWMA / shrinkage model 会一致地影响 covariance、correlation 和 RC。浏览器不得另算 sample correlation，后端也不得使用 pairwise dates 拼矩阵。
- Risk 页 instrument-scope planning taxonomy 的 Current Drift 用非现金 Holdings rows 与 Accounts workspace 现金账户值合成当前 NAV；现金 exposure 来自 Accounts 的 cash bucket/account value，Holdings cash rows 不得再作为 instrument leg 参与分母或分组。
- Taxonomy 页 instrument-scope planning taxonomy 的当前覆盖视图使用同一现金口径：当前实体池由非现金 Holdings rows 加 Accounts cash bucket 组成，不把 Holdings 的 `cash:{currency}` 行再作为 instrument 实体展示或计入分母。根行 `Actual Weight` 表示当前实体池整体，必须约等于 100%；各 node 行表示已分类实体，`Without Classification` 只表示未分类残差。
- Risk 页 rolling metrics 与 correlation matrix 必须进一步校验 lookback window 覆盖率：按 resolved frequency 使用最小收益样本数、窗口起点最大偏离和至少 80% elapsed-day 覆盖。覆盖不足时结果为 insufficient-history / unavailable，不用更短窗口、pairwise dates、0 return 或前向填充替代。

### 2.7 缺失数据与覆盖率

如果某一估值日存在缺失数据：

- 价格缺失时，状态型 NAV / holdings 只能在对应 typed freshness policy 上限内使用同一 locked quote series 的最近完整观察，并记录 carry / age / policy / lineage；超过上限后 NAV 与 holdings valuation unavailable。carry 日不得被伪装为新的市场观察进入 return/risk 样本；
- benchmark 缺失时，benchmark-relative 指标只在重叠日期上计算，但不影响绝对口径 snapshot 的 `complete` 状态；
- 若重叠覆盖率低于配置阈值，结果标记为 `partial` 或 `unavailable`；
- 风控和 period analytics 页面必须显示 coverage ratio，前端不得把缺失数据伪装成正常结果。

## 3. 估值与 NAV

### 3.1 单个头寸市值

对于任意 instrument `i`：

$$
MV_{i,t}^{local} = Q_{i,t} \times P_{i,t} \times Multiplier_i
$$

其中：

- `Q` 为数量；
- `P` 为估值价格；
- `Multiplier` 默认为 `1`，若未来支持合约乘数则显式给出。

换算到组合基准货币：

$$
MV_{i,t}^{base} = MV_{i,t}^{local} \times FX_{i,t}^{local \to base}
$$

### 3.2 组合总 NAV

组合总净值：

$$
NAV_t = SettledCash_t^{base} + PendingSettlementNet_t^{base} + \sum_i MV_{i,t}^{base} + OtherAssets_t - Liabilities_t
$$

MVP 中：

- `SettledCash_t^{base}` 表示截至 `t` 已经按 `effective_date` 生效的现金 posting；
- `PendingSettlementNet_t^{base}` 表示 trade date 已确认、但 cash leg 尚未到 `effective_date` 的证券结算应收 / 应付款；该值在 settlement 前继续留在 NAV 中，settlement 当日转入 `SettledCash_t^{base}`；
- `deposit` / `withdrawal` 不是证券结算应收应付：它们在 `settlement_date` 前完全不进入本公式，在该 value date 同时进入 settled cash 与 external-flow adjustment；
- `Accounts` workspace、account-axis contribution、Research current context / actual rows 在任意 `as_of_date = t` 都必须复用同一条 settled-vs-pending 口径；不能出现 settled cash 已按 `effective_date` 截断，但 ending value / actual rows 又漏掉 pending settlement 的情况；
- `OtherAssets_t` 可先默认为 `0`，除非显式支持应收项；
- `Liabilities_t` 可先包含费用、税费、应付款等可识别项目；
- 若未显式支持某类应计项目，则必须在结果说明中标明未纳入。

### 3.3 权重

对任意头寸 `i`：

$$
portfolio\_weight_{i,t} = \frac{MV_{i,t}^{base}}{NAV_t}
$$

规则：

- 任何涉及 target、drift、risk diagnostics 的结果必须带 `weight_basis`；
- 首版 `TargetSet.target_weight` 的 canonical basis 固定为 `portfolio_nav`；
- API/UI 不允许只返回一个无语义的 `weight` 字段。

### 3.5 Cost Basis / Purchase Value

成本法是账面成本、realized capital gain 和税务/会计解释口径，不是组合绩效收益口径。
Portfolio 级 TWR、IRR、drawdown 和 contribution 必须基于 fair value、cash flow 与 P&L 事件计算，不得因为账户从 FIFO 改成 moving average 而改变组合级 return。

当前实现支持账户级成本法：

- 新建 `securities_account` 未显式选择时默认 `FIFO`；
- `FIFO` 保留真实 open lots，并按交易时间顺序释放成本；
- `moving_average` 对每个 `account + instrument` 保留一个滚动平均成本 bucket；API 仍输出一个 synthetic position lot 以支持 UI、转仓链路和审计引用；
- 修改账户成本法会从交易事实重新推导 holdings、lots、ledger postings 与 snapshots，不保留“历史旧算法”的兼容分支。

对导入边界上的 opening positions：

- `opening_balance + position` 是 bootstrap event，但成本法上必须 materialize 为成本单元；
- `FIFO` 下，一个 opening lot 表示一个后续可被 FIFO 消耗的成本单元；
- `moving_average` 下，opening position 进入该账户该资产的 rolling average bucket；
- `gross_amount` 表示 imported remaining cost basis，可以为 0；0 成本持仓仍然是合法持仓，后续卖出或内部转仓不得被误判为缺失成本。

### 3.5.1 Bond valuation boundary

首版债券估值采用 **valuation-first** 口径：

- 首版 `bond` 默认指 `plain-vanilla cash bond`；
- 组合 NAV、持仓市值、绩效计算统一以 **dirty market value** 为 canonical basis；
- 若数据源提供的是 clean price，则必须同时提供 accrued interest，再合成为 canonical dirty value；
- coupon 作为现金收益进入 ledger；到期本金回收通过 `maturity_redemption` 或等价显式事件入账；
- duration、convexity、yield、spread 等字段可作为解释性外部输入展示，但不是首版 canonical 自研计算结果。

#### Open position book cost

对任意当前头寸，`Cost Basis` / `Purchase Value` 定义为 open-position book cost：

- 当前仍未 disposed / transferred out 的 remaining cost basis；
- 包含已资本化到 buy/opening/reinvestment 成本的费用和税费；
- 不包含已经 realized 或 transferred out 的部分；
- 在 `FIFO` 下等于 open FIFO lots 的 remaining cost basis 之和；
- 在 `moving_average` 下等于 rolling average bucket 的 remaining cost basis。

`Avg Cost` 定义为：

$$
AvgCost^{book}_i = \frac{RemainingCostBasis_i}{RemainingQuantity_i}
$$

它不是某一笔交易的 purchase price。真实交易价格在 lot 层用 `entry_price = entry_gross_amount / entry_quantity` 表示，且不包含资本化费用和税费；`entry_cost_per_unit` 才包含资本化费用和税费。`moving_average` 的 synthetic lot 没有真实 tax-lot purchase price，展示时应优先使用当前 `Avg Cost`。

Holdings 是当前持仓状态表，只展示当前仍然 open 的 position quantity、settled cash balance、quote、market value、weight、open-position cost basis 与 unrealized P&L。资产级 TWR、period contribution、realized gain、dividend / coupon income、fees / taxes impact 和 closed positions 属于 `Performance` / security detail 的区间绩效视图，不进入 Holdings 默认列，也不作为 Holdings 的 canonical 语义。

Holdings 中的现金行按 settled cash ledger 逐币种生成，`instrument_id = cash:{currency}`：

- `market_value` 等于该币种 settled cash amount；
- `market_value_base` 等于该现金金额按 as-of date FX 转成组合 base currency 后的值；
- `cost_basis`、`cost_basis_base`、`Avg Cost` 与 unrealized P&L 对现金不适用；
- base-currency cash 的 instrument return、day return 和 volatility 为 `0`；
- non-base cash 的 instrument return / day return 来自该现金币种兑 base currency 的 FX series；
- pending settlement 不生成 Holdings cash row，但仍进入组合 NAV 与 pending settlement total。

`Market Value Base` 是任意 holding row 的 base-currency fair value。对非现金资产，它等于 `quantity * selected valuation quote` 再按 as-of date FX 转换；对现金，它等于 settled cash amount 的 base-currency value。Holdings `Portfolio Total` 的 market value 包含非现金市值与 settled cash，不包含 pending settlement；NAV 另行等于 market value 加 pending settlement。

`Day Change` / `Day Return` 是 as-of date 当前持仓规模上的一天 total-return 变动，不是账面估值价格变动或区间组合绩效：

- 非现金资产只使用 canonical `total_return` role 的同一 locked series 前后 complete 点，`day_return = current_total_return_quote / previous_total_return_quote - 1`；valuation role 只计算当前 market value，不能替代 total return；
- 非现金资产把该 total return 作用于当前 valuation market value：`day_change_value = current_market_value - current_market_value / (1 + day_return)`；`day_change_value_base` 再按 as-of date FX 转 base currency；
- base-currency cash 的 day change 为 `0`；
- non-base cash 的 day return 使用当前日与上一 calendar day 在同一 locked canonical FX book 中解析的汇率，二者都必须满足五个 calendar day policy；`day_change_value_base = cash_amount * (current_fx - previous_fx)`；
- 若缺少当前 total-return 点、同 series 上一点或 FX，相关字段必须为空，不得用 valuation、chart、低优先级 series 或 0 补齐。

Holdings 可以展示 quote-derived instrument market trend 指标，作为扫描当前持仓标的自身近期市场表现的辅助列：

- `Chart 1M / 3M / 6M / 1Y` 是前端展示用的 sampled path；
- `1W Return / MTD / YTD / 1Y` 只使用标的自身 selected quote series，计算为 `latest_quote / anchor_quote - 1`；
- selected quote series 按 `quote_selection_policy.total_return -> chart -> valuation -> reference` 选择；若策略为空，则选用截至 as-of 最新的一条 quote basis，不能混用多个 basis；
- `1W Return` / `1Y` 的 anchor quote 是目标日期或之前最近 quote；
- `MTD` / `YTD` 的 anchor quote 是月初 / 年初之前最近 quote；若历史不足，则只能使用期间内第一条 quote 作为明确标记的 partial-data anchor，不能展示为完整区间收益；
- `Current DD` 计算为 `latest_quote / max_available_selected_quote_to_date - 1`；
- `Vol 1M / 3M / 6M / 1Y` 使用同一 selected quote series 先按组合 resolved risk frequency 取 daily / weekly / monthly period returns，再按实际 elapsed days 年化；不得使用 `Chart *` sampled points；
- volatility 窗口必须有接近窗口起点的初始 quote、足够 elapsed-day 覆盖和最小收益样本数，否则为空。当前门槛为 daily `10 / 30 / 60 / 120`、weekly `3 / 6 / 12 / 24`、monthly `2 / 2 / 4 / 6`，分别对应 `1M / 3M / 6M / 1Y`；
- 这些指标不读取 quantity、cash flow、cost basis、FIFO / moving average、realized gain 或 income，因此不属于组合 TWR、holding contribution 或 book P&L。

Holdings `Forward RC` 是当前持仓的组合级 forward risk contribution：

- 只对 active non-cash holdings 参与协方差矩阵；现金行的 `forward_risk_share = 0`；
- 权重使用当前 holdings row 的组合权重，分母是全组合非现金风险组合的 variance，不是 instrument 自身风险，也不是 sleeve local denominator；
- covariance model、lookback、calculation frequency、missing-return policy 与 contribution mode 必须来自组合级 `Production Risk Model`；
- 若任一 active non-cash holding 缺少完整收益窗口、权重或正的组合 variance，Forward RC 进入 `unavailable`，不得用短窗口、0 return、pairwise covariance 或现金归一化兜底；
- Holdings 的 `Vol 1M / 3M / 6M / 1Y` 仍是标的自身 trailing sample volatility 观测列，不受 Production Risk Model 的 lookback 或 covariance model 影响，也不能替代 Forward RC。

Holdings group rows 不是后端 period-performance group：

- market value、cost basis、day change、open lots 等绝对量按组内 rows 汇总；
- unrealized return 使用覆盖完整的组内非现金 `unrealized P&L / abs(cost basis)`，不是成员百分比的加权平均；现金既不进入分子也不进入成本分母，纯 cash group 为空。任一参与聚合的非现金 row 缺少 local/base market value 或 cost basis 时，对应 subtotal / `Portfolio Total` fail closed 为 `null`，不得只汇总有值成员；
- `1W / MTD / YTD / 1Y Return` 使用 as-of date base-currency market value 权重对成员自身 return 加权；覆盖不足时为空；
- group volatility / drawdown 用组内成员 return series 在共同 period 上组成当前权重的组 return series 后计算，包含协方差效果，不等于成员 volatility 或 drawdown 的加权平均；
- base-currency cash 可作为 0-return 成员参与覆盖；non-base cash 使用其 FX return series；
- UI 中的 `Non-cash Portfolio` 行是当前 rows 的非现金 subtotal，只服务展示和导出，不是源事实、不参与 group、sort、detail 或 portfolio totals。

#### Unrealized P&L

$$
UnrealizedPnL_i^{local} = MV_i^{local} - CostBasis_i^{local}
$$

$$
UnrealizedPnL_i^{base} = MV_i^{base} - CostBasis_i^{base}
$$

$$
UnrealizedReturn_i = \frac{UnrealizedPnL_i^{local}}{|CostBasis_i^{local}|}
$$

`unrealized_pnl`、`unrealized_pnl_base`、`unrealized_return` 是后端 authoritative 字段。market value 或 cost basis 不完整时对应结果为 `null`；成本绝对值小于等于数值容差时 return 为 `null`。现金行三个字段全部为 `null`，不得把 FX gain 或现金余额伪装成 unrealized P&L。

#### Realized P&L

每次卖出时：

- `FIFO` 按最早未平的 lot 依次匹配；
- `moving_average` 按卖出时的 rolling average cost per unit 释放成本。

$$
RealizedPnL = NetSaleProceeds - MatchedCostBasis
$$

说明：

- realized P&L 是成本法相关的 book/tax P&L；
- TWR 不使用 realized P&L 作为收益率输入，因此成本法切换只重算 book capital gain / cost basis，不应改变 fair-value based return。

### 3.5.2 Internal position transfer

对 `transfer_object_type = position` 的内部转仓，canonical 规则如下：

- 它不是卖出再买入，不得形成 realized P&L；
- source account 使用自己的成本法决定 transferred cost basis；
- `FIFO` source 按 open lots 顺序切分并搬迁 source lot metadata；
- `moving_average` source 按 rolling average cost 生成一个 average-cost transfer slice；
- destination account 再按自己的成本法接收：FIFO 生成 destination lot，moving average 合并到 destination bucket；
- 0 成本 lot 可以内部转仓，只要数量充足并且 source slice 存在；
- `TradeView` 不把内部 position transfer 解释为新的 open trade 或 closed trade，只更新所属 account 归属。

### 3.6 Trades 视图计算

`Trades` 子视图不是原始交易流水，而是 lot matching 后的派生结果。

首版默认定义：

- **Open trade**：尚未被完全平仓的买入 lot 或 lot slice
- **Closed trade**：已被卖出完全匹配的 lot slice

每条 `TradeView` 至少应包含：

- open date
- close date（open trades 可为空）
- matched quantity
- purchase value
- proceeds
- realized / unrealized P&L
- holding period

规则：

- `Transactions` 页面显示原始账本流水；
- `Trades` 子视图显示 lot-matched 分析结果；
- 二者不能混为同一对象。

### 3.7 Events 视图语义

`Events` 子视图服务于单资产 detail pane，不是组合级总账页面。

默认事件来源包括：

- corporate actions
- dividend / coupon records
- data quality warnings
- manual event notes

规则：

- `Events` 是解释层时间轴，用于说明某个资产发生过什么；
- `Events` 可引用交易或公司行为，但不等于原始账本流水；
- 改变持仓或成本基础的事件，必须能追溯到底层 `Transaction` 或 `CorporateAction`。
- `share_split` 在 `effective_date` BOD 生效，ratio 定义为 `new_units / old_units`；账户总量先按公告规则处理碎股，再按 lot 比例分摊，不能逐 lot 截位。
- 拆分不改变账户总成本基础；旧 lot 关闭并以 lineage 连接到 carry-cost successor lot，单位成本按 ratio 反向变化。
- source factor/价格连续性只能生成 `detected` 候选，不能入账；只有 issuer / exchange / CSD 确认事件才可形成数量 posting。
- 若登记日与生效日之间存在交易而系统没有 due-bill 事实，计算必须 fail closed；`cash_in_lieu` 没有金额/应收事实时也必须 fail closed。
- 原始 `close / official_nav` 用于交易与市值；`adjusted_close / total_return_nav` 只用于收益、风险、图表和拆分日持仓涨跌解释。
- `dividend / coupon` 进入 `Events` 时，若已经入账，则必须引用对应 `Transaction`；不得在事件层再次形成独立 ledger posting。

## 4. FX 口径

### 4.1 基础换算

所有组合级价值对象先换算到 `base_currency` 再聚合。

### 4.2 基准货币收益

base currency 下的单期收益应优先直接从 base value 计算：

$$
r_{i,t}^{base} = \frac{MV_{i,t}^{base} - MV_{i,t-1}^{base} - CF_{i,t}^{internal}}{MV_{i,t-1}^{base}}
$$

若需要拆分本地收益与 FX 收益，则使用：

$$
1 + r_{i,t}^{base} = (1 + r_{i,t}^{local}) \times (1 + r_{i,t}^{fx})
$$

因此：

$$
r_{i,t}^{base} = r_{i,t}^{local} + r_{i,t}^{fx} + r_{i,t}^{local} \times r_{i,t}^{fx}
$$

规则：

- 正式结果以 `base` 结果为准；
- 本地收益与 FX 收益分解仅作为解释层附加输出。

## 5. 绩效口径

### 5.1 默认返回语义

除非明确写成 `absolute_change`、`delta` 或 `pnl`，否则系统中的“return”默认指：

- 组合级：`TWR`
- 资金使用效率：`IRR / MWROR`
- 基准对比：`benchmark-relative return`

GIPS-informed 规则：

- TWR 是默认组合绩效语言；
- MWR / IRR 是补充资金效率指标，不得在 UI 或 API summary 中替代 TWR；
- 若 IRR 因现金流符号、同日窗口或数学求根原因不可得，不能据此把已完整计算的 TWR 结果标记为失败。

Overview 展示 `Monthly Return Matrix`，按 year x month 展示月度 TWR，YTD 为可用月份的复合收益。

Overview 的组合收益、benchmark 对比、1M / 3M VOL 和 drawdown 使用组合 fresh complete as-of 作为窗口终点。若组合最新物化日期中只有部分持仓资产更新，Overview 不得使用该日期计算组合层 return / risk；例如组合 fresh complete as-of 为 `2026-05-20` 时，`1W Return` 的 TWR 使用 `2026-05-13` EOD 到 `2026-05-20` EOD 的端点口径，几何链接 `2026-05-14` 至 `2026-05-20` 的 daily returns。单资产 holdings trend metrics 仍使用标的自身 selected quote series 的最后行情日。

Performance 页面使用用户选择的区间作为唯一窗口。UI 的主要结构为：

- `Return & Risk Metrics`：组合级 TWR / annualized TWR、IRR / MWR、risk、drawdown。benchmark compare 只能使用 canonical `total_return` role 的真实 total-return basis；price / chart / valuation basis 不得冒充 benchmark total return。benchmark、difference 与 relative metrics 由后端统一输出，前端只格式化和绘图；
- `PerformanceSummary.history_reliability` 是年化发布资格的唯一权威：后端从响应窗口的 start/end 计算 elapsed days 与 inclusive calendar span，以 365 elapsed days 为最低门槛，并返回 `annualized_return_eligible`、reason codes、sample label 和 message；不合格时响应中的 annualized TWR、IRR / MWRR 与依赖年化收益的 Calmar 必须为 `null`。UI 仍必须读取该字段 fail closed；浏览器不得用 `Date.parse`、毫秒差或本地阈值重复计算。Benchmark comparison 使用实际 comparison start boundary 与 last aligned date 构造区间级 `history_reliability`，并按该资格约束组合、基准与差异的 annualized return / Calmar。
- `Calculation`：合并 realized risk attribution、initial value、group rows、external flow、portfolio total 与 final value。表格有和 Holdings 一致的 view selector；系统默认视图命名为 `Default`，展示区间期初权重、平均权重、期末权重、区间收益、收益贡献、标的自身风险、相关性和风险贡献；`Beta to Portfolio` 保留为高级可选列，不进入默认视图。Group By 默认是 `None`，语义是直接展示 instrument lines，不做额外分组；也可按 instrument type / currency / account / default planning taxonomy 聚合。instrument type 与 currency 是底层 contribution axis，不允许仅在前端把 instrument rows 相加；taxonomy 聚合用于期间复盘时优先使用区间期末 assignment 并保留 cash 独立组，不把 reclassification residual 当成真实 P&L；若 instrument 期末已清仓且期末不再有 active assignment，则使用其区间内有效 assignment 承接历史 P&L，不归入 Unassigned。`TWR` 来自对应 group 的 daily return slices；`Contribution` 来自 daily contribution 聚合。表格采用 `Initial Value + Deposits - Withdrawals + Period P&L = Final Value` 的桥接口径。
- Performance group daily return 使用组内 `total_pnl / (beginning_value + period capital flow in)`；直接 axis、taxonomy regroup 与 calculation detail 聚合必须沿用同一分母，不能在聚合后退化成只除以 beginning value。
- Calculation 底层的 `Capital Gain` 使用期间绩效成本，而不是账户 book cost；它是 reconciliation 派生值，不作为默认表格列展示。期初已有持仓按 start date 的 beginning market value 重置为期间成本，区间内买入按成交 gross amount 建立期间成本，期末未卖出的持仓用 end date market value 计算 `Unrealized Gain`。
- `Capital Gain = Realized Gain + Unrealized Gain`；`Realized Gain` 是期间卖出部分相对于期间成本的资本利得，`Unrealized Gain` 是期末仍持有部分相对于期间成本的资本利得。FIFO / moving average 只影响 Holdings / book P&L，不改变 Performance Calculation 的期间资本利得拆分。
- `Income` 只包含 dividend / coupon / interest / dividend reinvestment 收益确认，不包含 realized capital gain。fees、taxes、FX P&L 分列。P&L 与 book attribution 不和 benchmark 对比。
- Performance 中的区间风险贡献是 realized attribution，不另设 Risk tab。对每个 group，`Vol / Sharpe` 使用 group 自身 daily return；`Corr to Portfolio` 使用 group daily return 与 portfolio daily TWR；`Beta to Portfolio = Cov(R_g, R_p) / Var(R_p)` 保留为高级可选列；`Realized RC` 使用 `Cov(Contribution_g, R_p) / Var(R_p)`，衡量该 group 的 contribution 路径对组合已实现方差的协方差占比。这些指标服务区间复盘，不使用 Risk 页的 point-in-time covariance lookback。

Overview 的 chart compare 与 Performance 的 benchmark compare 是独立选择状态，因为用户可能对图表和区间绩效选择不同对比对象。

### 5.2 Daily TWR

正式版组合级 TWR 采用 Portfolio Performance 的日级 true time-weighted 逻辑：

$$
r_t = \frac{MVE_t + CF_{out,t}}{MVB_t + CF_{in,t}} - 1
$$

其中：

- `MVB_t`：当日开始市值，等于上一估值日 `MVE`；
- `MVE_t`：当日结束市值；
- `CF_in,t`：当日外部流入；
- `CF_out,t`：当日外部流出。

解释：

- 外部流入放在分母，视作在当日开始投入；
- 外部流出加回分子，视作在当日结束取出；
- `t` 对 external flow 指 cash value / `settlement_date`，不是较早的录入 `trade_date`；daily NAV、TWR、IRR、contribution、calendar bucket 与 Calculation bridge 必须共用该日期；
- 这样可以把 external flows 从业绩中中性化。
- 当前 daily snapshot engine 对每个 `as_of_date` 估值，因此外部现金流发生日天然有估值；若未来支持非日频估值，必须引入 large cash flow policy 与子期间收益几何链接，不能静默改用近似 MWR 方法。
- 对显式区间 `2026-04-01` 到 `2026-04-20`，`initial value` 是 `2026-04-01` 的 `MVB / beginning_nav`，即 `2026-03-31` EOD；`final value` 是 `2026-04-20` 的 `MVE / ending_nav`。区间 TWR 几何链接 `2026-04-01` 至 `2026-04-20` 的 daily returns。

#### 5.2.1 估值覆盖与 TWR 可靠性边界

计算层必须分开表达三类事实，不能用一个笼统的 `coverage_state` 互相替代：

- **current fair-value NAV coverage**：当前现金、pending settlement、持仓数量、估值价格和关键 FX 是否足以得到当日 NAV；
- **book P&L coverage**：成本基础、realized P&L、历史 FX 分解是否完整。它影响 P&L 解释，但不得阻止已经完整的 fair-value NAV 和 TWR；
- **TWR link reliability**：当日 NAV、外部现金流边界及上一有效 anchor 是否足以形成可链接子期间收益。

物化 snapshot 与 contribution slice 的契约固定为：

- `nav_coverage_state` + `nav_coverage_reason_codes` 只描述 current fair-value NAV；latest complete date、complete/partial/unavailable counts 和默认 snapshot 选择只读取该状态；
- `book_pnl_coverage_state` + `book_pnl_coverage_reason_codes` 只描述 realized/total P&L、income/expense、cash/instrument currency gains 与 return of capital 的可解释性。book coverage 不是 complete 时这些 book 输出必须为 `null`，但不得改写 NAV coverage；
- `twr_state`、`twr_reliability_status`、`twr_reliability_reasons` 独立运行；book cost 或历史 FX attribution 缺失本身不能打断 fair-value TWR；
- `complete` 必须配空 reason list，`partial` / `unavailable` 必须配至少一个稳定 reason code；物理列与 JSON payload 必须逐项相等；
- 每个 snapshot 保存本次 locked FX window 的 dependency manifest 与确定性 fingerprint，使 FX source leg、revision 与反向/交叉路径可审计。

`stale_price_flag` / `stale_fx_flag` 是宽口径数据质量标记，可能包含成本、历史 P&L 或日历日期 carry，不可直接用于打断 TWR。TWR 断链只读取当前估值 resolver 明示的 reliability stale、当前估值不完整以及外部现金流边界；原始行情日期早于 `as_of_date` 本身不是 stale 的充分证据，因为周末、节假日和周频/月频基金 NAV 都会合法复用最近一次官方观察。

每日状态机固定为：

| 当日条件 | `twr_state` | reliability | 日收益与后续处理 |
| --- | --- | --- | --- |
| fresh complete valuation，无外部流 | `linked` | `reliable` | 正常计算并链接 |
| fresh complete valuation，有 deposit / withdrawal | `linked` | `reliable` | 用外部流调整分母/分子后链接 |
| 仅日历 carry，无外部流 | `carry_forward` | `qualified` | 可保持收益链，reason 为 `carried_forward_valuation_without_external_flow` |
| resolver 明示 current price / FX stale，无外部流 | `carry_forward` | `qualified` | 可保持收益链，reason 为 `stale_valuation_without_external_flow`，不得进入风险样本 |
| 任意 carry 且有 deposit / withdrawal | `broken` | `unavailable` | 不计算当日 TWR，reason 为 `carried_forward_valuation_on_external_flow` |
| resolver 明示 stale 且有 deposit / withdrawal | `broken` | `unavailable` | 不计算当日 TWR，reason 为 `stale_valuation_on_external_flow` |
| current valuation 或 flow conversion 不完整且有外部流 | `broken` | `unavailable` | 不计算当日 TWR，reason 为 `incomplete_valuation_on_external_flow` |
| 已断链且尚无 fresh complete valuation | `broken` | `unavailable` | reason 包含 `awaiting_fresh_valuation_anchor` |
| 断链后的第一笔 fresh complete valuation | `reanchor` | `qualified` | 只建立新 anchor，不生成跨断点收益，reason 为 `fresh_valuation_reanchor` |
| re-anchor 后的下一有效子期间 | `linked` | `reliable` | 从新 anchor 恢复计算 |

风险样本遵守以下不变量：

- resolver 明示 stale price / FX、`broken` 边界和 unavailable link 一律排除；
- 纯 carry 日因 `market_observation_count = 0` 排除；
- 混合频率组合中，如果日频 sleeve 有真实新观察而周频/月频 sleeve 仅按既定 cadence carry，该组合日可以进入 realized risk 样本。calendar carry 是可见的质量限定，不等于 stale；
- 窗口 reason 必须从 daily snapshot 的稳定 reason 聚合。不得看到任意 `carry_forward` 就统一改写成 stale；若窗口同时包含 calendar carry 与 resolver-explicit stale，两类 reason 都要按稳定顺序返回。

查询和归因必须 fail closed：

- 任何跨越 `broken`，或在窗口中段遇到 `reanchor` 的窗口，`cumulative_twr`、`annualized_twr`、drawdown 和依赖收益序列的风险指标均为 `null`，reason 为 `crosses_broken_twr_boundary`；
- 从 re-anchor 开始且只包含其后有效子期间的新窗口可以恢复计算；
- contribution、taxonomy regroup、calendar bucket 和 bucket drilldown 使用同一窗口 gate。不可用的 portfolio TWR 不能被当作 `0`，line contribution、total contribution 与 residual 都必须为 `null`；P&L 绝对额仍可保留用于账务解释；
- API 的 `twr_state`、`twr_reliability_status`、`twr_reliability_reasons` 是必填契约，所有 empty / unavailable 分支必须显式构造，不允许依靠模型默认值隐藏漏传；
- materialized snapshot payload 口径变化通过 `DAILY_SNAPSHOT_CALCULATION_VERSION` 整体失效重建，不新增事实表 schema，也不保留旧 JSON 兼容层。增量刷新若历史前缀已经包含 `broken` / `reanchor`，必须退回全历史重建，不能从断点后伪造连续 seed。

### 5.3 Cumulative TWR

给定区间内共有 `n` 个子期间：

$$
R_{cum} = \prod_{t=1}^{n}(1+r_t)-1
$$

这是组合页面和绩效页中默认的区间收益口径。

组合的 `TWR Index` 是把 `R_cum` 归一到 100 后得到的组合表现曲线。它在语义上类似基金的 total-return NAV / cumulative NAV，但不是组合会计单位净值；它只用于投资表现、回撤、波动和 benchmark comparison，不用于资产规模或账面 NAV 展示。

### 5.4 Annualized TWR

若区间长度为 `Y` 年（按 `ACT/365.25` 计算）：

$$
R_{ann} = (1 + R_{cum})^{1/Y} - 1
$$

若 `Y` 接近 0，则年化结果记为 `unavailable`。

### 5.5 IRR / MWROR

IRR 使用 XIRR 语义，按实际日期折现：

$$
\sum_{k=0}^{m}\frac{CF_k}{(1+IRR)^{\tau_k}}=0
$$

其中：

- `\tau_k` 为相对起始日的 `ACT/365.25` 年分数；
- `CF_k` 采用“从投资人视角”的符号约定。

默认现金流构造：

- 期初组合市值：负数（视为已投入资本）
- 期间 external contributions：负数
- 期间 withdrawals / distributions：正数
- 期末组合市值：正数

说明：

- IRR 反映资本使用效率；
- TWR 反映经理在中性化 external flows 后的投资表现；
- UI 不允许用 IRR 替代 TWR 展示“组合收益”。

### 5.6 Absolute Change 与 Delta

### Absolute Change

$$
AbsoluteChange = MVE - MVB
$$

### Delta

$$
Delta = (MVE - MVB) - NetExternalInflow
$$

其中：

$$
NetExternalInflow = \sum CF_{in} - \sum CF_{out}
$$

`Delta` 表示扣除 external cash flows 后的绝对收益金额。

在 period calculation 中，`Delta` 也等于本期 `Period P&L`，并应满足：

$$
FinalValue = InitialValue + NetExternalInflow + PeriodPnL
$$

其中：

$$
PeriodPnL = CapitalGain + Income - Fees - Taxes + FXPnL
$$

其中：

$$
CapitalGain = RealizedCapitalGain + UnrealizedCapitalGain
$$

`RealizedCapitalGain` 和 `UnrealizedCapitalGain` 在 Performance Calculation 中使用期间绩效成本：

- 期初已有持仓按期初市值重置为期间成本；
- 区间买入按成交 gross amount 建立期间成本；
- 区间卖出释放对应期间成本并形成 `RealizedCapitalGain`；
- 期末仍持有的剩余数量形成 `UnrealizedCapitalGain`；
- 已在期末完全卖出的资产没有剩余 period lot，因此 `UnrealizedCapitalGain = 0`。

账户 book P&L 的 `EndingUnrealizedPnL - BeginningUnrealizedPnL` 可用于会计解释，但不得作为 Performance Calculation 的 capital gain split。

### 5.7 Drawdown

基于累计增长指数：

$$
G_t = \prod_{s=1}^{t}(1+r_s)
$$

运行峰值：

$$
Peak_t = \max_{s \le t} G_s
$$

回撤：

$$
DD_t = \frac{G_t}{Peak_t} - 1
$$

关键指标：

- `current_drawdown = DD_t`
- `max_drawdown = min(DD_t)`
- `max_drawdown_days = peak -> trough`
- `drawdown_duration_days = peak -> recovery`

约束：

- 组合级 drawdown 必须基于 `TWR` 复合后的 `G_t`，不得基于资产规模 `NAV_t` 直接计算；
- 任何带 `start_date / end_date` 的区间 summary 都必须用区间内 `daily_twr` 重新复合并重算 drawdown，不能直接复用 inception-to-date 的 `cumulative_twr` 或 snapshot-level drawdown；
- 区间第一笔有效收益如果已经形成回撤，drawdown peak 应以区间起点锚点为基准，而不是把第一条收益观察日误当作峰值日；
- 若主图显示 `Portfolio Value`，下方 drawdown 仍然使用 `TWR Index`，因为外部出入金不应制造或稀释投资回撤。

### 5.8 Volatility / Sharpe / Sortino / Tracking Error

### Portfolio volatility

默认使用组合 `daily_twr` 的 simple returns 构造内部区间风险统计；当输出正式 GIPS Report 风格披露时，ex-post standard deviation 必须改用 monthly returns，并按同一方法同时计算组合和 benchmark。资产规模 `NAV_t` 的变化不得作为组合级 volatility / Sharpe / Sortino 的输入。

$$
\sigma_{ann} = std(r_t) \times \sqrt{periods\_per\_year}
$$

### Sharpe ratio

$$
Sharpe = \frac{\mu_{ann} - r_f}{\sigma_{ann}}
$$

MVP 默认：

- `r_f = 0`，除非显式提供 risk-free series；
- `\mu_{ann}` 使用同一区间、同一 periodicity 的 arithmetic mean return 年化；`annualized_twr` 是收益展示字段，不作为 Sharpe / Sortino 的分子。
- 若未来接入 risk-free series，必须按同一日期窗口、同一 periodicity 构造 excess return，不得混用静态年化收益率和日/月收益率。

### Sortino ratio

$$
Sortino = \frac{\mu_{ann} - r_f}{\sigma_{downside}}
$$

### Tracking error

给定 benchmark 同频收益 `r_{b,t}`：

$$
a_t = r_{p,t} - r_{b,t}
$$

$$
TE_{ann} = std(a_t) \times \sqrt{periods\_per\_year}
$$

### Information ratio

$$
IR = \frac{mean(a_t) \times periods\_per\_year}{TE_{ann}}
$$

## 6. Benchmark-relative 口径

### 6.1 基准序列

benchmark 必须先转化到组合基准货币，并对齐到组合估值日期。

当前 Performance 页的手动 `Compare benchmark` 调用独立的后端 comparison contract。服务在同一个数据库 Session 内读取权威物化 TWR 与锁定的 benchmark canonical `total_return` window，要求 portfolio TWR 与 benchmark reliability 均为 `reliable`、benchmark 与 portfolio base currency 一致、period start boundary 锚点没有超过按数据频率确定的最大间隔，并且每个 portfolio eligible return date 都有 benchmark 同日 level。服务统一计算 portfolio / benchmark metrics、difference、tracking error、information ratio、beta、up/down capture、CAP ratio 与归一化图形点，并携带 coverage、resolver status、quote lineage fingerprint 和 method version。任一条件失败时整组相对结果为 null；在未接入后端 benchmark FX conversion 前不得输出 raw-currency、stale-filled 或半截区间结果。

若 benchmark 提供的是 NAV / index level：

$$
r_{b,t} = \frac{B_t}{B_{t-1}} - 1
$$

### 6.2 Primary benchmark

一个组合可以有多个 benchmark definition，但 canonical 计算必须先从 `PortfolioBenchmarkAssignment` 解析出当前生效的 `primary benchmark`。

以下指标必须始终基于 resolved `primary benchmark`：

- benchmark return
- excess return
- tracking error
- information ratio
- active contribution

规则：

- current-state 页按 `as_of_date` 解析 active primary benchmark assignment；
- period 页按 `[period_start, period_end]` 解析 primary benchmark assignment；若区间内发生切换，period analytics 必须显式标记 mixed benchmark context 或按分段汇总；
- canonical benchmark resolution 不得读取 `Portfolio` 上的固定 benchmark pointer；唯一真相来源是 `PortfolioBenchmarkAssignment` 的生效区间解析；
- 若不存在可解析的 primary benchmark assignment，则 benchmark-relative 结果进入 `comparator missing / unavailable` 状态。

### 6.3 默认比较对象选择

系统不采用 `benchmark / target set / alert rule` 之间的静默互相替代。

canonical 规则如下：

- market-relative return / active contribution / snapshot relative columns：使用 `primary benchmark`
- benchmark_active_weight / benchmark-relative exposure / benchmark-active bets：使用 `primary benchmark`，且仅在 benchmark composition 可用时启用
- target_weight_gap / construction drift / rebalance diagnostics：在 `Risk` 中分别使用 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `weight` 维度
- limit checks / alerts：使用 configured `AlertRule`
- risk budget gap：在 `Risk` 中分别使用 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `risk_budget` 维度
- period target weight drift / target risk budget summary：使用 selected planning taxonomy 在 `[period_start, period_end]` 上的 resolved target timeline，并按已启用维度分别解释

其中：

- 被用于 drift / risk budget gap 的 selected taxonomy 必须是 `planning_enabled = true`；
- `Risk` 必须分别计算 selected planning taxonomy 下 active `SAA` 与 active `TAA` 的 `weight` / `risk_budget` comparator；UI 合并展示为 `Weight Target Gap` 与 `Risk Target Gap` 两个面板。每个 sleeve 只占一条 row，右侧同图并列展示当前值、`SAA` target 与 `TAA` target。任一来源或维度未配置时，只标记对应 comparator unavailable，不跨 `SAA` / `TAA` 或 `weight` / `risk_budget` 回退；
- period timeline 仍必须显式携带各段 target source 信息；若区间内来源随时间变化，则标记为 `mixed_timeline`
- period analytics 必须把上述结果 materialize 为正式 `ResolvedTargetTimeline` / `ResolvedTargetSegment`，而不是匿名 timeline blob；
- `TargetSet(type = taa)` 在存储层必须已物化为对已启用维度完整的目标集，运行时不做稀疏 overlay 解析；
- period 内若 target 发生切换，系统必须按生效区间分段汇总，而不是拿单一期初或期末 target 解释整个区间；
- 若用户切到纯分析 taxonomy，系统只能展示 `absolute only`，并标记 comparator missing。

若某项分析缺少其 canonical comparator：

- 结果状态记为 `unavailable`；若该页面本身支持绝对口径，则只展示 `absolute only`
- UI 必须明确标识 comparator missing
- 结果对象必须显式携带 `taxonomy_id` 与按维度解析后的 target source 信息
- 不允许无提示地改用另一类比较对象

### 6.4 Excess return

区间层面默认采用：

$$
ExcessReturn = R_{p,cum} - R_{b,cum}
$$

日频层面采用：

$$
a_t = r_{p,t} - r_{b,t}
$$

说明：

- `ExcessReturn` 是 arithmetic relative result；
- 若未来需要 `relative wealth ratio`，必须另起字段，不得复用 `excess_return` 名称。

### 6.5 Benchmark coverage

benchmark 能力应按可用数据分层理解：

- `return_only`：仅有 benchmark return series；可计算 excess return、tracking error、information ratio
- `group_weight`：额外有 benchmark group weights；可计算 group-level `benchmark_active_weight`、relative exposure 与 group-level benchmark attribution
- `constituent_weight`：额外有 benchmark constituent weights；可计算 security-level `benchmark_active_weight` 与更细的 benchmark-relative attribution

benchmark-relative 指标必须携带：

- overlap start / end
- overlapping observations
- coverage ratio
- status: `complete` / `partial` / `unavailable`

若 benchmark 只有 `return_only` 能力：

- 不允许计算 `benchmark_active_weight`
- 不允许计算 benchmark-relative exposure
- 不允许启用 Brinson 类 attribution
- UI 必须明确标识 `benchmark composition unavailable`
- period analytics 只能输出 `benchmark-relative performance summary`，不输出 `benchmark-active bets summary`

## 7. 归因口径

### 7.1 首版归因目标

首版优先支持**日频算术归因**，而不是一开始就做最复杂的多层 Brinson 变体。

说明：

- benchmark group-weight coverage 不足时，仅输出 contribution / excess return 口径，不强行输出 Brinson 风格分解；
- 任何 Brinson 或 benchmark allocation/selection 归因都依赖可用的 benchmark composition 数据。

### 7.2 组合日贡献

对任意 instrument 或 group `g`：

$$
Contribution_{g,t} = w_{g,t-1} \times r_{g,t}
$$

区间贡献：

$$
Contribution_{g,period} = \sum_t Contribution_{g,t}
$$

### 说明

- `w_{g,t-1}` 使用上一估值日权重，避免当日结果前视；
- 组级 return 由组内 base-currency market value 变化得到；
- 归因和总收益之间允许存在小 residual，必须显式展示。

### 7.3 Active contribution

若 benchmark 同样可按 group 聚合：

$$
ActiveContribution_g = Contribution_{p,g} - Contribution_{b,g}
$$

首版优先支持：

- instrument contribution
- taxonomy contribution
- planning taxonomy contribution
- benchmark-relative group contribution

## 8. 暴露与集中度口径

### 8.1 Exposure aggregation

按任意 grouping axis（taxonomy node、selected planning taxonomy node、region、sector）聚合时：

$$
Exposure_{group} = \sum_{i \in group} weight_i
$$

其中 `weight_i` 必须来自显式 `weight_basis`。

### 8.2 Concentration

### HHI

$$
HHI = \sum_i w_i^2
$$

### Top-k concentration

$$
TopK = \sum_{i \in largest\ k} w_i
$$

默认：

- concentration 诊断默认先排除现金，再对剩余头寸归一化；
- 若改用 `portfolio_nav`，必须在结果对象中显式标记。

## 9. Weight Drift 口径

### 9.1 单项 weight drift

对任意 instrument 或 group：

$$
Drift_i = CurrentWeight_i - TargetWeight_i
$$

### relative drift

若 `TargetWeight_i \neq 0`：

$$
RelativeDrift_i = \frac{CurrentWeight_i - TargetWeight_i}{TargetWeight_i}
$$

其中：

- `TargetWeight_i` 来自按维度解析后的 resolved `TargetSetLine.target_weight`
- canonical target 只能在 selected taxonomy 的 `budgeting_level` 上直接录入；父层节点目标必须派生汇总
- 首版 canonical target weight 固定为 `portfolio_nav` basis
- drift 计算前必须先校验 selected `TargetSet` 启用了 `weight` 维度，且在 budgeting level 上形成完整节点集；同一 scope 的 direct members 必须加总为 `100% ± epsilon`
- `Risk` 的 `SAA Weight` 与 `TAA Weight` comparator 独立计算，并在 `Weight Target Gap` 面板合并展示；若某个 target source 未启用 `weight` 维度，只有该 comparator 记为 `unavailable` / `comparator missing`

### 9.2 Total drift

$$
TotalDrift = \sum_i |Drift_i|
$$

### Rebalance turnover lower bound

若需要更接近最小调仓量的量纲，则另算：

$$
TurnoverFloor = \frac{1}{2}\sum_i |Drift_i|
$$

规则：

- `total_drift` 与 `turnover_floor` 必须分开命名；
- 不允许把二者混成一个“总偏离”指标。

### 9.3 Drift weight basis

首版 target drift 的 canonical 比较口径固定为 `portfolio_nav`：

- `TargetSet.target_weight` 一律按 `portfolio_nav` 定义；
- `CurrentWeight_i` 在 canonical drift 中也必须使用 `portfolio_nav`；
- 若某层 target 来自 sleeve-local capital split 或 optimizer recipe，则它不是 canonical drift 的直接输入；只有解析成 `portfolio_nav` basis 的 resolved implementation weight 后，才能进入 drift compare；
- 因而不能默认用 `ParentWeight × ChildLocalWeight` 机械展开所有层级权重；只有当 child weight 明确也是 capital share 且不会被内部求解器 / 杠杆 / 对冲改写时，该乘法才成立；
- resolved `target_weight` 必须在 budgeting level 上形成完整节点集，并在同一比较分母下加总为 `100% ± epsilon`；若组合存在 gross leverage / overlay，必须由独立 overlay / leverage config 先解析成 implementation target，不能把 gross exposure 直接混入 canonical target drift；
- 排除现金后的分析口径可用于暴露、集中度或纯分析展示，但不能替代 canonical target drift 口径。

### 9.4 MVP weight drift source decomposition

首版 canonical weight drift source decomposition 只锁定到三层对象：

- `resolved_target`
- `intended_target`（optional）
- `actual`

定义：

- `resolved_target`：由 selected planning taxonomy 下的 active `SAA` / `TAA` 解析出的正式 target；
- `intended_target`：把 active tilts 物化到 budgeting level 后形成的意图目标视图；若未提供该物化视图，则视为 unavailable；
- `actual`：当前组合在同一 budgeting level 上的实际权重。

若 `intended_target` 可用：

$$
IntentionalGap_i = IntendedWeight_i - ResolvedTargetWeight_i
$$

$$
UnintendedGap_i = ActualWeight_i - IntendedWeight_i
$$

且：

$$
Drift_i = IntentionalGap_i + UnintendedGap_i
$$

规则：

- `intentional / unintended` 分解只在 `intended_target` 已被物化且与 current target 使用同一 budgeting level 时启用；
- 单条 `TiltDecision` 本身不是 drift source decomposition 的直接计算输入；
- 首版不把 `market drift` 与 `execution bias` 进一步拆成独立 canonical 字段，避免伪精确解释。

## 10. 目标风险份额口径

### 10.1 目标

目标风险份额模块要回答的不是“仓位偏了多少”，而是：

- 风险贡献偏了多少；
- 偏离来自哪里；
- 是否已经背离当前 taxonomy target design。

### 10.2 Covariance-based portfolio risk

给定权重向量 `w` 和协方差矩阵 `\Sigma`：

$$
\sigma_p = \sqrt{w^\top \Sigma w}
$$

### 10.3 Marginal / Component Risk Contribution

Risk 页和 Research solver 使用同一套 covariance model id 与 contribution mode：

- `ewma_vol_shrinkage_corr_covariance`
- `ewma_covariance`
- `sample_covariance`
- contribution mode: `signed` / `abs`

这里的 `\Sigma` 是年化 covariance matrix。`sample_covariance` 的日/周/月 period return 样本使用 `n - 1` 分母；EWMA 和 Ledoit-Wolf 这类模型可在模型内部使用其自身估计口径，但必须通过 model id 明确区分。混合日频 / 周频 / 稀疏 NAV 时，先按 2.6.1 的 target calculation frequency 取 period-end 观测，再计算收益；不做 stale 价格生成的 0 return。默认 `strict` policy 下，active return matrix 必须在所有参与成员上完整，协方差和相关性按同一组完整收益日期估计并按这些日期的实际观察密度年化。Research 只有在用户显式选择 `complete_case_drop` 且通过覆盖率与尾部新鲜度约束时，才可以整行删除缺失 period 后继续求解。若完整有效收益不足两期，solver 必须进入 insufficient-history 诊断，而不是用不同长度的持有期收益硬拼协方差。

### Marginal contribution to variance

$$
MCV_i = (\Sigma w)_i
$$

### Signed component contribution to variance

$$
VCTR_i^{signed} = w_i \times MCV_i
$$

### Absolute component contribution to variance

$$
VCTR_i^{abs} = |VCTR_i^{signed}|
$$

### 10.4 Forward covariance risk share

正式版同时保留 `signed` 与 `abs` 两种 forward covariance risk share 口径，并在 Holdings、Risk 和 Research 中使用同一组选项。Research solver 的 primary mode 是 `signed`；当 signed shares 因对冲或负相关导致目标预算不可稳定匹配时，只能由配置显式切换到 `abs` 作为 alternate diagnostic view，不能在求解失败后自动切换。

Signed share:

$$
RiskShare_i^{signed} = \frac{VCTR_i^{signed}}{w^\top \Sigma w}
$$

Absolute share:

$$
RiskShare_i^{abs} = \frac{VCTR_i^{abs}}{\sum_j VCTR_j^{abs}}
$$

解释：

- 对冲或负相关位置可能产生负的 signed contribution；
- `signed` 更忠实地描述边际组合风险，适合 research solver 的 primary diagnostic；
- `abs` 更适合做 PM 视角的 alternate target risk share comparison；
- 页面展示必须标明当前 contribution mode，不能把两种口径混合比较。
- Performance `Calculation` 中的 realized risk attribution 使用实际区间组合路径和实际历史权重，是事后归因；不得用于判断当前持仓是否偏离风险预算，也不得和本节 forward risk share 直接相减。

### 10.5 Risk budget gap

若 `TargetRiskShare_i` 为目标风险预算占比，`RiskShare_i` 必须来自页面或 solver 当前选定的 contribution mode：

$$
RiskBudgetGap_i = RiskShare_i - TargetRiskShare_i
$$

总预算偏离：

$$
TotalRiskBudgetGap = \sum_i |RiskBudgetGap_i|
$$

其中：

- `TargetRiskShare_i` 来自当前 row 中对应的显式 risk comparator（`SAA Risk` 或 `TAA Risk`）的 `TargetSetLine.target_risk_share`
- canonical target 只能在 selected taxonomy 的 `budgeting_level` 上直接录入；父层节点目标必须派生汇总
- `RiskShare_i` 与 `TargetRiskShare_i` 必须使用同一风险分母和同一 contribution mode；首版 canonical top-level compare 的分母是 selected taxonomy 下的 portfolio-level forward risk share
- 层级 sleeve 内部的 `25%` 这类 local risk budget 表示“占父 sleeve 内部风险的 25%”，不是全组合风险的 `25% × 父层预算`
- 因而禁止通过祖先 `target_risk_share` 乘法把 local sleeve risk budget 铺平成全局 risk-budget target；若需要全局 leaf comparator，必须先由 solver / resolved implementation target 在全组合协方差下显式解出
- risk budget gap 计算前必须先校验 resolved risk-budget target 可用，且其非现金节点的 `target_risk_share` 加总为 `100% ± epsilon`，现金节点 `target_risk_share = 0`
- `Risk` 的 `SAA Risk` 与 `TAA Risk` comparator 独立计算，并在 `Risk Target Gap` 面板合并展示；若某个 target source 未启用 `risk_budget` 维度，只有该 comparator 记为 `unavailable` / `comparator missing`

说明：

- `RiskBudgetGap` 就是首版里唯一的风险预算偏离指标；
- 其计算输入是当前持仓 forward `RiskShare_i` 与 `target_risk_share`；
- canonical 计算对象是 selected taxonomy 下按维度解析后的 risk-budget target，而不是简单等同于整套 active target set 或独立风险预算对象。
- 若用户 drill into 某个 sleeve 做 local compare，则结果对象与 UI 必须显式标记 denominator = `parent_local_risk`，不得静默沿用 portfolio-level 含义。

### 10.6 Weight basis for target risk share comparison

默认规则：

- `risk share` 基于参与风险计算的头寸权重；
- 首版默认在参与风险计算的头寸集合内归一化权重进入协方差风险计算；
- 现金默认不贡献市场风险，除非显式建模为风险因子。

如果 target-set 设计需要把 cash 作为 selected planning taxonomy 下的独立节点展示：

- 它可以在 SAA 或 TAA `target_weight` 中存在；
- 但在 covariance-based forward risk share 中通常为 `0`；
- UI 必须解释“capital share != risk share”。

### 10.7 Research target solve

Research current target solve 使用 planning taxonomy 的层级 scope 做递归求解：

- 从最末端 sleeve 开始求解，再把每个子 sleeve 的目标权重和收益序列上卷到父 scope；
- `scope_default` 只解析当前 scope 自己的 `default_target_dimension`，不得因为目标集缺失而静默切到另一个维度；
- 若当前选中 scope 显式指定 `weight` 或 `risk_budget`，该 override 只作用于选中 scope；子 sleeve 仍按自己的 scope default 求解；
- 多成员 scope 必须有 active complete `SAA` 或 `TAA` target set。`TAA` 优先于 `SAA`；两者都缺失、启用维度不完整或目标值加总不正确时，该 scope 求解失败，不生成等权或目标权重替代结果；
- `weight` scope 使用该 scope direct members 的 `target_weight` 拟合本地权重；已启用的 `weight` 维度必须逐成员显式给出且合计为 `100%`；
- `risk_budget` scope 使用非现金 direct members 的 `target_risk_share` 求本地目标权重，现金的 `target_risk_share` 必须为 `0`，非现金风险份额加总为 `100%`；
- 若某个成员被标记为 frozen，优先使用该成员 as-of actual weight；若 actual weight 不存在，只能使用已配置的 `target_weight`，不能把 `target_risk_share` 当作资金权重；
- risk-budget solve 至少需要两个完整对齐 return observations；`strict` policy 下任何 active member 缺失都会失败，`complete_case_drop` 只能在显式选择且通过缺失行比例、latest complete row 新鲜度和最小完整观测数约束后使用，不能把 target risk share 当作 target weight；
- risk-budget solve、current risk-share estimate 与 target-volatility overlay 必须使用组合级 `Production Risk Model` 的 covariance model、lookback、frequency、missing-return policy 和 contribution mode；
- risk-budget solve 的 achieved risk share 最大绝对误差必须在显式阈值内；当前阈值为 `1e-4` share units，即 `0.01 percentage points`。超过阈值或产生负 signed risk share 时，该 scope 求解失败，不切换到 `abs` mode，也不返回旧求解器状态；
- Research `Solved Result` 的 `Look-through RC` 使用最终 leaf 权重在全组合 leaf covariance 上重新计算。父 scope 的风险预算求解误差仍以该父 scope 的本地 covariance 为准；当 covariance model 在每层重新做 correlation shrinkage 时，look-through RC 可以与父层本地 achieved risk share 有差异，UI 和报告必须明确区分两种口径；
- 单成员 scope 只允许输出数学上唯一确定的本地目标：非现金/普通成员权重 `100%`，现金 risk budget `0%`；
- 根 scope 完成风险 sleeve 权重后，`target_volatility` / `volatility_cap` / `fixed_gross` capital overlay 才对非现金目标权重整体放缩，并把残差写入系统 cash-like member；root top sleeve bounds 只约束 root 的直接 sleeve，违反上下限或与 frozen/fixed gross 不可行时，该 run 必须失败。没有 cash-like member、目标波动率无法用正的估计波动率缩放或 overlay 后违反可行约束时，该 run 必须失败或显式 unavailable，不用 unit gross、等权或旧算法兜底。

Research backtest 使用同一 Production Risk Model、planning taxonomy、TargetSet、frozen sleeves、top sleeve bounds 和 capital overlay 逐个 rebalance date 重算目标。支持 `1m` 与 `3m` rebalance；benchmark 历史只影响 benchmark 曲线和相对指标，不得推迟或阻断组合自身 backtest 起点。若组合成员共同历史不足完整 risk window，backtest 返回空 points 和 warning，不生成晚于 as-of 的 rebalance 日期，不用更短风险窗口替代。

Policy Replay 的 metrics schema 固定为 `research-backtest-metrics.v2.history-gated-arithmetic-sharpe`，Portfolio、Benchmark 与 Relative 三组各自携带后端 `history_reliability`。少于 365 elapsed days 时，geometric `annualized_return` 与依赖它的 Calmar 必须为 `null`；period / YTD return、drawdown、按实际观察密度年化的 volatility 仍可发布。Sharpe 使用同频 periodic returns 的 arithmetic mean × empirical periods per year 作为年化分子，再除以同频年化波动率；不能用 geometric annualized return 作 Sharpe 分子。浏览器不计算日期跨度或 365 日阈值，只按每组 eligibility fail closed 展示。

每次 run 必须输出 root `solve_event` 和完整 `scope_solve_events`，用于复核每层 scope 的默认维度、实际维度、solver、RC mode、risk gap 与成员数。

每次 run 还必须输出 `calculation_frequency` profile，包括用户请求频率、最终解析频率、可选频率、源数据频率计数和状态文案；同时输出 missing-return policy、rows before / after、missing rows、dropped rows、latest complete date 与 trailing staleness，便于复核样本是否被严格保留或显式 complete-case 删除。组合 workspace 的组合名状态栏展示当前组合的默认 risk basis，方便用户确认当前是 daily / weekly / monthly 口径。

### 10.8 Allocation Research canonical market-data boundary

Allocation Research 的收益、风险和回测输入固定采用共享 registry 的 canonical `total_return` role：基金只能采用 `total_return_nav`、`dividend_adjusted_nav`、`reinvested_nav` 等真实总回报口径，股票/ETF 采用 `adjusted_close`。估值、chart、reference 或未调整 `close` 不得在 `total_return` 缺失时替代。

每次 current solve 或 backtest 必须先收集 scope 内全部 instrument ids、canonical currencies、组合本币与所需 FX pairs，并在同一个 SQLAlchemy Session 内锁定完整 quote windows 和 canonical FX book。锁定完成后，频率识别、递归 scope solve、各 rebalance date 与 benchmark sampling 只能读取该内存快照；日期数量和 rebalance 次数不得增加 SQL 查询。

- quote window 只接受显式 `role=total_return`，并保留 series policy revision、current revision ids、excluded revision lineage 与 dependency fingerprint；
- FX 固定使用 `portfolio_fx_consumer.v1` 的五个自然日 freshness policy，支持 identity/direct/inverse/USD-cross，cross 必须保留有序两腿 dependency；
- adopted quote 与 FX 值在进入 pandas 前保持 `Decimal`，只在 pandas/NumPy 数值计算边界转换为 `float64`；
- latest applicable revision 为 `partial`、`rejected`、`withdrawn`，或 endpoint/任一 FX leg 为 late、missing、unavailable 时，active dependency 必须 fail closed，并返回 reason codes 与 calculation dependency；不得采用更早 complete revision；
- 明确配置为 0% target 且不受正下限重新激活的成员可以不读取收益历史，但仍保留在结果中；任何正权重/正风险预算成员都不能以“排除资产”或 warning 方式绕过 canonical dependency 失败；
- successful solve/backtest 返回 `allocation_research_market_data.v1` manifest，列出锁定窗口、instrument set 以及实际采用的 quote/FX dependencies，用于复算和数据修订影响分析。

## 11. Scenario P&L 口径

### 11.1 情景冲击对象

情景可以定义在以下层级：

- instrument
- taxonomy group
- selected planning taxonomy node
- factor bucket

若同一情景不是 instrument-level，则需通过映射规则下沉到 instrument。

### 11.2 单头寸 scenario P&L

对头寸 `i` 和情景 `s`：

$$
ScenarioPnL_{i,s} = MV_{i}^{base} \times ShockedReturn_{i,s}
$$

### 11.3 组合 scenario P&L

$$
ScenarioPnL_{p,s} = \sum_i ScenarioPnL_{i,s}
$$

默认情景收益率使用 **总 NAV** 作为分母：

$$
ScenarioReturn_{p,s} = \frac{ScenarioPnL_{p,s}}{NAV}
$$

这条规则必须固定，不能随 `weight_basis` 自动切换。

说明：

- 该归一化规则只适用于 `realized_risk_share / target_risk_share` 比较；
- canonical target drift 不属于该规则，仍固定按 `portfolio_nav`；
- 但 scenario loss 反映的是整个组合会亏多少，所以分母固定为 `total_nav`。

### 11.4 Group contribution in scenarios

group 级情景损益：

$$
ScenarioPnL_{group,s} = \sum_{i \in group} ScenarioPnL_{i,s}
$$

group 级贡献比例：

$$
ScenarioContributionShare_{group,s} = \frac{ScenarioPnL_{group,s}}{ScenarioPnL_{p,s}}
$$

若组合总情景损益接近 0，则 contribution share 记为 `unavailable`。

## 12. 输出状态与质量标记

所有关键分析结果应支持以下状态：

- `complete`
- `partial`
- `unavailable`

并附带至少以下质量字段：

- `as_of_date`
- `valuation_currency`
- `weight_basis`（若适用）
- `coverage_ratio`
- `stale_price_count`
- `benchmark_overlap_ratio`（若适用）
- `calculation_version`

### 12.1 物化快照刷新一致性

daily snapshot、holding snapshot、contribution slice 是可重建的读模型，不是源事实。刷新链路必须满足：

- 交易、账户、共享行情或 FX 变化先写入源事实，再把受影响组合标记为 `stale`；
- 每次 stale 标记生成新的 `refresh_request_id`，用于表示“至少需要覆盖到这次事实更新之后”；
- 物化 payload schema 或核心计算口径改变时必须提升 `calculation_version`，让旧 read model 自动失效并重建；不能在 daily snapshot、contribution regroup 或 calculation detail 聚合中长期保留旧字段兼容逻辑。
- migration `20260713_0034` 以不可逆 breaking change 删除旧 mixed `coverage_state`，将既有 snapshot / contribution slice 标记为 `coverage_contract_rebuild_required` 并把 calculation state 置为 stale；升级后必须运行 canonical rebuild。旧 payload 不允许被读路径翻译或兜底为新契约。
- migration `20260713_0035` 不可逆地失效没有 metrics method version 的历史 Research 回测指标与该 run 的全部旧工件引用；它保留 run 请求、求解详情和回测路径，不在运行时翻译旧年化或旧 Sharpe，也不继续暴露无法随数据库备份验证的历史文件路径。需要新指标或工件时必须按 v2 方法重跑。
- 组合 summary、Overview 与默认 Holdings 的 as-of 选择必须复用同一套 latest fresh complete snapshot 规则；不得在不同读路径各自实现日期兜底，也不得因浏览器日期、服务器当前日期或部分资产已更新而改变组合层窗口终点。
- 同一组合的物化刷新串行执行；如果刷新期间又收到新的 `refresh_request_id`，当前计算结果不得把状态置为 `current` 或清空 `dirty_from`，必须继续按最新事实再计算一轮；
- 邮件、文件或批量行情导入完成后按资产/组合去重触发刷新，不应在单个数据点写入过程中反复启动组合重建；
- 读路径可以在发现状态不是 `current` 时触发 repair refresh，但 repair 必须复用同一套串行 claim 逻辑，不能并行删除/插入同一组合的物化表。

## 13. 首版明确不锁死的高级口径

以下能力首版可以保留扩展位，但不在当前 canonical 规格中锁死：

- multi-period Brinson linking 的完整变体
- factor model ex-ante risk
- option Greeks
- CVaR / Expected Shortfall 的复杂参数化定义
- fixed income full accrual engine
- liquidity-adjusted scenario engine

## 14. 维护规则

任何计算相关改动必须同步检查：

- [02_GIPS_ALIGNMENT.md](./02_GIPS_ALIGNMENT.md) 是否仍准确描述 GIPS-informed 方法边界；
- Portfolio README 的计算层当前口径是否需要更新；
- 后端 Research / Performance / Risk 测试是否覆盖了新增或修改的失败状态；
- 前端是否显式展示 `partial`、`unavailable`、`comparator missing`、`insufficient-history` 等关键状态。
