# Investment Studio — Watchlist

Watchlist 承载观察池、单资产研究、监控和本地物化 read model。当前主路径支持 `public_fund / private_fund / etf / equity / index`；其他 Instrument Data 类型不会被塞入通用详情页。

## 职责与边界

- `Watchlists`：名单、view、筛选、排序、分组和导出；
- `Instrument Detail`：按资产类型进入基金、ETF、股票或指数工作面；
- 投资观点：PM 的投资判断、逐条研究笔记和修订历史；
- 研究追踪：专属研究员的资料档案、持续底稿、当前结论、重要风险机会与后续进展；
- 研究助手：各页面可打开的上下文对话抽屉，读取研究档案和风控结论，支持提问、上传证据和共同样本比较；
- 标的风险：复核线、自动观察、人工事项和持续跟进；Portfolio 复用同一套记录；
- `Monitoring`：来源新鲜度、字段缺失、重算状态和复核到期；
- `Recalculation`：从 Instrument Data canonical facts 构建 Watchlist-local snapshots 和 rows。

Watchlist 只读取 `instrument_data`，不修改 canonical identity、quote/NAV/FX 或 corporate actions。资产新增、行情导入、修订和刷新通过 `investment-studio data` CLI 维护。Watchlist taxonomy、研究事实、名单和 read model 只写入 `watchlist` schema，不进入共享资产数据或 Portfolio。

关键运行约束：

- 系统名单固定为 `Index / All 公募 / All 私募`；股票、ETF 和自定义名单按显式加入维护；
- 每个 instrument 使用自己的 calculation-series as-of，名单不伪造共同计算日；
- 混合资产名单只暴露对全部当前类型都有定义的字段；
- Group By 只组织视图，不隐式修改 taxonomy 或研究事实；
- 系统默认视图平铺资产，分类与研究阶段作为列；用户保存的分组视图保留；
- canonical recalc 只消费 Instrument Data 中符合 status、currency 和 quote policy 的观测；条件不足时结果为 unavailable；
- Instrument Data 通知用于降低延迟，不是正确性边界；durable worker 会主动对账 source generation；
- 收益、风险和 benchmark 只在合同规定的端点、共同样本及 return semantics 下发布，不做静默回退。

## 投资观点、研究追踪与研究助手

ETF、股票和指数详情页使用“总览 / 投资观点 / 研究追踪 / 业绩与风险”；公募和私募另有“基金档案”。投资观点保存 PM 自己的研究判断与历史，原研究资料可只读展开。研究追踪默认呈现自动研究员已准备好的当前判断、值得关注的变化和风险机会，来源与覆盖缺口按需展开。研究助手从各页面打开对话抽屉，读取当前页面和所选标的上下文，不占用独立详情 tab。对话回答、自动研究和 PM 观点分别保存，不把普通聊天回答发布成事件。

各资产共用上述框架，分析侧重点按类型区分：股票看经营与盈利，指数看规则和结构；ETF 先辨别行业股权、宽基、债券、商品或跨境敞口；公募看经理、风格、基准和已披露持仓；私募结合真实策略、净值频率、管理人材料及条款。持仓报告期、披露滞后和实际资料覆盖必须保留，不能把未披露敞口、杠杆或对冲由净值反推成事实。材料目录没有正文时不能视为已阅读。

每个已登记的支持类型都有只读研究档案入口，汇集该标的原始材料、适用研究方法、历史案例和最近完成的持续底稿。文字与文件材料复用稳定的 `dossier:{instrument_id}` 研究话题；实际正文、出处、发布日期、资料适用日期与收录时间分别保存。既有基金本地文件可读取文本或 PDF 文字层，缺失、扫描件或不支持格式明确标注；其他标的或组合私有资料不混入。研究方法是问题框架，不是事实依据。

持续底稿记录基本面与估值判断、关键驱动、待核实问题、支持和反对证据及下一步研究。未在本轮提及的旧问题保留，明确反证可以修订状态；各轮完成版本及原始依据可追溯，旧原文不会被改成今天发布。底稿随实际研究完成逐步积累，档案入口可用不代表所有登记标的都已完成深度研究，也不自动改写 PM 观点或研究方法。

