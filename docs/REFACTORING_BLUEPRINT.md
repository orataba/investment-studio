# Portfolio Operations Workbench 重构蓝图

状态：已批准执行
基线日期：2026-07-13
目标用户：基金经理本人，以及未来少量基金经理同事

## 1. 产品定位

本项目是面向基金经理的投资研究与组合运营工作台，不是覆盖公司全流程的企业级 PMS、OMS 或合规系统。

系统必须优先帮助基金经理完成以下闭环：

1. 维护可信的资产、基金、账户与交易事实。
2. 跟踪组合表现、风险、资产配置和策略执行状态。
3. 记录研究证据、人工判断、决策和后续复核。
4. 在数据不足或口径不成立时明确拒绝给出伪精确结论。
5. 让任何关键数字和判断都可以解释、复算和追溯。

系统不追求复制完整机构投资流程。审批链、交易通道、全公司合规、客户核算、企业数据湖和通用工作流引擎不在当前目标内。

## 2. 已确认的业务边界

### 2.1 基金研究

- 普通候选基金通常只有产品基本信息、管理人和历史净值，系统必须允许在稀疏数据下工作。
- 已投基金可以维护更多文档、访谈、尽调、人员、策略、费用和定期复核资料。
- 底层持仓穿透不是通用必备能力。只有数据可得且分析确有价值的主观股票基金，未来才提供可选的持仓分析。
- 不为无法穿透的基金虚构资产暴露，不把“缺少持仓”视为系统异常。

### 2.2 基金评级

- 内部基金评级只能由基金经理或研究人员人工形成。
- 晨星只作为方法论参考，不接入晨星评级接口，也不预留不存在的数据源。
- 定量分析只提供证据：同类分位、风险调整收益、回撤、稳定性、费用和数据覆盖度等。
- 定量证据不得自动映射为内部评级、投研观点或 `High Conviction`。
- 每次评级必须包含生效日期、分析人、简要依据、置信度和下次复核日期，并保留修订历史。

### 2.3 ETF 轮动

- 本项目不承担 ETF 轮动策略的研究回测。
- ETF Strategy Live 当前由独立项目承担，尚未稳定前不并入 Workbench，也不在本项目复制其数据模型。
- Workbench 当前不承担 ETF 轮动的信号、目标、偏离或风险跟踪；外部项目继续是唯一真源。
- 普通组合仍以 taxonomy 作为分类、归因和跟踪主轴；只有被显式归类为 ETF 轮动策略组合的组合是例外，
  不强制 allocation taxonomy、风险预算、`TargetSet` 或 policy drift。仅仅持有 ETF 不会让普通组合自动获得该豁免。
- 未来集成必须尊重 ETF 轮动的实际业务对象，不能为了复用普通组合或资产配置模型而强制映射。
- 资产配置研究仍可以运行历史政策模拟，但必须明确标为模拟，不能冒充 ETF 策略回测。

### 2.4 交易纠错

- 用户可以修改录错的历史交易，前端不要求使用复杂的会计冲销操作。
- 每次修改和删除在底层必须形成修订记录，保留修改前内容、修改后内容、操作者、时间和原因。
- 组合计算只读取当前生效版本；审计与复盘可以还原任意修订前状态。
- “可修改”不等于原地覆盖或无痕物理删除。

## 3. 第一性原则

### 3.1 事实、计算、判断、决策分层

| 层级 | 例子 | 约束 |
|---|---|---|
| 事实 | 交易、净值、汇率、基金资料、文档 | 有来源、时间和修订历史 |
| 计算 | TWR、波动率、归因、风险贡献、同类分位 | 有方法版本、输入清单和可靠性状态 |
| 判断 | 内部评级、研究观点、风险接受 | 人工形成，有依据、分析人和复核日 |
| 决策 | 调仓、继续持有、暂停策略、接受偏离 | 关联当时事实、计算和判断 |

任何自动计算都不能悄悄升级为人工判断；任何人工判断也不能伪装成客观事实。

### 3.2 有限数据下的专业性

专业性不等于字段越多越好。对只有净值和管理人信息的基金，系统只输出这些数据能够支持的分析，并给出：

