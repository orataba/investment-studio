# Investment Studio Architecture Boundaries

## Current Topology

`Investment Studio` 包含四个业务 App。公开市场数据统一采集、留存和分发，业务判断与私人账本由各应用持有：

- `home/`
  登录与导航主页，不是业务 App，也不连接业务数据库。
- `shared-data/`
  独立的 CLI 数据维护与定时作业，没有 HTTP 服务。
- `apps/watchlist`
  公募 / 私募 / ETF / 股票 / 指数 / 原生加密资产现货 Watchlist、local detail、monitoring 与 recalc。
- `apps/portfolio`
  Portfolio / account / transaction / performance / risk / research / taxonomy。
- `apps/regime`
  Git 子模块；保留独立模型、输入物化快照、结果数据库与发布流程，供应商采集由 `studio_market` 提供。
- `apps/briefing`
  日报／周报；读取公共数值与原始资讯，使用 DeepSeek Harness 生成版本化报告。

Watchlist/Portfolio 共用 `investment_studio` 数据库，包含：

- `identity` schema：账号、团队、会话、服务凭证与身份审计
- `instrument_data` schema
- `data_ingestion` schema
- `watchlist` schema
- `portfolio` schema
- `market_data` schema：公开数值数据目录、批次、最新投影与不可变 Parquet 索引
- `market_text` schema：资讯版本、原文索引、来源健康与数据包收件记录
- `briefing` schema：报告、绑定输入和生成任务

Regime 的独立 `market_data` 数据库保存模型私有输入与运行事实，区别于 Studio 的同名 schema。
两者不共用业务事务；Regime 来源适配器读取共享 `studio_market` 数据后物化自己的输入。
云端是公开数值采集主端，本地复制公开数据；两端的账户、交易、研究判断与私有材料分别维护。
外部 Market Intelligence 只提供文本／事件包。其他项目的数据库和源代码不是运行依赖。

## 文件与运行归属

| 内容 | 本地位置 | 云端位置 |
| --- | --- | --- |
| Studio 源码 | `~/Projects/investment-studio` | `/opt/investment-studio/current` |
| 共享数据库 | PostgreSQL `investment_studio`，集群 `/opt/homebrew/var/postgresql@17` | `investment-studio-postgres` 容器，`investment-studio-postgres-data-v1` 卷 |
| 公开数值历史与资讯原文 | `~/.local/share/investment-studio/market-data` | `market.env` 中配置的本项目数据目录 |
| Watchlist 上传文件 | `~/.local/share/investment-studio/watchlist-documents` | 服务用户同名相对路径 |
| Portfolio 计算产物 | `~/.local/share/investment-studio/portfolio-research-outputs` | 服务用户同名相对路径 |
| 更新状态与锁 | `~/.local/state/investment-studio` | 服务用户同名相对路径 |
| Studio 密钥 | `~/.config/orataba/secrets/investment-studio` 与 `~/.pgpass` | `/home/investment-studio/.config/orataba/secrets/investment-studio` 与该用户 `.pgpass` |
| Regime runtime | `~/.local/state/asset-regime-dashboard-production` | `/var/lib/regime-dashboard` |
| Regime 密钥 | 独立 `~/.local/state/market-data/secrets`、`~/.config/asset-regime-dashboard/secrets` | 独立 `/etc/regime-dashboard/credentials`、`compose-secrets` |

邮件原始附件及解析证据在 `data_ingestion` 数据库中，不与 Watchlist 上传文档混放。
Regime 的物化快照、模型、运行结果归自己的 runtime；其部署包由子模块的发布流程生成，
不是从 Studio 数据库恢复。Linux Studio 服务用户为 `investment-studio`；Regime 保持独立 worker/API 身份。

不是：

- 将 `data_ingestion` 的私有采集状态、原始附件和解析证据作为跨 app 数据 API
- `watchlist` / `portfolio` 通过 app-to-app HTTP 取得 canonical 行情或复用账本、绩效计算
- 一个 app 的 detail page 直接充当另一个 app 的上下文页

公开证券 listing catalog 由数据域维护，通过 `investment_studio_instrument_core.security_directory` 的限定字段只读接口供搜索使用。消费者不依赖私有采集状态表，也不直接写目录或访问供应商。

## Runtime Boundaries

### Home