DeepSeek Harness 实际可用时，Watchlist API 每天北京时间 08:30 起以最多四个并行任务，仅自动研究投资状态为“拟投”（Proposed）或“在投”（Invested）的有效登记标的，类型限于上述五类；关注、暂停、退出、未设置状态和已归档产品不自动启动研究。状态读取 Watchlist 实际保存的 coverage_status 最新记录，属于标的并在各列表间共享；状态变更在下一次自动范围读取时生效。每天先逐个处理当前可访问组合和现有列表：并行完成其中符合上述条件的成员研究，再执行该范围的风控研判，最后处理其余符合条件的标的。风控仍汇总完整列表和实际组合持仓的已留存事项，不删去其他状态的风险。包括符合条件的美股行业 ETF 在内均按标的独立日更，保留原批量运行的预期历史对照；从未研究或最久未更新者优先，重复成员按既有运行记录每日去重。失败保留原因而不反复自动重试，风控按实际可用的研究形成结论并保留缺口，不保证所有成员均已更新。服务较晚启动时继续处理，跨日仍优先未完成或最旧范围；页面显示每轮实际资料截止时间，不承诺所有标的已在同一时刻更新。其他状态仍可手动发起单标检查，最新失败不会覆盖上一版完成的研究结论。

分析复用仓库外 `portfolio-copilot.env` 的 `DEEPSEEK_API_KEY`，结合留存标的资料、公开检索和原文形成结论。网页更新时间、搜索估计日期和上传时间不代替首发时间；只有日期时保留日期，未知时不臆造具体时刻。搜索/读取失败放入覆盖缺口；当前没有 X 专用数据连接，不承诺完整新闻或社媒覆盖。

运行环境的可用性检查与启动脚本使用相同的 `INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE` 和 `INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM` 路径。生产环境须显式配置 Watchlist 自身的 `INVESTMENT_STUDIO_WATCHLIST_RESEARCH_API_BASE_URL`，以及 Portfolio、Regime 的 `INVESTMENT_STUDIO_WATCHLIST_RESEARCH_PORTFOLIO_API_URL`、`INVESTMENT_STUDIO_WATCHLIST_RESEARCH_REGIME_API_URL`。`data/research` 的方法、专属研究任务与历史案例属于运行所需源文件，随代码部署；外部 FMP 数据库另行配置。

研究助手的页面标的或组合必须与关联对话一致。研究档案、研究追踪与风控专题由各自模块维护，通用对话接口不能修改其归属或内容。研究追踪和风控只读取本轮绑定快照，不能借通用对话工具读取运行中变化的资料；单标的和批量研究共用标的范围锁，避免并发发布互相覆盖。

研究员通过 MCP `submit_research_review(result: ReviewResult)` 向 `POST /api/research/runs/{run_id}/sector-draft` 提交完整结构化结果。接口校验本轮完整标的范围、来源、时间和底稿，仅保存 `context_json.submitted_draft`；校验错误反馈给研究员修正，成功返回 `pending_fact_review`，不发布结论或事件。提交成功后，助手只确认已提交，不在最终正文再次序列化 JSON。

新研究中的底稿与候选提醒均交由独立核证，只依据本轮取得及沿用的原始证据，检查主体、数字、转载或自动汇编、方向、证据强度和研究问题的依据。即使没有事件，只要有底稿也进行复核。核证可修订底稿或剔除候选，草稿与核证结果留在同一研究记录中；独立核证成功后才由既有 `apply` 流程保存研究结论与事件，核证失败不发布未核证草稿。此流程不把旧历史摘要追溯标记为已经复核，也不保证新闻召回或模型判断完整。

研究追踪默认浏览近七日真实进展，这是展示范围，不是信息准入的时间上限。重要旧事实可补录，较早但仍有效的事项持续关注。发布、发生、收录时间分别保存，不把今天收录的旧事写成今天发生。没有重大新增时不制造提醒，也不因没有新消息自动解除原事项。量化统计和价格规则集中在“业绩与风险”。

Watchlist、Portfolio 和标的详情的顶部右侧统一放语言选择；大标题右侧按“设置 / 风险提示 / 研究助手”排列，均作用于当前对象。风险提示汇总整个列表、实际组合持仓或当前标的中仍需关注的风险、重大不确定性及价格触发，同等优先级按最近进展排列，机会和已处理事项不占默认首屏。研究追踪仍保留完整的研究结论、重要变化和风险机会。

风控研判复用上述 08:30 worker，每天为现有列表和当前可访问组合排队，依据已留存研究事项、价格触发与实际取得的持仓形成综合结论；单标的风控研判按需发起。它与标的研究分别留存输入和结论，失败保留上次完成结果。列表只是观察成员，不能冒充组合；组合使用 Portfolio 提供的实际市值、币种和净值口径，市值敞口及占净值比例不是风险贡献。没有聚合风险指标时明确缺口，不用权重推算。研究助手可读取这些结论、研究底稿及其原始依据，历史判断仍需按当时输入日期使用。风控先读取范围和实际持仓索引，再通过 `read_risk_instrument` 逐个读取绑定快照内的业绩、比较与研究员上报；避免一次发送全部原文及重复历史被 Harness 的单工具输出上限截断。标的风险在 Watchlist 与 Portfolio 共用 case_id 和来源。