- 数据截至日期；
- 有效历史长度；
- 频率和币种；
- 同类样本数；
- 缺失项；
- 结论可靠性。

少于最低历史长度、时点不一致或同类样本不足时，指标显示 `Unavailable`，不得通过任意年化、旧值携带或跨口径拼接制造精确数字。

### 3.3 可纠错且可复现

业务操作可以保持灵活，但底层采用 revision model：

- 当前表或当前视图负责高性能读取；
- revision/event 表负责完整历史；
- 计算运行保存输入版本和方法版本；
- 更正事实后，受影响的计算明确失效并重新生成。

### 3.4 人机分工

系统负责一致计算、异常发现、证据组织和提醒；基金经理负责评级、主观观点、约束选择和最终决策。未来 Copilot 只能生成带来源的草稿，不能自动改变评级、目标权重或交易事实。

## 4. 目标架构

长期收敛为单一 Workbench 产品和清晰的后端领域模块，避免继续维护三个相互重复的业务应用。

```text
React Workbench
  ├── Instruments & Data Quality
  ├── Fund Research
  ├── Portfolio & Performance
  ├── Portfolio Risk
  └── Allocation Research

Workbench API
  ├── identity_audit
  ├── instrument_data
  ├── fund_research
  ├── portfolio_ledger
  ├── portfolio_analytics
  └── allocation_research

Durable Worker
  ├── market data ingestion
  ├── materialization and invalidation
  ├── valuation/performance/risk calculations
  └── backup and restore verification

PostgreSQL
  ├── immutable/revisioned facts
  ├── versioned research and decisions
  ├── calculation runs and manifests
  ├── replaceable read models
  └── jobs, audit and outbox
```

部署保持简单：一个前端入口、一个 API 进程、一个 worker、一个 PostgreSQL 和一个备份任务。不引入微服务、Kafka 或分布式基础设施。

在迁移完成前，现有 `platform / watchlist / portfolio` 可以继续作为物理边界，但新代码必须按上述领域模块组织，禁止新增跨 app 重复计算。

`Research` 只可以是前端导航分组，不能成为一套包办所有投资方法的通用领域模型。当前
Portfolio 中名为 `Research` 的能力，业务含义严格限定为 `Allocation Research`；基金研究
仍归 `Fund Research`，ETF 轮动研究和实盘状态仍归外部项目。

## 5. 核心领域模型

### 5.1 Fund Research

- `FundResearchAssessmentRevision`
  人工评级修订；保存 rating、as-of、analyst、rationale、confidence、next review 和 supersedes。
- `FundResearchNote`
  研究事件、访谈、风险变化和后续动作。
- `FundDocument`
  原始资料及来源日期；已投基金按需丰富。
- `QuantEvidenceSnapshot`
  量化证据快照。只保存可解释指标、同类样本和可靠性，不保存自动投资观点。
- `PeerGroupVersion`
  明确同类定义和成员版本；同类排名必须基于同一时点与同一口径。

### 5.2 Portfolio Ledger

- `TransactionIdentity`
  稳定且不可复用的交易身份；修改和删除都不改变 `transaction_id`。
- `TransactionRevisionGroup`
  一次业务变更的原子审计边界；保存来源、操作者、原因和记录时间，并可包含多笔交易修订。
- `TransactionRevision`
  append-only 保存每次 baseline、create、amend 和 delete；payload hash 与前序 revision 形成可验证链。
- `transaction_current`
  面向日常读取和当前计算的只读视图，只投影每个稳定身份的最新非 tombstone revision。
- `ReconciliationRun`
  对账批次、差异和处理状态；尚未实现，属于 Phase 3B。
- `ValuationRun` / `PerformanceRun`
  固化输入 manifest、方法版本、估值日期和可靠性状态；通用 run / manifest registry 尚未实现，属于下一阶段。

第一轮不强制把用户操作改成会计式 reversal；审计模型必须先于复杂会计工作流。

### 5.3 Portfolio Risk 与 Allocation Research

两者相关但不是同一个 bounded context：

- `Portfolio Risk` 基于真实持仓和市场数据描述当前风险；`RiskModelVersion` 与
  `RiskPolicy` 分离，`RiskLimit`、`RiskBreach`、`RiskOverride` 形成监控闭环。
