# Investment Studio — Watchlist

资产详情的五类工作面统一为总览、风险监测、投资研究、投资观点、资料。总览合并价格／净值与完整绩效及历史风险特征；风险监测提供适用的日度 VaR／ES 和既有事项跟踪，资料保留各资产真实档案与原编辑能力。旧表现和基金档案链接分别进入总览、资料。当前边界见 [详情架构](./docs/ASSET_DETAIL_ARCHITECTURE.md) 与 [收益序列契约](./docs/RETURN_SERIES_CONTRACT.md)。

Watchlist 承载观察池、单资产研究、监控和本地物化 read model。当前主路径支持 `public_fund / private_fund / etf / equity / index / crypto`；其他 Instrument Data 类型不会被塞入通用详情页。

## 职责与边界

- `Watchlists`：名单、view、筛选、排序、分组和导出；CSV／Excel 导出当前筛选与搜索命中的完整集合，保留当前排序、列和币种，不受屏幕分页或已展开行数限制；
- 自定义观察列表可在入口菜单或列表设置中重命名，保留原 ID、成员和视图；
- 管理员与协作成员可创建共享列表，所有成员均可见，只读成员不能修改；列表卡片和设置显示创建人，旧列表未记录的创建人保持未知；
- `Instrument Detail`：按资产类型进入基金、ETF、股票、指数或加密资产现货工作面；
- 投资观点：PM 的投资判断、逐条研究笔记和修订历史；
- 投资研究：专属研究员的资料档案、持续底稿、当前结论、重要风险机会与后续进展；
- 研究助手：各页面可打开的上下文对话抽屉，读取研究档案和风控结论，支持提问、上传证据和共同样本比较；
- 标的风险：复核线、自动观察、人工事项和持续跟进；Portfolio 复用同一套记录；
- `Monitoring`：来源新鲜度、字段缺失、重算状态和复核到期；
- `Recalculation`：从 Instrument Data canonical facts 构建 Watchlist-local snapshots 和 rows。

Watchlist 直接读取 `instrument_data` 共享事实和 `data_ingestion` 的公开证券目录，不直接修改 canonical identity、quote/NAV/FX 或 corporate actions。列表添加并行查询已登记资产和市场证券目录；已登记资产通过一次身份查询跨全部支持类型检索，不逐类型请求完整行情档案；用户明确添加未登记股票或 ETF 时，经共享证券桥接调用 `investment-studio data` 的登记与行情刷新命令，再加入列表。搜索不登记资产，目录缺失或失败保留提示，批量文件添加仍只匹配已登记标的。添加窗口保持固定外框，结果在内部滚动；当前名单的已有成员直接标明且不可重复选择，成员身份来自名单详情而非筛选后的表格；已有成员的投资状态通过列表批量动作或标的设置单独修改。添加后保留窗口、投资状态选择并回到搜索输入。添加可同时选择全局投资状态，留空则保留原状态；列表勾选标的后可一键设为在投或拟投，表头全选可处理当前筛选下全部行。成员、状态与新成员的后台重算任务原子提交，HTTP 请求不等待全历史重算；已有任务运行时保留后继任务，不能让重算占用导致添加失败。单独修改投资状态不触发行情重算，列表的显示、筛选、排序与分组直接消费当前属性事实。其余行情导入、修订和刷新由同一数据维护入口负责。Watchlist taxonomy、研究事实、名单和 read model 只写入 `watchlist` schema，不进入共享资产数据或 Portfolio。

关键运行约束：

- 系统名单为 `全部标的 / Index / All 公募 / All 私募`；全部标的自动收录有效登记且有 Watchlist 工作面的资产，自定义名单只组织成员；打开或切回列表页面时先同步成员及数量，再更新行数据，保留当前筛选和视图；
- 首屏并行读取目录、字段和保存视图；分类选项不阻塞主表，筛选值在打开菜单时按需加载。主要页面加载在内容区域居中呈现，搜索与保存进度邻近对应操作；切换名单时取消旧请求，迟到结果不覆盖当前页面；
- 列表创建、复制、移动、重命名、视图保存与文件添加的系统失败沿用统一右侧通知；名称校验、未匹配的文件标识及领域数据提示保留在相应表单中，失败不清空输入或关闭窗口；
- 当前路由的页面代码与会话校验并行下载，业务页面及数据请求仍等待授权。列表和详情不提前下载未打开的研究助手；列表风险抽屉也在打开时加载，进度留在抽屉内容区；
- 每个 instrument 使用自己的 calculation-series as-of，名单不伪造共同计算日；
- 混合资产名单只暴露对全部当前类型都有定义的字段；
- Group By 只组织视图，不隐式修改 taxonomy 或研究事实；成员新增、移除及文件添加后保留当前仍有效的命名视图，不回到默认视图；
- 窄窗口用原生选择器切换列表，创建和全部列表入口保留；列表目录将名称与证券数量保持在同一行，排序按钮保留键盘访问；640px 及以下在名称下重复呈现当前视图前两项已选收益及其原始日期提示，缺失值保留为空；不改列顺序、字段选择或保存的命名视图；
- 系统默认视图平铺资产，分类与研究阶段作为列；用户保存的分组视图保留；
- canonical recalc 只消费 Instrument Data 中符合 status、currency 和 quote policy 的观测；条件不足时结果为 unavailable；
- Instrument Data 通知用于降低延迟，不是正确性边界；durable worker 会主动对账 source generation；
- 列表使用与完整图表同次发布的紧凑投影，预先计算现有 `1D / 1W / 1M / 1Y` 四个走势窗口；详情保留完整历史。窗口计算、收益口径和来源时点一致，列表请求不读取完整历史再裁切；当前列菜单仍只开放 `1M` 走势；
- 收益、风险和 benchmark 只在合同规定的端点、共同样本及 return semantics 下发布，不做静默回退。

