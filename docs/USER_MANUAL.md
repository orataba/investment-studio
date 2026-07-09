# Portfolio Operations Workbench 使用手册

## 1. 系统入口

在公司网络环境内使用浏览器访问以下地址：

- 总入口 / Platform：`http://172.188.30.166:3100/`
- Watchlist：`http://172.188.30.166:3101/`
- Portfolio：`http://172.188.30.166:3102/`

推荐使用最新版 Chrome 或 Edge。三个入口可以分别加入浏览器收藏夹。日常使用只打开 `3100 / 3101 / 3102` 这三个前端地址；`8100 / 8101 / 8102` 是后端 API 端口，除维护排查外不直接访问。

Platform 是系统总入口，首页会展示可进入的业务应用。Watchlist 和 Portfolio 也可以通过各自地址直接打开。页面打不开时，先确认电脑已连接公司网络，再刷新浏览器；仍不可用时记录访问地址、发生时间、浏览器、页面截图或错误提示，交给维护人排查。

维护人可用以下健康检查地址判断服务是否存活：

- Platform API：`http://172.188.30.166:8102/api/health`
- Watchlist API：`http://172.188.30.166:8100/api/health`
- Portfolio API：`http://172.188.30.166:8101/api/health`

健康检查只用于判断服务状态，不用于业务操作。

## 2. 系统分工

Portfolio Operations Workbench 分为三块：

- Platform：共享资产库和行情主数据。维护 instrument、identifier、NAV、close price、FX 等基础事实。
- Watchlist：基金和指数观察列表。用于资产池筛选、分组、单资产详情、研究标签、监控和导出。
- Portfolio：组合管理工作台。用于账户、交易、持仓、绩效、风险、分类体系和研究调仓。

三块系统共用同一套 instrument 主档。基金、指数、债券、股票、现金、汇率等资产都应先在 Platform 建档，再被 Watchlist 或 Portfolio 引用。不要在不同模块里重复创建同一资产，也不要用临时名称绕过主档管理。

核心数据分四类：

- 资产主档：名称、资产类型、币种、ticker、ISIN、内部 ID、生命周期状态。
- 市场行情：基金 NAV、含分红 NAV、指数 close、股票/债券价格、FX spot。
- 组合事实：账户、交易、现金流、持仓、成本、费用、税费、内部转账。
- 分类体系：Watchlist fund/index taxonomy、研究标签、Portfolio planning taxonomy、sleeve tree、TargetSet。

系统不会用示例数据补空，也不会自动猜业务分类。字段为空通常代表数据确实缺失、口径不适用、日期不重叠或刷新尚未完成。

## 3. 数据维护原则

资产、行情、交易和 taxonomy 都是正式业务事实，保存前应确认含义、日期、币种和数值。不要把测试资产、测试交易或临时标签录入正式 watchlist / portfolio。需要试验功能时，先与维护人确认是否有专用测试组合或测试列表。

同一资产只维护一个 canonical instrument。新增前先在 Platform 搜索名称、ticker、ISIN 或其他 identifier。若发现重复资产，不要继续新增下游数据，应先合并或停用重复记录。

日期要按实际业务日期填写。基金 NAV 使用净值日期，指数 close 使用收盘日期，交易使用 trade date 和必要的 settlement date。Portfolio 默认交易时间为 Asia/Shanghai 12:00；同一天多笔交易需要体现先后顺序时，应填写准确 trade time。

币种要与资产、账户和现金流一致。当前 Portfolio 交易币种支持 USD、HKD、CNY。跨币种组合需要维护 FX，否则组合市值、绩效和风险会出现缺口。

## 4. Platform 使用

### 4.1 进入共享资产库

打开 `http://172.188.30.166:3100/`，进入 `Database Dashboard`。这里管理所有模块共用的 instrument 和市场数据。

常用操作顺序：