- 除明确标记为 ETF 轮动策略组合的特殊组合外，组合 taxonomy 是分类、绩效归因、风险聚合和
  日常跟踪的标准主轴；它不因为 Research 分域而消失。
- `Allocation Research` 研究资产配置问题；`AssumptionSet`、`InvestmentView`、
  `ConstraintSet`、`AllocationRun` 形成证据链。
- taxonomy 中的 planning 扩展、`TargetSet`、风险预算、权重缺口和 policy drift 只属于
  `Allocation Research` 或已绑定 allocation policy 的组合；普通分类 taxonomy 不受此限制，
  但这些 planning 对象不能成为 ETF 轮动组合的必填字段。
- 历史政策模拟统一标注数据范围、当前政策假设和实施成本是否纳入，并命名为
  `Policy Replay`，不得泛化成策略回测。

不创建通用 `StrategyResearch`、全局 `strategy_type` 或包含大量 nullable 字段的策略表。
不同研究模块只共享身份、证据来源、as-of、版本、作者和决策追踪等稳定基础语义。

当前 Portfolio 数据模型尚无可审计的组合策略分类字段，因此本阶段不在运行时代码中实现
“ETF 组合自动豁免”分支。系统不得根据组合名称、组合 ID、持仓品种或 `instrument_type=etf`
推断豁免；普通组合持有 ETF 时仍完整使用 taxonomy。现有 ETF 轮动组合暂由人工运营边界
管理，不把其无 planning taxonomy 的现状泛化为规则。未来只有在需要自动执行该边界时，
才引入一个最小、显式、人工维护并可审计的 portfolio operating profile；它只表达组合运营
类型和适用能力，不承载策略参数，也不演变成通用策略研究模型。

### 5.4 ETF Strategy Live 的未来集成边界

当前不在 Workbench 创建 ETF Live 表、API 或 read model，也不与外部项目共享数据库或双写。
未来如果评估集成，只先共享稳定事实：canonical instrument identity、外部 strategy/version
identity、source event id、observed/effective timestamps、provenance，以及确有需要的
portfolio/account/transaction linkage。策略内容保持 strategy-native，不预设目标权重、
风险预算或 drift。

集成必须依次通过阶段门：

1. **Gate A：链接或摘要。** 外部项目稳定运行后，Workbench 最多提供入口链接或只读状态摘要。
2. **Gate B：只读投影。** 外部项目具备稳定身份、时间语义、幂等导出、全量回放，并连续通过多个完整实盘周期核对后，才允许单向 API/event ingestion。
3. **Gate C：领域迁移。** 只有重复事实已经造成明确维护成本，且迁移演练、备份恢复、全量重放、逐笔/逐日 reconciliation 和回滚全部通过，才考虑把该 bounded context 搬入 Workbench；切换后必须保持单一写入源。

### 5.5 Canonical Market Data 与时间语义

市场数据必须把稳定序列身份、业务观察、进入系统的修订和计算发布分开：

- `QuoteSeries` 表示稳定的 canonical 序列，身份至少包含 instrument、metric family、quote basis 和 currency。
  canonical 序列允许经过明确 adoption policy 接受不同导入批次；单条 observation 的 source reference 只是
  provenance，不能误当成稳定 source-series identity。
- `QuoteObservation` 表示某个 series 的业务观察日；同一 series/date 只有一个逻辑 observation。
- `QuoteObservationRevision` append-only 保存每次值、来源状态和 provenance 修订。历史更正必须 supersede 旧 revision，
  不能原地覆盖；旧数据无法证明 ingestion 时间时保持 unknown，禁止用迁移或计算时刻冒充。
- quote role 由版本化 selection policy 解析。valuation、trading、total return、chart 和 reference 只能绑定或严格
  解析到一个完整 series identity；缺少 binding、出现歧义或 status 不合格时返回 unavailable，不按数组顺序或最新日期兜底。

所有面向用户的状态同时区分四个正交维度：