- 只处理登录、会话、业务入口地址和自身健康检查
- 只读写 `identity` 账号、团队与凭证；不导入 `studio_data` 或 `instrument-core`，不读取业务账本或供应商密钥
- `home.env` 与后台维护的 `data.env` 分开
- `home/apps.json` 定义导航项，`INVESTMENT_STUDIO_HOME_APP_URLS` 只覆盖运行地址；导航目录不承担服务发现或数据状态管理

### Data

- `shared-data/market` 统一接入 FMP、DataHub／Tushare、权威网站等公开数据与 MI 文本包；PG 管理目录，Parquet 保存数值历史，原始响应独立留存
- 公开全市场采集不以是否加入 Watchlist 为前提；分析师预期、修订财报和成份快照保留观察时钟与完整快照范围
- 直接读写 `instrument_data` 中的共享资产事实
- 直接读写 `data_ingestion` 中的邮件抓取、原始证据、解析、重试和候选路由状态
- 共享数据维护只能经 CLI 与定时任务执行，不提供 HTTP 服务
- 不依赖主页进程；暂停作业不会使业务页面失去对已有事实的读取能力

### Watchlist

- 直接读写 `watchlist`
- 直接读取 `instrument_data`
- 当前已发布范围是 `public_fund / private_fund / etf / equity / index / crypto`；加密资产现货当前数据合同为 BTC/USD，直接债券和商品不进入主工作面，其 ETF／基金／指数载体按登记类型研究
- 在本地维护自己的 read models、recalc jobs、manual profile 和产品框架
- 维护研究对话、标的资料档案、持续研究底稿、原始证据及其日期、助手运行快照和风险跟进；沿用 ResearchTopic / ResearchEntry，PM 观点与自动研究分别保存
- 每日研究与列表、可访问组合的风控研判复用既有 08:30 worker，单标的风控按需运行；研究助手可读取档案和风控结论，底稿按实际完成逐步积累，不宣称全部登记标的均已深研
- 研究工具通过显式配置的只读 API 取得 Portfolio 持仓与 Regime 状态；不取得交易写权限，不跨 schema 复制这些事实
- 研究读取共享数值库的 ETF 成份、公司资料、年度／季度预期和价格，并比较完整历史采集批次；不再把全套公司明细嵌入 ETF 参考资料 JSON。历史预期不以当前报价币种推断计价币种。
- 资讯研究优先检索 `market_text` 并读取绑定版本原文，必要时定向搜索补充。研究记录保存原文引用与时钟，缺少覆盖、日期不明、获取失败明确留存；发布时间、事件时间、观察时间和本地接收时间分别解释。

### Briefing

- 云端生成正式日报／周报，本地生成预览；角色由外部配置明确指定
- 数值变化由代码计算，报告保留各市场实际截止日；模型只能解释绑定输入
- 周报重新读取整周资讯和数值，不拼接日报；迟到资讯按观察／接收时钟进入后续报告
- 使用固定版本 DeepSeek Harness 和受限 MCP 工具；不调用 Codex CLI

### Portfolio

账本与页面分析分别拥有可重建的读模型：核算 worker 发布日度快照后，继续准备最新可靠日期的持仓分析与风险频率，页面读取持久结果并附加实时任务和衍生品状态。HTTP 与后台共用 `services/holdings_workspace.py`，业务服务不依赖路由模块。任意 Performance 区间基于同一次读取的已发布日度输入聚合，不重放全套行情或为所有区间建立缓存。数据版本、发布锁及金融响应的出口核验共同防止混合新旧来源；持久页面结果不承担鉴权或金融事实的职责。

- 直接读写 `portfolio`
- 直接读取 `instrument_data`
- 在所属应用的 schema 内维护 ledger、lots、performance、risk、taxonomy、target set、research；本地与云端分别持有各自业务数据
- Portfolio 管理分类和目标只有当前配置；自动版本用于审计与失效，修改后重述历史派生分析。Research 将当前配置冻结到每次运行，已保存结果不随以后修改漂移；金融事实与来源保留各自日期
- 在所属应用的 schema 内维护 FCN/期权不可变合约及事件交易；只有合约的 underlying / deliverable 引用 Instrument Data 市场资产
- 风险面板经 Watchlist API 读取和更新跟进记录及范围内风控研判；研判读取 Portfolio 实际持仓，市值敞口不等于风险贡献。组合风险、风险预算和研究求解仍由 Portfolio 独立计算；该连接失败不影响账本和绩效计算

### DeepSeek Harness