1. 在搜索框输入资产名称、ticker、ISIN 或内部编号。
2. 检查搜索结果中是否已有同一资产。
3. 不存在时新建 instrument。
4. 填写 instrument type、名称、币种和 identifier。
5. 录入或导入市场数据。
6. 回到 Watchlist 或 Portfolio 引用该 instrument。

### 4.2 新建 instrument

新建时必须至少维护一个 identifier，并指定一个 primary identifier。常见填写方式：

- 基金：instrument type 选 `fund`，币种按基金净值币种填写，identifier 可填写 ticker、ISIN、Bloomberg code 或内部代码。
- 指数：instrument type 选 `index`，币种按指数点位或报价币种填写，identifier 可填写 ticker 或指数代码。
- 股票：instrument type 选 `equity`，币种按交易报价币种填写。
- 债券：instrument type 选 `bond`，identifier 优先使用 ISIN 或内部债券代码。
- 现金：instrument type 选 `cash`，用于组合现金账户或现金桶，不作为普通证券交易标的。

命名应使用公司内部可识别的正式名称。名称中不要混入临时判断、评级、日期或个人备注；这些信息应放在 Watchlist 研究字段或 Portfolio notes 中。

### 4.3 维护市场数据

基金优先维护 NAV 或 NAV with dividend：

- 普通净值使用 NAV。
- 需要体现分红再投资或总回报时使用 NAV with dividend / total return NAV。
- 同一日期同一口径不要重复录入多个冲突值。

指数维护 close 序列：

- close 用于 Watchlist 指数 performance/risk 字段。
- close 日期越完整，YTD、MTD、1M、年化收益、回撤、波动率和 Sharpe 越可靠。

FX 维护 spot：

- 组合中出现 USD、HKD、CNY 跨币种资产时，需要对应 FX。
- Platform 维护 FX 后会触发下游组合刷新。

行情录入后，下游不会立即“猜算”缺失历史。若需要完整区间分析，应补齐区间内必要日期的数据。

### 4.4 刷新和生命周期

资产有 active / archived 生命周期。已停用或重复资产应 archive，不应删除业务历史。Archive 后下游新搜索一般不再优先展示，但历史记录仍可追溯。

手动刷新会请求系统重新读取或计算相关市场数据。刷新后 Watchlist recalc 和 Portfolio snapshot 可能需要一点时间完成。若页面仍为空，检查 source settings、行情覆盖日期、资产类型和下游刷新状态。

## 5. Watchlist 使用

### 5.1 打开观察列表

访问 `http://172.188.30.166:3101/`。默认入口会进入 watchlist 选择或默认列表。每个 watchlist 是一个资产池，可用于基金池、指数池、策略候选池或专项研究池。

Watchlist 管理“哪些资产进入观察范围”和“如何展示这些资产”，不负责创建资产主档。新增条目前，应先确保资产已在 Platform 存在。

### 5.2 添加资产

在列表页面搜索共享资产并加入当前 watchlist。搜索不到时回到 Platform 检查 instrument 是否存在、是否 active、identifier 是否正确。不要用相似名称新建重复资产。

加入后，资产会出现在主表中。若指标为空，先看资产详情页的数据状态，再看 Monitoring 是否提示缺失行情、缺失标签或 recalc 失败。

### 5.3 主表浏览

主表支持分页、筛选、排序、列配置、分组和导出：

- 分页：用于浏览大列表。
- 排序：点击列头或使用当前 view 的默认排序。
- 筛选：按字段值缩小结果范围。
- Data & Columns：选择展示字段和列顺序，`Name` 固定为第一列。
- Group By：按 taxonomy、属性或可分组字段查看资产分布。
- Download：导出当前筛选和排序后的全量结果，不只导出当前页。

导出前应确认当前筛选、排序、分组和日期区间符合沟通口径。导出的 CSV 可用于复核和会议讨论，但不要把导出文件当作新的事实来源再回灌系统。

### 5.4 视图和字段