1. observation freshness：相对 requested/effective as-of、预期频率和交易日历评价业务观察是否及时；
2. ingestion freshness：分别记录 source publish、last attempt、last success、ingested/revised 时间；
3. calculation freshness：由 input manifest、methodology version 和当前 canonical revision 精确判断 current/stale；
4. metric reliability：只描述样本覆盖、数学可解性和业务适用性，不与前三种时间状态混用。

历史 as-of 的旧 observation 可以完全可靠；今天看到的最新 observation 也可能 late。`complete`、`fresh`、`current`
不得互相替代。

## 6. 计算准确性与可靠性规范

所有面向投资决策的指标返回统一 envelope：

```text
value
as_of
currency
frequency
methodology_version
input_manifest_id
reliability_status
reliability_reasons[]
```

必须统一执行：

- 不同币种、quote basis、source adoption policy 和 status 不得拼成同一序列；
- 陈旧估值遇到重大外部现金流时，TWR fail closed；
- 少于规定历史长度时不展示年化收益、Calmar 等长期指标；
- 同类分位要求相同 as-of、币种、频率、收益口径和最低覆盖率；
- 任何事实修订都通过依赖 manifest 精确失效相关结果；
- 后端是财务计算唯一真源，前端只负责展示和交互。

## 7. 性能与稳定性目标

不以缓存掩盖错误数据模型。优先顺序为正确索引、批量读取、预计算 read model，最后才是短期缓存。

初始服务目标：

- 常用列表和组合概览：本机 P95 小于 500ms；
- 基金详情首屏：P95 小于 800ms；
- 普通写操作：P95 小于 500ms，不含异步重算；
- 大计算提交后异步执行，接口立即返回 run id；
- 同一输入和方法版本产生确定性结果；
- worker 任务具备幂等、租约、心跳和失败重试；
- API readiness 同时检查数据库、迁移版本和 worker 心跳。

## 8. 执行计划

### Phase 1：删除误导性评级并建立人工评级修订链（已完成，2026-07-13）

- 删除 `canonical-score/v2` 自动评级。
- 删除 rating recalc job、score snapshot 和 rating read model。
- 将 watchlist 的 `overall_rating` 改为明确的 `research_rating`。
- 新建人工评级 revision 表和 current 查询。
- 将评级保存从覆盖 JSON 改为创建修订。
- 列表只展示当前人工评级；详情 Research 同时展示 current rating 与 append-only revision history；没有人工评级时显示 Unrated。
- 量化表现和风险继续作为独立证据展示。

验收结果：迁移 `0026` 已部署；任何行情重算都不能改变内部评级；评级修改有历史记录且可在 Research 查阅；列表与详情的 current rating 一致。

### Phase 1B：删除通用基金穿透脚手架（已完成，2026-07-13）

- 删除 Watchlist 当前通用 holdings/exposure facts、快照、read model、recalc branch、API 和前端 Exposure tab。
- 不迁移由简单平均生成的 duration/YTW 等无效派生值。
- 未来只有在主观股票基金确有可靠持仓数据和明确分析需求时，才重新建设独立、可选的持仓分析能力。
- 组合层的基金 NAV 风险分析继续保留，不依赖底层持仓穿透。

验收结果：迁移 `0027` 已部署；普通基金缺少持仓不再产生空模块、错误告警或伪造 exposure；
运行时不存在 dormant 通用穿透代码。Portfolio 真实持仓、除显式 ETF 轮动策略组合外所有普通组合的
taxonomy、基金 NAV 表现与风险分析均保持不变；普通组合持有 ETF 不构成豁免。

### Phase 2：时间、序列和指标可靠性（2A / 2D 已部署；2B / 2C 继续）

#### Phase 2A：Canonical Fact Foundation

- 重建 `QuoteSeries / QuoteObservation / QuoteObservationRevision`，市场数据修订 append-only。
- 分离 canonical series identity 与 observation provenance；禁止跨 identity 拼接或按输入顺序选点。
- selection policy 版本化，由 registry 提供唯一 resolver；删除各 app 自己的 basis/default fallback。
- late historical revision 即使不改变 latest as-of，也必须推进 data version 并失效依赖结果。

2026-07-13 部署结果：canonical quote series、append-only observation revision、严格
`total_return` role、canonical FX identity / resolver、显式币种约束和三套 consumer policy
已上线；正式库迁移到 Instrument `0011` 并通过真实数据审计。