Harness 只负责模型循环和受限 MCP 工具调用；数据库事实、计算和结果发布仍由所属应用维护。
DSH 和传递依赖在版本部署时按 `infra/harness/pnpm-lock.yaml` 安装；三个应用共用
`infra/harness/run.sh`，请求执行不经过包管理器，也不依赖 npm 注册表可用性。

各应用 Agent、独立核证与简报校稿共用 `INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME`，不按任务阶段切换 Pro/Flash。模型与默认网关由共享配置及启动器确定；`DEEPSEEK_BASE_URL` 是 OpenAI 兼容 API 命名空间。部署使用网关中绑定 `ygyg-vip55` 分组的 key，分组由服务商侧配置，不通过自造请求头或模型名前缀传递。凭据仅保存在仓库外 `portfolio-copilot.env`。

| 入口 | 所有者与输入 | 允许产生的结果 |
| --- | --- | --- |
| 页面研究助手 | Watchlist；页面标的、观察列表或组合必须与对话关联一致 | 私人对话及精确版本引用；当前用户明确授权后，经同一核证与发布校验更新团队研究。组合资料不能发布到团队；不交易 |
| 标的研究追踪 | Watchlist；本轮绑定的标的、档案快照及取得的原始证据 | 结构化草稿经独立事实核证后发布底稿和研究事件 |
| 日报／周报 | Briefing；绑定的共享数值、资讯原文版本、覆盖与截止时间 | 结构化报告及引用；正式版仅由配置为 publisher 的实例生成 |
| 风控研判 | Watchlist；绑定的研究、价格风险与 Portfolio 只读风险上下文 | 结构化研判及原有 case_id 引用，不写 Portfolio 账本 |
| 交易截图 Copilot | Portfolio；限定组合和导入批次的证据 | 分析修订和导入预览；交易确认仍由 Portfolio 自己执行 |

Watchlist 的三个入口共用 `research_runner.py` 和对应的 Harness patch；交易截图使用
Portfolio 自己的 runner、patch 和 MCP。子进程只接收运行所需的显式环境变量，
没有数据库、行情供应商或交易提交权限。普通对话接口不直接修改系统研究档案或风控权威状态；
助手记录 PM 观点或主题须有当前消息的明确指令，保留原作者与版本。共同研究发布与风控发布
均在完成时重新检查当前团队写权限，运行中降权或撤回服务权限会阻止写入；组合研判另按实时组合 ACL。
自动研究不能经普通对话工具绕过已绑定的输入。手动与自动研究均以单个标的入队，
共用该标的的运行检查与发布锁，避免并发覆盖底稿与事件；历史批量记录仅保留读取。

部署地址与外部运行配置见 [Server Deployment](SERVER_DEPLOYMENT.md)，
投资判断、研究状态、持续验证与人机协作合同见 [投资研究系统](INVESTMENT_RESEARCH_SYSTEM.md)，
研究材料和任务流程见 [Watchlist README](../apps/watchlist/README.md)，
截图工具边界见 [Portfolio Copilot Harness](PORTFOLIO_COPILOT_HARNESS.md)。

## Same-Name Page Boundary

Watchlist 与 Portfolio 会使用相同的投资术语，但这些页面不是同一个业务对象：

- Watchlist Instrument Detail 位于 `/instruments/:instrumentId`，Performance / Risk 面向单一公募、私募或上市资产的 NAV、price、benchmark 和单资产统计。
- Portfolio workspace 位于 `/portfolios/:portfolioId/...`，Overview / Performance / Risk / Research 面向组合账户、交易、现金流、持仓、TWR、归因、风险预算和规划求解。
- 两边可以遵守相同的视觉基线，但不得互用业务计算、页面 fixture、截图或验收结果。
- 修改或验收前必须同时确认 app、URL、源码目录与 API origin；页面标题相同不能证明页面身份相同。

## Shared Layer

当前共享层包括：

### `shared-data/market`

公开数值与文本的采集、版本留存、查询、数据包和复制；详细口径、迁移范围及运行命令见
[Market Data Pipeline](MARKET_DATA_PIPELINE.md)。大规模历史仅保存一套 Parquet，不再建立完整持久化 DuckDB 副本。

### `shared-data/instruments`

承载跨 app 稳定 contract 和共享持久化 helper：