Watchlist 的字段来自 field registry 和 instrument attributes。字段可能只适用于特定 instrument type。例如基金字段不一定适用于指数；指数 performance/risk 字段依赖 close 序列；基金收益风险字段依赖 NAV 或 NAV with dividend。

列配置用于当前分析任务，不改变底层数据。若某个字段长期需要在团队视图中出现，应创建或调整 view，而不是让每个人临时改列。

### 5.5 分组和 taxonomy

`Group By` 支持可写 taxonomy 或只读字段分组。可写 taxonomy 分组支持拖拽资产到目标分组，并把分类结果写回后端。只读指标分组只用于查看，不能拖拽修改。

分类应由研究或业务负责人明确维护。系统不会自动推断 fund/index taxonomy。遇到分类为空，应补标签或补 taxonomy assignment，而不是等待系统自动填充。

### 5.6 基金详情页

基金详情页包含 Overview、Quote、Performance、Risk、Exposure、Ratings、People、Strategy、Price、Documents、Research、Monitoring 等信息区。实际可见 tab 会根据数据覆盖情况和资产类型变化。

常用区域：

- Overview：查看基金名称、identifier、核心状态、taxonomy、关键指标和数据 freshness。
- Quote：查看 NAV 序列、NAV with dividend、分红、默认 benchmark 和图表。
- Performance：查看增长曲线、年度收益、trailing returns、peer comparison 和区间表现。
- Risk：查看波动率、回撤、风险结构、rolling volatility / Sharpe 和 benchmark 对比。
- Research：维护人工评级、研究结论、research overview 和时间线 notes。
- Monitoring：查看需要关注的缺失数据、标签、刷新任务和监控判断。

在 Quote / Performance / Risk 中选择 benchmark 后，图表会展示基金与 benchmark 的相对表现。benchmark 本身也必须有可用行情，否则只显示基金自身数据。

### 5.7 指数详情页

指数详情页是轻量工作面，重点是 Overview、Performance、Risk。指数不使用基金专属的 people、strategy、fee 等字段。指数可基于 close 序列计算：

- YTD、MTD、1M 和年化收益。
- 最大回撤、当前回撤。
- 年化波动率、Sharpe。
- 与 benchmark 或其他资产的图表对比。

若指数指标为空，优先检查 Platform 中该指数是否维护 close，日期是否覆盖分析区间，Watchlist recalc 是否完成。

### 5.8 Monitoring 和 recalc

Monitoring 页面用于发现需要处理的问题，包括缺失报价、缺失标签、需要刷新、正在运行或失败的 recalc job。处理顺序：

1. 先补 Platform 主档或行情。
2. 再补 Watchlist taxonomy / attributes。
3. 触发或等待 recalc。
4. 回到主表或详情页确认字段恢复。

recalc 失败时，不要手工改计算结果。应查看失败资产、失败字段和错误信息，补齐源数据后重新计算。

## 6. Portfolio 使用

### 6.1 打开组合

访问 `http://172.188.30.166:3102/`。进入 Portfolio 后选择具体组合。组合页面通常包含 Overview、Holdings、Accounts、Transactions、Performance、Risk、Taxonomies、Research。

Portfolio 以交易和行情为事实来源。持仓、市值、绩效、风险和研究结果都由这些事实计算得出。不要直接修改结果表来“修正”展示值，应回到交易、账户、行情或 taxonomy 源头处理。

### 6.2 Accounts

Accounts 管理组合内账户。账户类型通常包括证券账户和现金/存款账户。证券账户可设置默认 settlement cash account，用于买卖、分红、债券兑付等交易的现金结算。

账户关键字段：

- account name：账户名称。
- account type：账户类型。
- currency：账户币种。
- institution：机构或平台。
- default settlement cash account：证券账户对应的默认结算现金账户。
- cost basis method：成本法，支持 `fifo` 和 `moving_average`。
- allowed instrument types：账户允许持有的资产类型。
- status：账户状态。