#### Phase 2B：Calculation Run 与不可变发布

- 新建 calculation run 和 input manifest，保存 exact series/revision、policy revision、FX legs 和方法版本。
- 拆分 observation、ingestion、calculation freshness；旧的 last-good 结果只能明确标为 stale，不能伪装 current。
- 修复 stale valuation + external flow 的 TWR：现金流边界没有 fresh complete valuation 时整段 fail closed，
  经过 fresh re-anchor 后新窗口才可恢复计算。
- Portfolio GET 不再同步写库或以同形 live fallback 隐藏 materialization 失败。

当前进度：Portfolio daily snapshot 已保存 quote / FX dependency manifest、独立
NAV / book-P&L / TWR 状态并支持全量确定性重建；Watchlist performance/risk pair 已有
一致 fingerprint 和两轮 cohort convergence。通用的 versioned calculation-run 表、跨领域
input-manifest registry，以及所有 GET 读路径彻底移除同步 materialization，仍属于下一批工作。

#### Phase 2C：Contract Convergence

- 所有关键响应使用 typed lineage/reliability envelope 和稳定 reason code；`null` 不得转成财务零值。
- OpenAPI 生成共享 TypeScript client，删除三个前端手写的重复 DTO。
- calculation current 必须等于当前 input fingerprint 与 methodology version，不以“有日期/有快照”代替。

当前进度：Portfolio risk workspace、performance comparison/reliability、holdings unrealized
P&L 和 Watchlist analytics 已返回 typed coverage / reliability / lineage；旧混合 coverage、裸
AUM 和误导性 Reference Tape 已删除。OpenAPI 生成 TypeScript client 以及三个前端剩余手写
DTO 的统一仍未完成，不以手写兼容 alias 作为过渡方案。

#### Phase 2D：One Calculation Authority

- 删除 Portfolio 前端的 covariance、correlation、rolling risk、risk contribution、annualization 等计算引擎。
- 删除 Watchlist 前端的 return、volatility、Sharpe/Sortino、drawdown 和 relative metric 重算。
- 前端只做展示格式、图形坐标和交互；所有投资指标由后端 canonical calculation 输出。

2026-07-13 部署结果：Portfolio Risk、benchmark comparison、年化资格、Calmar、组合/分组
收益风险和 unrealized P&L 均已回收到后端；Watchlist 前端只消费 authoritative analytics，
浏览器仅保留图形坐标、筛选、排序和格式化。前端 authority-boundary tests 防止公式回流。

验收：不满足数据条件时明确 unavailable；同一输入只有一个后端结果；同日修订、晚到旧日期修订、policy-only
变更、计算竞态和方法升级均能产生可解释的 revision/manifest/state 变化。

### Phase 3：交易修订、计算 manifest 与对账（3A 已部署；3B 下一阶段）

#### Phase 3A：版本化 Decimal 交易账本（已实现）

- stable transaction identity 与 append-only revision/group 已落库；新增、修改、删除分别形成
  `create / amend / delete` revision，修改前版本不被覆盖。
- `transaction_current` 是当前计算与日常查询的只读投影；delete 使用 tombstone，身份和历史链继续保留。
- quantity / price 使用 `NUMERIC(38,12)`，金额与费用使用 `NUMERIC(38,8)`，FX rate 使用
  `NUMERIC(38,18)`；API 以 plain-decimal JSON string 接收和返回，不允许浮点输入或静默舍入。
- revision group 保存 actor、change reason、source kind 和 recorded time；update/delete 以 expected revision
  id/number 实施 optimistic concurrency，旧版本写入返回显式冲突。
- 内部转账的 transfer-out / transfer-in 在同一 revision group 原子创建和删除，不能单腿编辑或删除。
- Portfolio migration `20260713_0036` 已把旧 mutable `transaction_record` 捕获为 baseline revisions、校验
  payload hash、删除旧表并安装 append-only enforcement；旧 binary-float 尾差按目标 scale 显式
  round-half-even，受影响字段和 transaction ids 写入 migration evidence；该迁移有意不可逆。