组合另从同一次 Portfolio 风险上下文读取实际持仓、生产风险贡献、默认规划分类的 SAA/TAA 目标差，以及同模型重算历史持仓的 RC/相关性变化；模型隐含相关性不冒充风险页自选窗口的样本相关矩阵，历史重算也不冒充当时已保存的 PIT 预测。没有配置偏离容忍带或变化阈值时，仅据实研判，不称为已越限。

FCN/Option 使用 Portfolio 本地未平仓合约与可信详情链接，不登记为 Watchlist 标的；底层已有 canonical instrument_id 的研究风险可同时进入组合。通过 `read_portfolio_risk` 按组合模块及逐合约读取绑定快照，保留币种、报价日期、实物/现金结算、生命周期、条件交付现金与股票、实际参考资源和条款缺口；没有概率或新增衍生品定价。合约来源绑定 holding_id，组合整体事项不必制造 instrument_id。研究助手读取的风险研判沿用相同证据。

风控使用 `submit_risk_review` 结构化提交，保存前检查绑定快照中的标的、合约与来源，错误可在本轮直接反馈修正；成功运行后发布综合结论。控制台回复不再充当JSON数据，避免长段中文、引号和Markdown使整轮结果无法解析。结构与范围检查不等于金融判断已被核证，模型解释仍需内容验收。

业绩研判不依赖是否配置价格报警线，结合已观察到的月度表现、回撤、持仓剩余成本损益与实际仓位；同类直接复用基金页已登记分类及地域，默认基准复用标的设置。比较使用同币种、频率、收益口径和实际共同观察日，不从组合成员推断同类，也不声称全市场排名。邮箱私募净值按来源交易日历及披露延迟判断：显式设置优先，未声明的邮箱私募采用一个交易日；正常未披露尾部不作为过期，实际序列内部缺点仍保留。

历史参考目前包括从 Market Intelligence 保留的 XLK 14 个精选案例，来自 183 个单日跌幅至少 3% 的合并候选事件段，价格覆盖截至 2026-08-28。它没有覆盖上涨至少 3% 的样本，也不是完整独立事件全集；本次复用未重新核证原文，缺少完整历史持仓权重与原文归档。案例只用于机制、背景差异和观察点类比，不能成为当期事实、事件概率或交易规则。方法与案例位于仓库 `data/research`，原出处和局限一并保留。

### 美股行业 ETF 数据增强

XLB、XLC、XLE、XLF、XLI、XLK、XLP、XLRE、XLU、XLV、XLY 保留 FMP 数据增强。在仓库外 `watchlist.env` 中配置 `INVESTMENT_STUDIO_WATCHLIST_SECTOR_MARKET_DATABASE_PATH` 为现有 Market Research Database DuckDB 路径。Watchlist 通过短连接只读取得实际持仓、公司资料、分析师年/季预期及各自来源日期，不更新外部数据库。没有该配置仍可进行研究，但明确缺少这份持仓和预期输入；自动日更不再由指定 Watchlist 范围或 FMP 路径决定。

成份公司预期按每轮 FMP 采集快照留存，并按相同公司、预测财期、年度/季度、指标和币种比较。财期滚动及覆盖增减不视为上修或下修；原始记录缺少预测币种时仅保留待核实读数，不能用报价币种替代。可比变化会进入每日分析和独立核证，只有重要变化才形成事件。当前快照覆盖上述 11 只美股行业 ETF，其他适用 ETF 明确显示未接入，已披露为商品资产的 ETF 不展示公司盈利预期入口。

每次标的资料、FMP 输入、原文证据和分析结果保存在既有 ResearchEntry；重要事件使用 RiskCase 的 `sector:` 来源，由研究追踪独立维护，不因价格风险重算被解除。每个事件的实质进展在既有 history_json 中保留完整当时快照，同一事实与来源不会因为日更重复追加。未检查、获取失败、无重大新增与持续事件分别保留。研究不执行交易。

验证：后端 `tests/test_instrument_events.py`、`test_sector_market_data.py`、`test_sector_estimates.py`、`test_sector_web.py`、`test_sector_research.py`、`test_sector_fact_review.py`、`test_research_dossier.py`、`test_research_notebook.py`、`test_risk_officer.py`，以及前端研究追踪、研究档案、风控研判和研究助手抽屉的聚焦测试。

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
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$PROJECT_ROOT"
investment_studio_load_env_file \
  "$(investment_studio_runtime_env_file watchlist "$RUNTIME_ENV_ROOT")" \
  INVESTMENT_STUDIO_WATCHLIST_
: "${INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL:?watchlist.env must set the canonical database URL}"
PYTHONPATH="$PROJECT_ROOT/apps/watchlist/backend:$PROJECT_ROOT/shared-data/instruments/python" \
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