成本法影响成本、已实现/未实现盈亏和 lot 展示，不影响 TWR 绩效口径。修改成本法前应确认历史交易是否需要重算。

### 6.3 Transactions

Transactions 是组合事实入口。新增交易前确认账户、资产、币种、trade date、settlement date、数量、价格、费用和税费。交易保存后会生成 ledger postings，并影响持仓、现金、成本和组合快照。

支持的交易类型：

- `buy`：买入证券。需要证券账户、instrument、quantity、price、gross amount，可填写 fee / tax，需要结算现金账户。
- `sell`：卖出证券。需要有足够持仓，系统会校验 trade date 时点的可卖数量。
- `dividend`：基金或股票分红。需要 instrument，可填写 entitlement date，进入结算现金账户。
- `dividend_reinvestment`：分红再投资。需要已有持仓、instrument、quantity，可选 price，不走结算现金账户。
- `coupon`：债券票息。需要债券 instrument，可填写 entitlement date。
- `interest`：现金账户利息。不关联 instrument。
- `return_of_capital`：资本返还。需要基金或股票 instrument，不能超过对应持仓成本基础。
- `maturity_redemption`：债券到期兑付。需要债券 instrument 和 quantity。
- `fee`：费用。可作为现金账户费用，也可关联证券账户和 instrument。
- `tax`：税费。规则与 fee 类似。
- `deposit`：外部入金，只用于现金账户。
- `withdrawal`：外部出金，只用于现金账户。
- `fx_conversion`：现金账户之间换汇。需要 counterparty account、counter amount 和 fx rate。
- `transfer_out` / `transfer_in`：内部账户转账，由 Internal Transfer 表单成对创建。
- `opening_balance`：期初现金或期初证券持仓。

买入、卖出和证券期初持仓要求 gross amount 与 quantity × price 一致。卖出、到期兑付和仓位转移会校验可用数量。分红、票息、费用、税费等若带 entitlement date，日期不能晚于 trade date。settlement date 不能早于 trade date。

内部转账用于组合内账户之间移动现金或持仓。现金转账填写金额；持仓转账填写 instrument、quantity，必要时填写 transferred cost basis。内部转账会生成 transfer in/out 配对记录，不应手工分别录入两边。

删除交易会影响由该交易派生的现金、持仓、成本和绩效。删除内部转账配对时，系统会按 transfer group 处理对应记录。删除前应确认该交易不是后续复盘口径的一部分。

### 6.4 Holdings

Holdings 展示当前或指定 as-of 的持仓、数量、价格、市值、权重、成本和未实现盈亏。持仓来自交易、行情和账户计算，不手工录入。若持仓数量不对，优先检查 Transactions；若市值不对，优先检查 Platform 行情和 FX；若成本不对，检查账户成本法和历史交易顺序。

### 6.5 Overview

Overview 是组合默认首页，展示组合市值、TWR index、回撤、sleeve 结构、top holdings 和 benchmark 对比。页面只在数据完整时展示计算结果；缺少 fresh snapshot、关键行情或 FX 时，不会用部分数据硬算。

常见使用方式：

- 查看组合总市值和近期变化。
- 检查 top holdings 权重是否异常。
- 选择 benchmark 对比 1W、MTD、YTD、since inception、当前回撤和最大回撤。
- 查看 sleeve 结构是否偏离规划。

### 6.6 Performance

Performance 用于真实组合区间复盘。核心口径是日频 TWR、期间 P&L、资金流、贡献拆分和分组归因。

常用区域：

- Performance chart：查看组合 NAV/TWR 曲线、drawdown 和 benchmark。
- Calculation：查看区间收益、现金流、fees、taxes、income、FX gains、P&L。
- Groups：按 instrument、account、instrument type、currency、taxonomy 等维度分组。
- Contribution：查看贡献明细和日历视图。
- Boundary holdings：检查区间开始和结束持仓。
- Entries / drilldown：追踪计算条目。