- `instrument_id`
- `instrument_name`
- identifiers
- `instrument_type`
- `currency`
- typed market data / FX
- quote selection policy
- shared store helper / db models
- 股票／ETF 目录搜索通过共享 core 只读查询 canonical 数据库公开 catalog，命中的登记身份一次批量匹配；显式登记仍走数据维护 CLI，Watchlist 和 Portfolio 不取得供应商凭据或另写登记逻辑

### `packages/runtime`

四个 Studio API 复用无业务数据的请求与操作诊断：请求关联号、数据库耗时、后台计算耗时及脱敏错误定位。该包不读取身份库或业务事实，也不持有缓存、授权和数据重算职责。浏览器经 Home 的已认证诊断入口提交数值耗时与代码位置；详细运行规则见 [Server Deployment](SERVER_DEPLOYMENT.md)。

### `packages/ui`

承载跨 app 的前端共享能力：

- language context
- language selector
- confirm dialog and modal focus management
- notice toast and download-format menu
- Sparkline and table export
- InstrumentRiskPanel 的展示和跟进交互；风险事项持久化仍由 Watchlist 所有
- RiskOfficerPanel 的研判展示与 WorkspaceTools 的页面工具入口；请求和页面范围由各应用提供
- request identity and serial task helpers

只沉淀已经在多个 app 中稳定复用的基础能力；业务布局、业务表格和领域组件仍留在各自 app。

### `instrument_data` schema

承载可跨 watchlist、portfolio 和数据源复用的市场资产主档与共享市场事实：

- instrument
- instrument_identifier
- instrument_market_data
- registry metadata

FCN 与期权合约依赖具体组合、账户、对手方和交易条款，不是共享市场资产；它们不进入 Instrument Data，也不产生 Instrument Data 行情。Instrument Data 只保存其 underlying / deliverable 等可复用证券。

`instrument_data` 的 Alembic 入口由独立的 [shared-data/instruments](../shared-data/instruments/MIGRATIONS.md) 管理，与主页无关。

## Data Ingestion Layer

### `data_ingestion` schema

承载后台摄取可重放、可审计的运行状态，不向 Watchlist 或 Portfolio 提供 canonical 市场事实：

- 每个邮箱目录及其 `UIDVALIDITY / UID` 高水位
- 邮件出现记录、附件内容哈希和消息到附件的关联
- 带 parser 版本、lease、重试与 dead-letter 状态的解析任务
- 尚未进入 Instrument Data 的 NAV 候选和原始 NAV 证据
- 供应商原样提供的现金累计净值、复权口径声明与拒绝原因

该 schema 由 [shared-data/alembic](../shared-data/alembic) 独立迁移。口令和 token 不进入数据库；附件内容、哈希和解析证据保存在受限的 `data_ingestion` 分区，不通过业务页面或公共 API 暴露。

## Storage Boundary

共享资产身份由数据库直接约束，而不是靠 app 约定：

- `portfolio.transaction_record.instrument_id -> instrument_data.instrument.instrument_id`（普通市场资产交易）
- `(portfolio.transaction_record.portfolio_id, derivative_contract_id) -> portfolio.derivative_contract_record`（FCN/期权交易）
- `watchlist.watchlist_item.instrument_id -> instrument_data.instrument.instrument_id`
- `watchlist.instrument_detail.instrument_id -> instrument_data.instrument.instrument_id`

这意味着：

- app 私有 schema 可以引用共享资产
- 共享资产重命名/修正要通过 共享资产数据 完成
- 本地脏数据会在 FK 迁移或写入时暴露，而不是长期静默漂移
- 普通交易最多引用一个 Instrument Data instrument，FCN/期权交易改为引用同 Portfolio 的本地合约；现金事实可以两者都不引用

## Data Flow

### Shared Instrument Maintenance

1. 在 `CLI 数据维护` 维护共享资产主档、identifier、价格、净值、FX
   支持手工录入、CSV/Excel 文件导入、邮件刷新，并能直接查看选中资产的共享市场数据与净值历史
2. 数据写入 `instrument_data`
3. `Watchlist` 和 `Portfolio` 直接从 `instrument_data` 读取

邮件通道先进入 `data_ingestion` 私有 ingestion state：逐目录按 `UIDVALIDITY + UID` 增量发现，仅对可能命中的邮件抓取正文与附件，并按附件 SHA-256 和 parser 版本去重。目录归档规则只是缩小候选范围；`INBOX` 仍是显式扫描目录，日频/周频只用于 freshness 判断，不能替代目录覆盖。解析结果必须先保留原始证据和匹配依据，再由严格 NAV 口径校验写入 Instrument Data。