Watchlist schema `20260920_0059` 新增列表图表投影。统一发布流程须先停写、备份并迁移，再执行 `backend/scripts/refresh_release_watchlists.py`，为包括归档资产在内的既有图表补齐投影；该补齐只派生已存图表，不获取行情，也不推进原来源时点。`audit_live_data.py` 的 `watchlist_screener_projection_ready` 检查通过后才可恢复服务与调度。运行时若投影缺失，列表明确返回暂不可用并持久化入队修复，worker 对账也会发现缺口；不在页面请求中现场重算。字段与升级合同见 [数据模型与 API](./docs/DATA_MODEL_AND_API.md#45-read-models)。

## 投资观点、投资研究与研究助手

投资研究面向公司股票、股票 ETF、行业指数和股票公募，分为「总览 / 标的动态 / 重要研究主题 / 量化指标」。总览突出建议与依据；标的动态筛选外部新闻公告、市场观点、财报业绩会和会议纪要，不混入研究修订或系统日志；主题聚焦具体催化剂并支持人工记录和删除；量化指标展示实际数据与历史，模型暂停时仍可用。

主研究员按实际问题直接研究或调用动态、主题、量化专员；相互独立的工作并行，主题专员可继续拆分一层独立主题。各角色使用不同上下文、skill 和工具，原生子会话不复制父聊天，成果留在同一运行中。主研究员统一整合并接受独立事实核证。各部分 skill 可编辑，人工指导与研究结论分别保存。下一轮只预载持续事项、活跃主题和相关基线，原文及历史按来源和版本读取；恢复复用已完成子任务，不重复开展同一研究。

私募策略、加密及明确非权益产品的此套研究暂缓，风险监测、研究助手、数据和人工资料继续可用。共享投资研究范围为最新拟投或在投、有效登记且适用的权益标的；自动风控独立检查范围。模型开关关闭时所有模型入口暂停，确定性数据继续运行。本地模型默认暂停，云端自动任务在配置的非高峰窗口领取；普通助手随时响应。

PM 投资观点保留作者、日期及引用。研究助手从当前部分、事件或主题打开并携带上下文；保存观点、操作主题或发布团队研究需要明确指令。组合助手可读取真实持仓，并按已保存目标调用组合优化，不能更改账本、目标或生成交易。重要风险经独立风控判断后按严重性进入标的、组合和首页，不把普通数据缺口变成首页警报。

完整合同由[投资研究系统](../../docs/INVESTMENT_RESEARCH_SYSTEM.md)维护，接口与数据对象见[数据模型与 API](./docs/DATA_MODEL_AND_API.md)。运行器、MCP、量化隔离和服务部署分别见 [Architecture](../../docs/ARCHITECTURE.md#deepseek-harness)、[量化环境](../../infra/research-quant/README.md)和[云端部署](../../docs/SERVER_DEPLOYMENT.md#controlled-watchlist-research-refresh)。

模型共享约束在 [research_core.md](./backend/config/research_core.md)，各标的可编辑 skill 的默认值在后端 `research_skills.py`；二者分别持有安全/证据边界与研究分工，不重复存储用户偏好。

研究、风险及助手使用仓库外 `portfolio-copilot.env` 和统一 Harness 模型配置。`DEEPSEEK_BASE_URL` 是追加 `/chat/completions` 前的 API 命名空间；通用网页搜索独立使用 [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)，不再经过模型网关。专用密钥在私有 `watchlist.env` 配置 `INVESTMENT_STUDIO_WATCHLIST_TAVILY_API_KEY`，只由 Watchlist API 持有，不传入 Harness 主／子代理、MCP 或计算沙箱。未配置时返回 `search_status=not_configured` 并保留覆盖缺口；共享资讯和已知 URL 原文读取继续可用。开通与密钥管理使用 [Tavily 官方控制台](https://app.tavily.com/)，配置密钥后需用公开查询完成真实调用验收，接口安装不代表真实搜索已可用。

Wind 补充研究由后端连接官方 HTTP MCP，在私有 `watchlist.env` 配置 `INVESTMENT_STUDIO_WATCHLIST_WIND_API_KEY`，凭据不进入 Harness 或模型。主研究员和专员按问题发现新闻公告、基金、股票、指数或宏观域的实际工具，读取必要资料；同轮同请求复用已留存原件，续读不重复联网。原始响应保留单位、日期和片段范围，可供隔离量化使用；供应商原件不等于自行计算或已核证结论。该入口不改正式行情、交易或账户，不提供 Wind 通用分析或交易工具，研究按具体缺口取数、不自动批量扫市场；接口可用不代表有完整覆盖或无限额度。Wind 新闻检索也不代替通用网页搜索。富途沿用[仅行情 REST 接入](../../docs/FUTU_MARKET_DATA.md)，不为研究挂载账户和交易工具。

新研究基线尚未建立时，总览显示紧凑说明；保留的历史主题明确标注尚未经新一轮复核，不能当作当前结论。预期采集与对照时钟明确显示 UTC，未记录的时间保持缺失。

基金绩效矩阵默认展示 1M、YTD、3M、6M、1Y 与成立以来，可展开全部周期；只有选择基准后才显示相对收益、Beta 等比较指标。比较读取、失败与共同样本不足分别呈现，切换基准不沿用上一基准结果。私募资料页将来源配置与当前净值实际来源分开，分别展示净值日期、来源文件夹和附件、邮件发送与导入时间，以及最近检查结果；系统资料更新时间不代替净值获取时间。

页面固定控件随界面语言切换，研究正文、引用和未提交草稿保留原文。来源目录只传轻量信息，原件按 run/source/version 查看和下载；引用不等于已读证明。收益、样本、价格与判断日期分别保留，缺失不按零显示。无实质变化不重复生成文章，运行失败不覆盖上次完成结果。

### 美股行业 ETF 数据增强

XLB、XLC、XLE、XLF、XLI、XLK、XLP、XLRE、XLU、XLV、XLY 的完整成份、公司资料、年度／季度预期与价格来自 `studio_market` 共享数值库。数据按全市场范围独立采集，查询按研究截止时点绑定不可变批次及来源；不再把明细复制进 ETF 参考资料 JSON。成份公司不因此进入观察列表。研究先检索共享文本库，再读取绑定版本原文；新在线补充也进入共享原文库，研究自身只保存引用。缺少数据或未进行检索时显示覆盖不足，不能据此认定无重大新增。

成份公司预期按每轮 FMP 采集快照留存，并按相同公司、预测财期、年度/季度、指标和币种比较。财期滚动及覆盖增减不视为上修或下修；原始预期未提供币种时依据同公司最近财报的 reportedCurrency 推断，保留财报日期、来源和推断状态；若仍未知则仅保留待核实读数，不能用报价币种替代。可比变化会进入每日分析和独立核证，只有重要变化才形成事件。预期对照界面目前支持上述 11 只美股行业 ETF；底层采集覆盖全美股，其他适用 ETF 明确显示尚未接入对照界面，已披露为商品资产的 ETF 不展示公司盈利预期入口。

每轮研究实际读取的标的资料、FMP 输入、原文证据和分析结果绑定在既有 ResearchEntry；重要事件复用 RiskCase 的 `sector:` 来源；研究维护事件与跟进安排，风险研判维护独立风险状态，价格重算与研究到期都不解除既有风险。每个事件的实质进展在既有 history_json 中保留完整当时快照，同一事实与来源不会因为日更重复追加。自动研究提交时，每个标的必须包含真实的复核回执（已复核或证据不足）；缺失即拒绝草稿并提示补齐，对话更新仍可省略。未检查、获取失败、无重大新增与持续事件分别保留。研究不执行交易。

验证：后端 `tests/test_instrument_events.py`、`test_sector_market_data.py`、`test_sector_estimates.py`、`test_sector_web.py`、`test_sector_research.py`、`test_sector_fact_review.py`、`test_research_dossier.py`、`test_research_notebook.py`、`test_risk_officer.py`，以及前端投资研究、研究档案、风控研判和研究助手抽屉的聚焦测试。

模型读取边界：持续跟踪事件和重要主题的目录仅携带当前判断、来源标识/日期及同轮原件读取入口；事件内部的重复证据快照通过精确 `event:<case>:<revision>` 版本读取，主题完整来源通过既有 `source_id` 读取，冻结原件和发布 CAS 不变。工作集不复制增量触发来源清单，改指向同轮 `incremental_trigger` 分区；`affected_event_ids` 与 `affected_theme_ids` 都是必要复核集合，未提交的其他主题不标为已复核。目录和分页读取不等于读取原件，独立复核仍检查实际证据交付。标的概览明确数值工具适用性：公募/私募净值不使用日频事件价格反应，应按真实净值区间读取固定观察，不能补造单日因果。

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