Performance 反映真实历史组合，不是当前权重假设。若与 Risk 或 Research 结果不同，先确认三者口径：Performance 是历史事实，Risk 是当前持仓风险，Research 是规划求解和假设回测。

### 6.7 Risk

Risk 是当前权重口径的风险工作台，使用当前非现金持仓权重和资产历史收益窗口。它适合回答“现在这组持仓的风险结构如何”，不适合替代历史绩效归因。

常用内容：

- rolling volatility / Sharpe：看风险和风险调整收益随时间变化。
- correlation matrix：看资产间相关性。
- current drift：看当前权重相对目标或分类的偏离。
- risk contribution：看各资产或分组对组合风险的贡献。
- benchmark：选择可用 benchmark 后比较风险曲线。

风险结果依赖资产收益序列。缺少历史行情、日期不重叠或缺少 FX 时，结果可能为空或覆盖不足。

### 6.8 Taxonomies

Taxonomies 管理组合分类和目标体系。Research 的求解结构来自这里。

常见对象：

- planning taxonomy：组合规划分类体系。
- sleeve tree：资产或资金桶的层级结构。
- assignment：把 instrument、account 或 cash bucket 分配到 taxonomy node。
- TargetSet：目标权重或风险预算集合。
- default planning taxonomy：Research 默认使用的规划体系。

维护顺序：

1. 建立或选择 planning taxonomy。
2. 维护 sleeve tree。
3. 给资产、账户或现金桶做 assignment。
4. 建立 TargetSet。
5. 检查目标权重或风险预算是否完整。
6. 设置 default planning taxonomy。
7. 进入 Research 运行求解。

若 Research 报 scope 无成员、目标缺失或 top sleeve bounds 无法满足，通常要回到 Taxonomies 检查 assignment、TargetSet 和树结构。

### 6.9 Research

Research 用于 target solve 和假设回测。它不改变真实持仓，结果用于研究和调仓建议。

运行前需要确认：

- planning taxonomy：使用哪套规划分类。
- scope：求解整个组合还是某个 sleeve。
- target dimension：使用 scope default、weight 或 risk budget。
- capital mode：`unit_notional`、`fixed_gross`、`target_volatility` 或 `volatility_cap`。
- lookback days：风险和收益窗口长度。
- calculation frequency：auto、daily、weekly、monthly。
- missing return policy：`strict` 或 `complete_case_drop`。
- rebalance frequency：`1W / 1M / 3M`。
- benchmark：选择用于回测比较的资产。
- top sleeve bounds：顶层 sleeve 的权重约束。
- frozen sleeves：不参与调整的 sleeve。

运行结果包含：

- 当前上下文：当前持仓、scope、规划分类和风险窗口。
- solved target weights：求解后的目标权重。
- target weight gap：当前权重与目标权重差距。
- risk budget diagnostics：风险预算达成情况。
- top sleeve bounds status：约束是否满足。
- backtest curve：按所选频率调仓后的假设曲线。
- drawdown、YTD、Calmar、volatility、Sharpe 等指标。
- benchmark comparison：与 benchmark 的同期表现。
- relative metrics：组合相对 benchmark 的收益、波动和信息比率。

Research 失败不会覆盖上一轮成功结果。失败后先看页面上的失败原因，再检查 Taxonomies、TargetSet、缺失收益政策、行情覆盖、benchmark 和 top sleeve bounds。不要为通过求解而随意放宽约束；约束应反映真实投资纪律。

## 7. 典型工作流

### 7.1 新基金进入观察池

1. Platform 搜索基金 ticker / ISIN / 名称。
2. 不存在时新建 fund instrument。
3. 维护基金币种、primary identifier 和 NAV / NAV with dividend。
4. Watchlist 搜索该 instrument 并加入目标 watchlist。
5. 在基金详情页维护 taxonomy、research overview、manual rating 或 notes。
6. 触发或等待 recalc。
7. 在主表配置字段、排序和筛选，导出或保存 view。