- Portfolio migration `20260713_0037` 把 payload hash 重算与内部转账整对生命周期提升为 PostgreSQL
  数据库约束；伪造 hash、孤腿/不镜像转账、单腿删除、转账 amend 和历史 group 重用均在数据库提交时拒绝。
- Portfolio migration `20260713_0038` 将 0036 基线组的实现来源名精确归一化为 canonical `migration`，并让
  数据库、service 与 API 共用封闭的 actor source/type 语义；不保留旧值 alias 或 serializer 兜底。
- 交易修订会把受影响的组合快照状态标记为 stale；当前仍按已有 dirty boundary 重建，不宣称已经具备
  通用 calculation input manifest 的逐 revision 精确依赖失效。

Phase 3A 验收已完成：API/UI 可以修改历史事实并查看 History；旧 revision、actor、原因、变更字段和
mutation group 可追溯；并发修改不会静默覆盖，删除不会物理抹除历史。

#### Phase 3B：Calculation input manifest 与 reconciliation（下一阶段）

- 建立 valuation / performance / risk calculation run 与统一 input manifest，保存采用的 transaction
  revision、quote/FX revision、policy revision 和 methodology version。
- 用 manifest 依赖替代粗粒度 stale 范围，在事实修订后精确定位、失效和重建受影响结果。
- 增加导入批次、reconciliation run、差异分类、处理状态和可重复回放。
- 把现有 snapshot 与 Research 的局部 dependency payload 收敛到可查询的跨领域 manifest registry；
  在该 registry 完成前，不把现有 fingerprint 描述为完整 calculation provenance。

Phase 3B 验收：可以解释某个历史绩效数字采用了哪些交易、行情、FX、policy 和方法版本，并能通过
reconciliation 证明导入前后与重算前后差异已处理。

### Phase 4：Allocation Research 语义收窄（暂缓）

当前不改数据库、路由或 UI，避免在更高优先级的账本与数据可靠性重构期间扩大变更面。
未来以一次不兼容重构完成：

- 将 Portfolio 当前泛化的 `Research` 明确重命名为 `Allocation Research / Allocation Lab`。
- 将现有 backtest 明确重命名为 `Policy Replay`。
- 将 `Current Drift` 收窄为 `Allocation Policy Drift`，只对绑定有效 allocation policy 的组合出现。
- UI、API、DTO、表和文档一次性改名，不保留 `research` 双轨别名。

验收：普通组合继续用 taxonomy 跟踪；ETF 轮动组合不被强制配置 taxonomy、风险预算、目标和 policy drift。

### Phase 5：架构收敛与多用户基础

- 统一 Web shell 和 API contract，逐步淘汰 app 间重复骨架。
- OpenAPI 生成 TypeScript client，禁止手写重复 DTO。
- 在已落地的交易 optimistic concurrency 与 client-asserted 本地 actor 基础上，加入认证身份、RBAC，
  并把审计查询扩展到其他关键事实；本地 actor 不能冒充认证主体。
- 使用 transactional outbox 处理重算和通知。
- 建立 PostgreSQL CI、关键 E2E、加密异地备份和恢复演练。

验收：可以安全开放给少量同事，且并发修改不会静默覆盖。

## 9. 明确不做的事情

- 不建设通用基金穿透平台。
- 不在本项目中建设 ETF 策略回测器。
- ETF Strategy Live 稳定且通过集成阶段门前，不合并、不复制模型、不双写。
- 不接入不存在的晨星评级接口。
- 不为了旧 API、旧字段或临时自动评级保留兼容分支。
- 不引入微服务、消息总线和企业级工作流引擎。
- 不让 AI 自动作出评级、调仓或风险接受决策。
- 不在数据不足时用默认值、旧值或任意年化填补结果。

## 10. 每阶段质量门槛

每个阶段必须同时满足：

1. 数据库从空库可以升级到 head。
2. 迁移后的真实库通过领域不变量审计。
3. 后端单元测试、PostgreSQL 集成测试和前端测试通过。
4. 三个现有前端在收敛完成前均可生产构建。
5. 关键计算含确定性 golden tests 和边界条件测试。
6. 删除被替代的表、接口、字段、文档和前端路径，不留下双轨实现。
7. 记录性能基线，禁止出现无法解释的显著退化。