基金在 Instrument Data 中只允许两种 NAV identity：`official_nav` 是单位净值，`total_return_nav` 是分红再投资后的复权累计净值。单位净值加历史现金分红的普通累计值不是回报指数，只能作为 摄取层私有原始证据，不能写成 `total_return_nav`。只有供应商明确提供可信复权序列，或系统拥有完整分红与再投资信息并完成可审计计算时，才写入复权累计净值；否则该字段保持 unavailable/NA，不使用单位净值、现金累计值或交易价格兜底。

Instrument Data 行情合同在数据库与 shared store 两层一致执行：point currency 必须等于 instrument
master currency，value 必须有限且大于零，status 必须 canonical；FX 还必须匹配维护中的
pair id、`fx/spot` identity 与 pair quote currency。消费者不得用 USD/base currency、另一币种
序列或本地旧行情替代不合格的 canonical observation。

项目维护的五个 FMP 外汇源使用 `24/5` 日历：周一至周五应有日频观察，缺少工作日仍阻止严格估值；周末可沿用最近有效观察，FMP 实际提供的周末报价仍按原日期读取。该日历是外汇日频可用性约定，不套用股票交易所或单一地区的假期，也不生成或补齐报价。

Instrument Data 的 `quote_selection_policy` 必须显式持久化五个非空 role；shared store 读取、更新和
各消费者都不得动态补默认 role。类型默认 policy 仅是新建 instrument 时完整写入的领域规则。

### Watchlist Read Models

1. 用户把共享资产加入 watchlist
2. `watchlist` 持有自己的本地镜像和 read models
3. stale 检查发现 canonical 数据变化时，写 durable `recalc_job`
4. 后台 worker 消费 job，刷新本地 read model 和 canonical metadata mirror

列表只读取 chart 同事务生成的紧凑投影：分类元数据与四个固定区间的 sparkline，单资产详情保留完整序列。迁移 `20260920_0059` 后，发布脚本从已有 chart 补齐存量投影；缺失时进入既有重算队列，发布审计要求补齐，不在列表请求中重复读取和压缩全历史。

Watchlist 的历史价格/NAV 计算只接受 Instrument Data 中符合 master currency 且 status=complete 的
序列，并严格按持久化 `quote_selection_policy` 选取；Instrument Data 无序列或无 policy 时结果为
unavailable。Watchlist-local `nav_fact` 只用于审计，不是行情计算输入。

### Portfolio Facts

1. 用户显式创建 portfolio / account / transaction，或用导入脚本导入
2. 普通市场资产交易引用 Instrument Data instrument；首笔 FCN/期权交易在 `portfolio` 内原子创建不可变 derivative contract，后续事件只引用同一 contract id
3. `portfolio` 只在自己的 schema 持久化账户、交易、合约、ledger 与组合计算事实
4. 普通市场资产的 valuation / holdings / charts / performance 使用 Instrument Data canonical market data；FCN/期权按事件记账，在事件之间使用成本/权利金负债口径，不进入实时定价、协方差、Risk Budget 或 Research 序列

## Non-goals

下面这些仍然不属于共享层：

- watchlist rows
- instrument detail read models
- monitoring assessments / investment research / manual profiles
- portfolios
- accounts
- transactions
- ledger postings / lots
- target sets / research runs

共享的是资产身份、经过校验的市场事实，以及解释这些事实何时应到达、来自何处所必需的非秘密 source descriptor / schedule；不是 app 业务语义。`SourceSettings` 中的 source mode、位置/profile、email rule 描述、expected frequency、market calendar 和 release lag 属于 Instrument Data 合同，因为所有消费者都需要同一 freshness/provenance 口径。真实邮箱口令、API token、抓取进程、原始附件、原始 NAV 证据、解析器、重试状态和后台维护命令仍属于 data_ingestion 私域，不得写入共享 Instrument Data 记录。

## Operational Rule

如果未来再新增功能，默认遵守这条判断：

- 如果是公开市场采集、数值历史、文本事件或公开数据包，放入 `studio_market`
- 如果是登记资产身份、经资产口径处理的行情／净值／FX，放入 `instrument-core + instrument_data`
- 如果是邮件、私有附件、净值候选解析工作流，放入 `data_ingestion` schema
- 如果是某个 app 的工作流、派生读模型、研究判断、展示状态，必须留在 app 私域