### 7.2 新指数用于比较

1. Platform 新建或确认 index instrument。
2. 维护 close 序列。
3. Watchlist 加入指数池，检查 Performance / Risk。
4. Portfolio Overview、Performance、Risk 或 Research 中搜索并选择该指数作为 benchmark。
5. 若 benchmark 没有曲线，检查 close 日期是否覆盖组合或回测区间。

### 7.3 录入一笔买入交易

1. Portfolio 进入对应组合。
2. Accounts 确认证券账户和默认结算现金账户存在。
3. Platform 确认买入资产已建档并有行情。
4. Transactions 新增 `buy`。
5. 填写 account、instrument、trade date、settlement date、quantity、price、gross amount、fee、tax、currency。
6. 保存后查看 ledger posting、Holdings 和 Overview。
7. 若快照 stale，等待刷新完成。

### 7.4 做一次组合区间复盘

1. Portfolio 进入 Performance。
2. 选择起止日期和 benchmark。
3. 查看 TWR、drawdown、P&L、资金流。
4. 进入 Calculation / Groups 查看按资产、账户、币种或 taxonomy 的分组贡献。
5. 检查 Boundary holdings，确认区间开始和结束持仓合理。
6. 导出需要沟通的表格。

### 7.5 做一次研究调仓

1. Taxonomies 检查 planning taxonomy、assignment 和 TargetSet。
2. Research 选择 scope、target dimension、capital mode、lookback、frequency、missing return policy。
3. 选择 benchmark 和 rebalance frequency。
4. 检查 top sleeve bounds 和 frozen sleeves。
5. 运行 Research。
6. 阅读 solved weights、target gap、risk diagnostics、backtest 和 benchmark comparison。
7. 把结果作为调仓讨论依据，不直接当作已执行交易。

## 8. 常见问题

- 找不到资产：先到 Platform 搜索名称、ticker、ISIN；确认 active 状态和 identifier；仍没有再新建。
- Watchlist 指标为空：检查资产类型是否适用、行情是否覆盖、recalc 是否完成、字段是否属于该 instrument scope。
- 指数没有风险指标：检查 close 序列是否足够长，日期是否连续或覆盖所选区间。
- 基金图表没有 benchmark：benchmark 资产需要可用 NAV 或 close，且日期与基金有交集。
- Portfolio 市值不对：检查交易数量、价格、FX、持仓日期和 snapshot 状态。
- 成本不对：检查账户成本法、历史交易顺序、期初持仓和 return of capital。
- 卖出保存失败：通常是 trade date 时点持仓数量不足，或账户/资产不匹配。
- 组合 as-of 没更新：等待 snapshot 刷新；若长期 stale，检查最近行情、FX 和失败任务。
- Performance 与 Risk 不一致：Performance 是历史组合复盘，Risk 是当前权重风险，不是同一口径。
- Research 失败：检查 TargetSet、scope assignment、缺失收益政策、benchmark 行情和 top sleeve bounds。
- 导出结果和页面不一致：确认导出前的筛选、排序、日期和 view 是否与页面一致。

## 9. 使用规范

正式数据保存前先确认来源。行情要保留可追溯 provider 或来源说明；交易要保留业务票据或记录；taxonomy 和评级要有研究依据。

不要为了让图表显示而录入猜测值，不要用零值代替缺失值，不要把不适用字段强行填满。缺失就是重要信息，应通过补源数据或调整分析口径解决。

不要删除已有历史事实来修正展示。需要更正时，优先按业务规则修改原交易、补录冲销或维护正确行情；不确定时先记录问题，不直接操作正式数据。

导出结果适合会议沟通、数据复核和阶段性留档。投资判断仍需结合数据来源、覆盖期、缺失提示、人工研究和风险约束。
