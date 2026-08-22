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
- Watchlist：基金、ETF、股票和指数观察列表。用于资产池筛选、分组、单资产详情、研究标签、监控和导出。
- Portfolio：组合管理工作台。用于账户、交易、持仓、绩效、风险、分类体系和研究调仓。

三块系统共用同一套可复用市场资产主档。公募、私募、ETF、指数、现金和汇率等资产先在 Platform 建档，再被 Watchlist 或 Portfolio 引用。股票不走人工注册：Platform 定时维护美股、港股、A 股，以及 FMP 当前账户已覆盖的伦敦、Xetra、巴黎、阿姆斯特丹、米兰和瑞士主要市场目录；FMP ETF 使用同一覆盖范围并另含 Cboe BZX。用户在 Watchlist 或 Portfolio 搜索后，系统才按需建立共享 identity 并加载 EOD。FCN 和期权是组合特定合约，直接在 Portfolio 首笔交易中创建，不在 Platform 建档；它们的 underlying 或 deliverable 证券仍引用共享 instrument。直接债券不进入 Registry 或 Watchlist，当前也没有 Portfolio 债券交易入口。不要在不同模块里重复创建同一市场资产，也不要用临时名称绕过主档管理。

核心数据分四类：

- 资产主档：名称、资产类型、币种、ticker、ISIN、内部 ID、生命周期状态。
- 市场行情：基金单位净值、分红再投资复权累计净值、指数 close、股票价格、FX spot。
- 组合事实：账户、交易、现金流、持仓、成本、费用、税费、内部转账。
- 分类体系：Watchlist instrument taxonomy、研究标签、Portfolio planning taxonomy、sleeve tree、TargetSet。

系统不会用示例数据补空，也不会自动猜业务分类。字段为空通常代表数据确实缺失、口径不适用、日期不重叠或刷新尚未完成。

## 3. 数据维护原则

资产、行情、交易和 taxonomy 都是正式业务事实，保存前应确认含义、日期、币种和数值。不要把测试资产、测试交易或临时标签录入正式 watchlist / portfolio。需要试验功能时，先与维护人确认是否有专用测试组合或测试列表。

同一资产只维护一个 canonical instrument。新增前先在 Platform 搜索名称、ticker、ISIN 或其他 identifier。若发现重复资产，不要继续新增下游数据，应先合并或停用重复记录。

日期要按实际业务日期填写。基金 NAV 使用净值日期，指数 close 使用收盘日期，交易使用 trade date 和必要的 settlement date。Portfolio 默认交易时间为 Asia/Shanghai 12:00；同一天多笔交易需要体现先后顺序时，应填写准确 trade time。

币种要与资产、账户和现金流一致。当前 Portfolio 交易币种支持 USD、HKD、CNY、EUR、GBP、CHF。跨币种组合需要维护 FX，否则组合市值、绩效和风险会出现缺口。

## 4. Platform 使用

### 4.1 进入共享资产库

打开 `http://172.188.30.166:3100/`，进入 `Database Dashboard`。这里管理所有模块共用的 instrument 和市场数据。

常用操作顺序：

1. 在搜索框输入资产名称、ticker、ISIN 或内部编号。
2. 检查搜索结果中是否已有同一资产。
3. 公募、私募、指数等不存在时按运营流程新建 instrument；股票和 FMP 已覆盖的 ETF 不要手工建档，直接在 Watchlist 或 Portfolio 搜索本地 FMP 目录。
4. 填写 instrument type、名称、币种和 identifier。
5. 录入或导入市场数据。
6. 回到 Watchlist 或 Portfolio 引用该 instrument。

### 4.2 新建 instrument

新建时必须至少维护一个 identifier，并指定一个 primary identifier。常见填写方式：

- 公募：instrument type 选 `public_fund`；Tushare `.OF` 净值属于这一类。
- 私募：instrument type 选 `private_fund`；邮件净值来源属于这一类。
- 指数：instrument type 选 `index`，币种按指数点位或报价币种填写，identifier 可填写 ticker 或指数代码。
- 股票：不在这里手工注册。Watchlist/Portfolio 搜索本地 FMP 目录后，系统按交易所创建 `equity` identity，并按需回补 EOD。
- ETF：与股票使用同一操作方式，但底层仍保持独立 `etf` 类型和独立目录。FMP 已覆盖的 ETF 在 Watchlist/Portfolio 搜索后按需建档并回补 EOD；现有 A 股 ETF 可继续使用覆盖更完整的 Tushare 行情。
- 欧洲非上市基金：使用 `public_fund` 或 `private_fund`，以 ISIN/正式产品代码保持唯一 identity，并按现有 NAV 导入流程维护。当前不从 FMP 自动发现或抓取欧洲共同基金；没有具体产品和可验证数据源时不新增另一套基金管线。
- 现金：instrument type 选 `cash`，用于组合现金账户或现金桶，不作为普通证券交易标的。

不要在 Platform 为直接债券、某一笔 FCN 或期权新建 instrument。FCN/期权的合约条款、到期日、行权价、障碍条件、发行人和对手方属于 Portfolio 本地交易事实；只有其 underlying、deliverable 或实际交付的证券需要先在 Platform 建档。

命名应使用公司内部可识别的正式名称。名称中不要混入临时判断、评级、日期或个人备注；这些信息应放在 Watchlist 研究字段或 Portfolio notes 中。

### 4.3 维护市场数据

基金只维护两种 canonical NAV：

- 单位净值使用 NAV / `official_nav`。
- 需要体现总回报时，只使用分红再投资复权累计净值 / `total_return_nav`。
- 单位净值加历史现金分红的普通累计值不是复权累计净值，不得录入 total return；无法确认供应商口径或缺少完整分红再投资信息时保持空值/NA。
- 同一日期同一口径不要重复录入多个冲突值。

指数维护 close 序列：

- close 用于 Watchlist 指数 performance/risk 字段。
- close 本身不代表价格收益或全收益；必须按指数官方口径在 source settings 中维护
  `return_semantics=price_return` 或 `return_semantics=total_return`。无法确认时保持 Unknown，
  系统不会据此计算 benchmark-relative 指标。
- close 日期越完整，YTD、MTD、1M、年化收益、回撤、波动率和 Sharpe 越可靠。

FX 维护 spot：

- 系统维护 USD/HKD、USD/CNY、USD/EUR、USD/GBP、USD/CHF；其他币种不在当前 Portfolio 范围。
- Platform 维护 FX 后会触发下游组合刷新。

欧洲证券在目录搜索阶段显示交易所的默认币种，首次选中时会读取 FMP profile 确认该 listing 的实际报价币种。伦敦的 `GBp/GBX` 报价会先按 `0.01` 转为 canonical GBP 再写入价格和估值；伦敦的 USD 报价 ETF 仍保留 USD，不按交易所强行改成 GBP。

行情录入后，下游不会立即“猜算”缺失历史。若需要完整区间分析，应补齐区间内必要日期的数据。

基金 NAV 文件导入支持 CSV、TSV、文本、XLSX 和旧版 XLS，并在正式写入前展示解析预览；工作簿可包含供应商原始列和多个 sheet，后端按内容识别格式和 canonical NAV 字段。选中资产的 Market-data series 可按当前 Family/Basis 筛选下载 CSV 或 Excel，保留 `as_of_date`、metric/quote basis、原始数值精度、币种、price contract、status 和 provider。该下载是类型化行情审计文件，不等同于 NAV 导入模板；只有字段满足 NAV 导入契约的文件才能回灌。

### 4.4 刷新和生命周期

资产有 active / archived 生命周期。已停用或重复资产应 archive，不应删除业务历史。Archive 后下游新搜索一般不再优先展示，但历史记录仍可追溯。

手动刷新会请求系统重新读取或计算相关市场数据。刷新后 Watchlist recalc 和 Portfolio snapshot 可能需要一点时间完成。若页面仍为空，检查 source settings、行情覆盖日期、资产类型和下游刷新状态。

托管环境在任务加载/用户登录时先运行一次，并默认每天 `21:00 Asia/Shanghai` 自动运行统一刷新。邮件通道会扫描配置中的所有目录，`INBOX` 和产品专用目录都必须显式列入；平时按目录 UID 增量读取，不会每次全量下载邮箱。源数据刷新后还会单独协调方法版本落后的基金净值投影，这一步只读已落库证据，不重复扫描邮箱。某个目录、附件、基金或投影失败时会留下可重试状态和运行摘要，不应把“任务进程退出”误当成全部基金已经成功更新。

## 5. Watchlist 使用

### 5.1 打开观察列表

访问 `http://172.188.30.166:3101/`。默认入口会进入 watchlist 选择或默认列表。每个 watchlist 是一个资产池，可用于基金池、ETF/股票候选池、指数池或专项研究池。

Watchlist 管理“哪些资产进入观察范围”和“如何展示这些资产”。系统列表固定为 `Index`、`All 公募`、`All 私募`，分别自动同步所有 active 指数、公募和私募；股票、ETF 以及其他自定义列表只按人工添加维护。公募、私募、ETF 和指数来自 Registry；股票搜索本地 FMP 目录，首次添加时由 Platform API 按需准备共享 identity 与 EOD。

### 5.2 添加资产

在列表页面搜索资产并加入当前 watchlist。公募、私募、ETF 和指数搜索不到时，回到 Platform 检查 instrument 是否存在、是否 active、identifier 是否正确；股票搜索不到时，检查 FMP 本地目录最近一次刷新是否成功。不要用相似名称新建重复资产。

加入后，资产会出现在主表中。若指标为空，先看资产详情页的数据状态，再看 Monitoring 是否提示缺失行情、缺失标签或 recalc 失败。

`Add From File` 支持 CSV、TSV、文本和 XLSX，读取 `Identifier`、`Ticker`、`ISIN`、`Ticker / ISIN` 或 `Instrument ID` 列；没有表头时读取第一列。文件中的 identifier 会先去重并全部到共享 Registry 解析，存在未找到或不支持的类型时整批不添加，避免得到半截 watchlist。Excel 单元格必须是字面值，不能用公式生成 identifier。

### 5.3 主表浏览

主表支持分批显示、筛选、排序、列配置、分组和导出：

- 分批显示：页面先显示前 `80` 行，可继续显示更多或全部；全选只选中当前已经显示的行。
- 排序：点击列头或使用当前 view 的默认排序。
- 筛选：按字段值缩小结果范围。
- Data & Columns：选择展示字段和列顺序，`Name` 固定为第一列。
- Group By：所有 watchlist 使用同一组通用分组，可按资产类别、taxonomy 或数据新鲜度查看分布；基金风格、研究标签和投资状态不进入 Group By。
- Download：导出当前筛选和排序后的全量结果，不只导出当前页。

导出前应确认当前筛选、排序、分组和日期区间符合沟通口径。CSV/Excel 都导出当前 view 的可见字段和筛选、排序后的全量结果；涉及端点敏感指标时会同时带出 metric as-of、return kind、quote basis 和 series type。导出首列固定包含 `instrument_id`，因此可通过 `Add From File` 把这些成员加入另一 watchlist；导入只读取 identifier 列，其他分析字段不会回灌或覆盖 Registry 事实。

Watchlist 允许不同 instrument 的最新数据日期不同。`1M / 3M / YTD` 等字段各自从该行显示的 `Metric As Of` 回看，不使用名单中最晚日期统一截断。页脚会显示当前结果的 as-of 范围；若同一分组内终点不同，收益和风险平均显示 `—`，不要把它理解为 0。Peer 排名只使用同一 as-of 的可比样本。

### 5.4 视图和字段

Watchlist 的字段来自 field registry 和 instrument attributes。字段可能只适用于特定 instrument type。例如基金字段不一定适用于指数；指数 performance/risk 字段依赖 Registry 允许的行情序列；基金收益风险字段只依赖可信的分红再投资复权累计净值，缺失时为 NA，不回退单位净值。常用标的收益窗口包括 `1W / 1M / 3M / 6M / MTD / YTD / 1Y`。

列配置用于当前分析任务，不改变底层数据。若某个字段长期需要在团队视图中出现，应创建或调整 view，而不是让每个人临时改列。

### 5.5 分组和 taxonomy

`Group By` 支持可写 taxonomy 或只读字段分组。可写 taxonomy 分组支持拖拽资产到目标分组，并把分类结果写回后端。只读指标分组只用于查看，不能拖拽修改。

Watchlist 在自己的 schema 内维护多资产 `instrument_taxonomy`，Registry 不保存 taxonomy。公募与私募已经是不同的 `instrument_type`，各自在类型内使用产品/策略分类；ETF 使用 ETF 分类，股票按市场/交易所分类，指数使用指数分类。股票交易所分类由 Registry 的 canonical `exchange_code` 映射，其余产品分类仍由研究或业务负责人在 Watchlist 内人工设置。

### 5.6 基金详情页

基金详情页包含 Overview、Quote、Performance、Risk、Price、Exposure、People、Strategy、Documents、Research、Monitoring 等信息区。自动 Ratings 已移除；人工评级只在 Research 中维护。实际可见 tab 会根据数据覆盖情况变化。

基金、ETF、股票和指数详情页右上角的 `Settings` 同时维护投资状态和适用于该资产类型的 taxonomy。投资状态可选择未设置、观察、拟投、在投、暂停或退出；这些设置在 Watchlist 内按 instrument 共享，并不写回 Registry。

常用区域：

- Overview：查看基金名称、identifier、核心状态、taxonomy、关键指标和数据 freshness。
- Quote：查看单位净值、分红再投资复权累计净值、分红、默认 benchmark 和图表。
- Performance：查看增长曲线、年度收益、trailing returns、peer comparison 和区间表现。
- Risk：查看波动率、回撤、风险结构、rolling volatility / Sharpe 和 benchmark 对比。
- Research：维护人工评级、研究结论、research overview 和时间线 notes。
- Monitoring：查看需要关注的缺失数据、标签、刷新任务和监控判断。

在 Quote / Performance / Risk 中选择 benchmark 后，图表会展示基金与 benchmark 的相对表现。benchmark 本身必须有可用行情，且双方收益语义必须明确并一致；否则只保留可验证的独立展示，不计算相对统计。

基金自身指标默认截至自己的最新观测日。选择 benchmark 后，比较矩阵只使用双方日期完全相同的共同观测收盘点：双方都截到最晚共同观测日，SI 从最早共同观测日开始，中间收益也按同一对起止收盘计算。少于一个完整日历年的历史不显示年化收益。若 daily 序列缺少应有交易日，端点收益仍可用，但回撤、波动率、Sharpe 等路径风险指标会显示不可用；周末和交易所休市日不会被当成缺点。

### 5.7 ETF、股票和指数详情页

ETF、股票和指数使用轻量工作面，重点是 Overview、Performance、Risk、Price，不使用基金专属的 people、strategy、fee 等字段。它们可基于 Registry quote selection policy 选中的序列计算：

- `1W / 1M / 3M / 6M / MTD / YTD / 1Y` 和年化收益。
- 最大回撤、当前回撤。
- 年化波动率、Sharpe。
- 与 benchmark 或其他资产的图表对比。

`close / last` 只是字段身份；是否为 total return 或 price return 必须由 Registry `return_semantics` 明确声明。未声明时可以保留独立行情展示，但需要同类收益语义的相对指标会失败关闭。若指标为空，优先检查 Platform 中是否有 policy 允许的完整行情、日期是否覆盖分析区间、return semantics 是否明确，以及 Watchlist recalc 是否完成。

### 5.8 Monitoring 和 recalc

Monitoring 页面用于发现需要处理的问题，包括缺失报价、缺失标签、需要刷新、正在运行或失败的 recalc job。处理顺序：

1. 先补 Platform 主档或行情。
2. 再补 Watchlist taxonomy / attributes。
3. 触发或等待 recalc。
4. 回到主表或详情页确认字段恢复。

recalc 失败时，不要手工改计算结果。应查看失败资产、失败字段和错误信息，补齐源数据后重新计算。

## 6. Portfolio 使用

### 6.1 创建与打开组合

访问 `http://172.188.30.166:3102/`。进入 Portfolio 后选择具体组合。组合页面通常包含 Overview、Holdings、Accounts、Transactions、Performance、Risk、Taxonomies、Research。

创建组合时必须明确选择基础币种（USD、HKD、CNY、EUR、GBP 或 CHF）。基础币种决定组合 NAV、汇总市值、绩效和风险金额的统一表达口径；各账户和交易仍保留自己的事实币种，非基础币种现金与持仓按对应 as-of date 的 Registry FX 换算。应按实际投资汇报口径选择基础币种，不能因为某一笔交易使用港币就改成港币，也不能在缺少 FX 时把不同币种金额直接相加。

Portfolio 以交易和行情为事实来源。持仓、市值、绩效、风险和研究结果都由这些事实计算得出。不要直接修改结果表来“修正”展示值，应回到交易、账户、行情或 taxonomy 源头处理。

### 6.2 Accounts

Accounts 管理组合内账户。新增账户时直接选择 `Cash`、`Security`、`FCN` 或 `Option`。后三类是相互独立的持仓账户，不能在同一账户中混放；每个持仓账户必须绑定组合内同币种的 Cash 账户，用于交易和现金事件结算。

持仓账户应按“大类 × 币种”分开，例如 CNY Security、HKD Option、USD FCN。Cash 则按真实资金池和币种建立：如果三类持仓实际上共用同一个券商现金余额，可以共同绑定一个同币种 Cash；只有资金在现实中确实隔离时，才建立多个 Cash 账户。系统不会为了形式上的分类重复计算现金。

页面左侧是账户目录，右侧是所选账户工作区。账户价值、现金余额、持仓市值和待交收余额始终显示在顶部；下方视图分开处理：

- `Overview`：账户配置和交易派生结果的数量摘要。
- `Positions`：该账户当前开放持仓、成本、市值和未实现盈亏，可进入对应 Security Detail 的 Position Lots。
- `Transactions`：直接记入该账户的源交易，同时显示 trade date 和 position recognition / settlement date。基金买卖在界面中显示为 `Subscription / Redemption`，底层仍保留 `buy / sell` 交易类型。
- `Ledger`：源交易生成的只读 postings，分别列示 cash、pending settlement、quantity 和 cost basis 变动。发现错误时应进入源交易修改，不能直接改 posting。

账户关键字段：

- account name：账户名称。
- account category：`Cash`、`Security`、`FCN` 或 `Option`。
- currency：账户币种。
- institution：机构或平台。
- default settlement cash account：持仓账户对应的同币种结算 Cash 账户。
- cost basis method：成本法，支持 `fifo` 和 `moving_average`。
- status：账户状态。

账户已有交易或衍生品合约后不能更改 category；需要调整时应创建正确类别的账户并按事实迁移。账户币种在创建后固定，避免历史现金、成本、NAV 和风险换算口径发生漂移。

成本法影响成本、已实现/未实现盈亏和 lot 展示，不影响 TWR 绩效口径。修改成本法前应确认历史交易是否需要重算。

### 6.3 Transactions

Transactions 是组合事实入口。新增时先选 `Security`、`FCN`、`Option` 或 `Cash & Operations`，再从该资产自己的动作列表选择交易；不同资产不会共用一张混杂的动作菜单。确认账户、证券或合约、trade date、settlement date、数量、价格、费用和税费后保存；币种由所选账户确定，结算现金账户必须同币种。系统随后生成 ledger postings，并影响持仓、现金、成本和组合快照。

日期用途不同：证券头寸通常在 trade date 生效，结算现金在 settlement date 生效；dividend / coupon 可在 entitlement date 确认收益；deposit / withdrawal 在实际收付日进入 TWR 外部现金流。页面会分别展示这些日期，不能为了让绩效落到预期日期而改写另一种日期。

支持的资产与交易动作：

- Security：`buy`、`sell`、`dividend`、`dividend_reinvestment`、`return_of_capital`、`fee`、`tax`、`transfer_out`、`transfer_in`、`opening_balance`。买卖需要 Security 账户、instrument、quantity、price、gross amount 和同币种结算现金账户；卖出和转出还会校验可用持仓。
- FCN：`entry`、`early_exit`、`coupon`、`knock_in_close`、`knock_out_close`、`maturity_close`、`fee`、`tax`、`opening_balance`。所有动作使用 FCN 账户和本地合约；结束结果直接作为 FCN 动作选择，不再填写单独的事件类别。
- Option：`buy_to_open`、`sell_to_close`、`sell_to_open`、`buy_to_close`、`expire_long`、`cash_settle_long`、`expire_written`、`cash_settle_written`、`fee`、`tax`、`opening_balance`。所有动作使用 Option 账户和本地合约，并明确区分多头与空头方向；`Long Option Opening Balance` 只表示多头。
- Cash & Operations：`deposit`、`withdrawal`、`interest`、`fx_conversion`、`fee`、`tax`、`transfer_out`、`transfer_in`、`opening_balance`。Cash 动作不关联 instrument 或衍生品合约；换汇还需目标现金账户、counter amount 和 fx rate。

FCN 与期权使用 Portfolio 本地合约，不从 Platform instrument 列表中选择一个“衍生品资产”：

- 首笔 FCN/期权交易同时创建不可变合约，记录合约名称、币种、账户和条款；后续交易动作只选择同一 `derivative_contract_id`。
- 期权动作明确区分 `Buy to Open`、`Sell to Close`、`Sell to Open`、`Buy to Close`，并在选定合约后标明 Call 或 Put；另外分别提供 long/writer expiry 与 long/writer cash settlement。数量单位是合约张数，premium gross amount 按张数、每单位权利金和 multiplier 计算。合约不预设现金或实物结算方式。
- FCN 支持买入、coupon，以及 normal maturity、knock-in、knock-out 关闭结果。系统记录事件，不自动验证障碍是否触发，也不把交付资产与关闭事件绑定成一笔复合交易。
- FCN 合约主条款记录名义本金、年化票息率、发行日、最终观察日、到期日、发行人和对手方；每个 underlying 单独记录 Registry security、初始参考价、strike/knock-in/knock-out 百分比水平和是否可交付。
- 一行只记录一个经济事实。若实际发生期权实物交割，按“期权现金结算 + 交割日市场/参考价的独立股票买卖”录入；两者共同还原交割经济结果，但系统不建立关联。FCN 敲入后的资产接收也另录普通证券交易。
- 合约条款创建后不可修改；录错时应撤销错误交易并创建新的合约身份，不能改写历史条款。

买入、卖出和证券期初持仓以 quantity 与 gross amount 作为份额和成交金额事实，隐含成交价按 `gross amount / quantity / price scale` 计算；输入 price 可以是该隐含价格保留四位小数的展示值。系统接受精确乘积或与隐含价格四位小数一致的价格，但不会用舍入后的 `quantity × price` 反写 gross amount。普通 Registry 证券的 price scale 为 `1`；期权由本地合约 multiplier 决定。卖出、合约关闭和仓位转移会校验可用数量。分红、FCN 利息、费用、税费等若带 entitlement date，日期不能晚于 trade date。settlement date 不能早于 trade date。FCN 的建仓、退出、期初、票息及敲入/敲出必须发生在 issue date 至 maturity date（含）之间，到期事件不得早于 maturity date。

Portfolio 创建时必须确定 inception date。任何交易都不得早于该日期；所有 `opening_balance` 的 trade date 与 settlement date 固定为该日期。已有资产可保留更早的 acquisition date，零成本 opening position 允许 gross amount 为 0；除此之外只有 Option long/writer expiry 允许零 gross amount。运行期新增现金应录为 Deposit，新增持仓应录为实际 Buy 或 Transfer，不能用 opening balance 绕过外部现金流。Cash Fee / Tax 不填写 entitlement date；资产关联费用要求 entitlement date 当日存在 long position 或 written-option obligation。Option 独立 Fee / Tax 是合约级现金费用，不改变 long lot 或 writer obligation。Return of Capital 用 trade date 表示 entitlement/record date、settlement date 表示到账日。

费用应选择可证明的 fee category；来源无法分类时保留 `Unknown`，不要猜测。重复提交会通过 idempotency key 去重；若页面提示记录版本冲突，说明事实已在别处更新，应刷新后重新核对，不能覆盖较新版本。

页面右上角固定为 `Export / Import / Template / Record Transaction` 四个操作。Export 和 Template 都可选 CSV 或 Excel；Export 始终包含组合的全部交易命令，不受当前筛选影响，导出的任一格式都可再次 Import。Excel 文件使用 `Transactions` 工作表。Import 会先显示逐行和整批校验结果，只有全部通过后才能原子写入；CSV 与 Excel 使用同一字段、账户/币种规则、仓位校验和 preview digest。不要把数据库行 ID、内部 transfer legs 或页面筛选结果另做成第二种导入格式。

交易截图识别等外部系统使用 JSON Preview/Commit 接口，同样先校验、后确认并原子入账；它与 CSV/Excel 共用交易动作、账户/币种、持仓历史和来源去重规则。具体对接合同见 [Portfolio 标准交易记录 JSON 接口对接说明](TRANSACTION_IMPORT_API_GUIDE.md)。

内部转账用于组合内账户之间移动现金或持仓。现金转账填写金额；持仓转账填写 instrument、quantity，必要时填写 transferred cost basis。内部转账会生成 transfer in/out 配对记录，不应手工分别录入两边。页面或文件填写的 `source_system + external_reference` 是整笔 Transfer 的来源身份，系统只把它保存在 transfer-out 腿；导出折叠和再次导入仍会保留并查重。

Holdings 若显示 `Negative settled cash` critical alert，表示账本已有负现金但没有明确融资事实。应核对并补录缺失的 Deposit、Transfer 或真实融资记录；当前系统不会自动把负数解释为保证金融资。

删除交易会影响由该交易派生的现金、持仓、成本和绩效。删除内部转账配对时，系统会按 transfer group 处理对应记录。删除前应确认该交易不是后续复盘口径的一部分。

### 6.4 Holdings

Holdings 展示当前或指定 as-of 的持仓、数量、价格、市值、权重、成本和未实现盈亏。持仓来自交易、行情和账户计算，不手工录入。若持仓数量不对，优先检查 Transactions；若市值不对，优先检查 Platform 行情和 FX；若成本不对，检查账户成本法和历史交易顺序。

Holdings 的指标分成两种主要口径：

- 当前账面状态：Quantity、Market Value、Weight、Cost Basis、Avg Cost、Unrealized P&L。FIFO / moving average 只影响剩余成本、已实现/未实现账面盈亏和 lot，不影响组合 TWR。
- 当前持仓回看：标的 `1W / 1M / 3M / 6M / MTD / YTD / 1Y Return` 使用自身已确认 total-return series；Vol / Drawdown 使用同一收益序列。它们不读取历史买卖份额或成本。

普通 dividend / coupon 是 entitlement-date 已实现 `Income`，不进入 Unrealized P&L；分红再投资同时确认 Income 并以再投资金额建立新 lot；只有 `return_of_capital` 冲减剩余成本。

FCN 和 long option 在已记录事件之间按 transaction cost carrying；short option 以剩余 premium liability 进入 NAV。它们不接收实时行情，不计算日常未实现盈亏、协方差、Risk Budget 或 Research 序列；相关现金、费用、coupon 和已实现盈亏仍完整进入组合 NAV 与经营绩效。市场风险收益链会把衍生品现金结果、FCN coupon 和衍生品费用从风险收益分子中剔除，并把衍生品资本与本币现金一样保留在总 NAV 分母中，作为 0-return capital。

主表始终分为 `Securities`、`Derivatives`、`Cash & Settlement` 三个固定区段。`Group By` 由底层限定为只对 Securities 做 taxonomy、instrument type、currency 等二级分组；衍生品与现金不分类，Taxonomy 列显示 `N/A`。CSV/Excel 导出始终包含当前筛选和排序后的全量结果、`Category`、各级 subtotal 与 `Portfolio Total`；启用证券分组时才增加 `Group`。这是当前持仓分析文件，不是交易导入文件。

Group、Non-cash subtotal 和 `Portfolio Total` 仍然是**当前持仓篮子**：金额加总、比例用组级分子分母重算；Return 用当前 base-market-value 权重合成；Vol / Drawdown 用内部连续、起止完全一致且尾部仍新鲜的共同历史区间先生成当前权重篮子路径再算。Group 与 subtotal 使用各自篮子净市值分母，`Portfolio Total` 使用 total NAV；衍生品与本币现金在所在 scope 内按 0 return 保留。Forward RC 统一使用 total-NAV 权重，并加总相对于同一组合方差的贡献。非本币现金仍需要兑本币 FX 收益。当前成员收益或市值覆盖不足、return currency 无法统一、共同路径中间缺段或整条路径已经陈旧时显示 `—`，不剔除缺失成员后重新归一。Holding Since、Quantity、Avg Cost、Quote、Accounts、Chart、Coverage 和 Held Max DD 等没有稳定分组含义的字段只在 instrument row 展示。

`Portfolio Total` 的上述 Return 不是组合实际 TWR；组合真实历史表现仍到 Performance 查看。相同 instrument、相同 as-of 和 total-return basis 下，Holdings 行级窗口收益应与 Watchlist 相同，但两个 app 各自计算、互不调用。完整字段标准见 [Holdings 字段计算与分组标准](../apps/portfolio/docs/03_HOLDINGS_FIELD_REFERENCE.md)。

默认列表使用 compact payload；sparkline 是有界采样，打开 Security Detail 后再加载 lots、交易和完整图表。子资源尚未返回时显示 Loading/skeleton，不把 `$0.00` 当成真实数据；真正缺失或不适用的指标显示 `—`。

Security Detail 分成三个互不混杂的视图：`Overview` 展示标的行情序列、当前仓位口径和分账户持仓；`Transactions` 展示该标的截至 as-of 的已确认经济事实及持仓生效、结算日期；`Position Lots` 展示开放成本批次、剩余数量和账面盈亏。卖出匹配记录附属于具体 lot，选择 lot 后在右侧 `Matched exits` 查看，不作为独立顶层页面。

### 6.5 Overview

Overview 是组合默认首页，展示组合市值、TWR index、回撤、sleeve 结构、top holdings 和 benchmark 对比。页面只在数据完整时展示计算结果；缺少 fresh snapshot、关键行情或 FX 时，不会用部分数据硬算。

Overview 的质量提示只在检测到实际问题时出现，并给出受影响的资产/日期和处理方向。没有检测到 corporate action 问题时不会显示通用警告。若请求日期晚于最后一个可靠估值日，页面会使用后端返回的 effective as-of，并解释 clamp 原因。

常见使用方式：

- 查看组合总市值和近期变化。
- 检查 top holdings 权重是否异常。
- 选择 benchmark 对比 1W、MTD、YTD、since inception、当前回撤和最大回撤。
- 查看 sleeve 结构是否偏离规划。

### 6.6 Performance

Performance 用于真实组合区间复盘。核心口径是日频经营 TWR、期间 P&L、资金流、贡献拆分和分组归因；波动率、Sharpe、Sortino、Calmar 和风险回撤则统一读取独立的 `Market Risk Return` 链。

组合已经存在时，Performance 选择 `1 日` 到 `7 日` 表示 1 日收盘到 7 日收盘，收益从 2 日开始链接；1 日发生的交易和现金流已经体现在期初状态，不会在期间内重复计算。相同起止日是 0 长度区间。例外是请求起点正好等于 funded-segment start：组合首次入金日，或 NAV 真正归零后的再次入金日，会计入该日 BOD-to-EOD 收益。保留的现金即使无收益也仍属于组合 NAV，不算归零；后来某日新买一项资产也不算组合重新成立。要包含普通买入日至收盘的收益，需要把起始日选为前一日。若跨越连续零 NAV 的无资本空档，系统不会伪造零收益并强行链接，而会把整段 TWR 标记为不完整，归零前后分别计算。MTD、QTD、YTD 分别从上月末、上季末、上年 12 月 31 日的收盘状态开始；目标日休市时，组合使用该日完整 EOD 状态，标的和 benchmark 使用不晚于目标日的最近有效收盘。

从未入资的空组合没有收益分母，因此即使已有零 NAV 快照也显示不可用，不显示 0%。开始日期晚于结束日期时，页面会直接提示区间无效；请求结束日期晚于最后可靠估值日时，页面保留原请求日期，同时明确显示实际计算到哪一天以及收缩原因。Performance、Calculation、Contribution 和 Groups 使用同一组 requested/effective 区间及起始边界语义，不会各自把首次入资日解释成不同的区间。

区间中的买卖不是入金或出金：系统按实际份额和成交金额记账，再按当日收盘估值，成交价到收盘价的变化进入当日收益。未结算交易通过 pending settlement 维持 NAV 连续。入金默认在 settlement / external-flow date 作为日初流入，出金作为日末流出；若业务要求精确处理盘中大额现金流，需要补充流发生时点的完整组合估值，只有交易时间而没有盘中 NAV 不足以精确切分 TWR。

选择 benchmark 后，系统会把组合和基准都按所选区间起点归一化。已确认的全收益指数作为 canonical comparator；已确认的价格指数也会计算差值、tracking error、information ratio、beta 和 capture ratio，但页面会提示价格指数可能不含分红或利息再投资，因此相对结果包含这部分口径差异。收益语义仍为 Unknown 的行情只展示基准自身曲线和指标，不计算相对统计。

常用区域：

- Performance chart：查看组合 NAV/TWR 曲线、drawdown 和 benchmark。
- Calculation：查看区间收益、现金流、fees、taxes、income、FX gains、P&L。
- Groups：按 instrument、account、instrument type、currency、taxonomy 等维度分组。
- Contribution：查看贡献明细和日历视图。
- Boundary holdings：检查区间开始和结束持仓。
- Entries / drilldown：追踪计算条目。

期间工具栏提供 Latest、Reset 以及 MTD、QTD、YTD、1Y、SI 快捷项。一次选择会同时作用于 scorecard、chart、Calculation 和 Groups；切换期间后无需在各区块重复设置。少于一年仍可看 period TWR，但 annualized TWR/MWR 和 Calmar 显示不可用。XIRR 只有唯一有效解时才显示；无解、多解或现金流非法会显示对应原因，不显示 0。

Calculation 的 Download 可选 CSV 或 Excel，内容严格对应当前 requested/effective 区间、分组、排序和可见列，并在文件名中保留组合、日期和分组口径。它是绩效计算分析文件，不可导入 Transactions，也不会改写任何组合事实。

Performance 反映真实历史组合，不是当前权重假设。若与 Risk 或 Research 结果不同，先确认三者口径：Performance 是历史事实，Risk 是当前持仓风险，Research 是规划求解和假设回测。

当组合含事件记账型衍生品时，`Total Portfolio Operational Return` 继续完整反映 NAV、衍生品现金结算、coupon、费用和已实现盈亏，用于对账与经营复盘；它不冒充公允价值衍生品收益。`Market Risk Return` 以同一总 NAV 为分母，把衍生品和本币现金视为 0-return capital：衍生品买入/费用/现金结算、FCN coupon 与现金利息从风险 P&L 中剔除，普通证券的价格变化、普通证券 dividend/coupon 和非本币现金 FX 仍保留。只有本币现金或衍生品、没有任何可建模市场资产或非本币货币风险的组合，风险指标显示 unavailable，而不是实际波动率 0。Calculation 的分组波动率、相关性、Beta 与 Realized RC 使用同一条分组市场风险链，不读取经营收益后再按资产类别过滤。

### 6.7 Risk

Risk 是当前权重口径的风险工作台，使用市场资产历史收益与当前总 NAV 权重。它适合回答“现在这组持仓的风险结构如何”，不适合替代历史绩效归因。

现金和衍生品都会进入组合 NAV、Weight Target 与 Current Drift 的资本权重，但都不设置 Risk Target。现金包含所有账户的现金余额与待交收，衍生品使用 Holdings 的 carrying/liability amount；两者都作为固定系统桶汇总，不读取 taxonomy assignment，也不会重复计数。前瞻组合波动率使用 `market exposure / total NAV`，因此本币现金与衍生品按 0-return capital 稀释组合风险；非本币现金缺少 FX total-return series 时风险不可用。`Risk Target Gap` 只比较承担市场风险的 Securities sleeve；Cash 与 Derivatives 不显示 risk-target row，不进入风险预算 100% 分母。

常用内容：

- Risk Health：看 forward volatility 与样本覆盖、风险预算总偏离、Top-3/HHI 集中度、现金与待交收。
- rolling volatility / Sharpe：看风险和风险调整收益随时间变化。
- correlation matrix：默认查看 Current Holdings；需要研究未持有资产时可显式切到 Full Universe。
- current drift：看当前权重相对目标或分类的偏离。
- risk contribution：看各资产或分组对组合风险的贡献。
- benchmark：选择可用 benchmark 后比较风险曲线。

风险结果依赖资产收益序列。Production Risk Model 的 `1M / 3M / 6M / 12M / 24M` 从 holdings as-of date 按自然月回看，不是 30/90 个交易日；起止日都是 EOD boundary，只使用 `(start EOD, as-of EOD]` 的收益行。计算频率优先读取 Registry expected frequency，daily 数据按 instrument 自己的 market calendar 区分休市和缺点；即使所有持仓共同漏掉同一个预期交易日，也不会把“彼此仍对齐”误当成完整数据。若 start 落在周末或休市日，首条收益从此前最近有效收盘连接到此后首个有效交易日；不会虚构非交易日 close，也不会因为最新数据较早就把窗口整体向前挪。缺少历史行情、只有期末净值但没有对应期初净值、期间起止不一致、尾部数据陈旧或缺少 FX 时，结果会明确显示 unavailable。

Forward RC 先对当前 leaf instruments 运行一次组合级 covariance；taxonomy/sleeve 只加总这些资产相对于同一组合 variance 的贡献，不会在分组后重新估计一套风险。Rolling Risk 和 Correlation Matrix 都要求所有 active members 在共同历史内使用完全一致的 period start/end 与日期序列。缺少成员、缺少日期、日期逆序、常数收益或窗口覆盖不足时，页面列出具体成员与日期并保持 unavailable；不会补 0、静默取交集或用每对资产不同的日期拼矩阵。

Benchmark 对比要求 benchmark 与组合本币一致，并且收益语义已确认为 total return 或 price return。价格收益可以查看，但页面会提示其不含分红；币种不一致或收益语义未知时不绘制比较曲线。

### 6.8 Taxonomies

Taxonomies 管理组合分类和目标体系。Research 的求解结构来自这里。

常见对象：

- planning taxonomy：组合规划分类体系。
- sleeve tree：资产或资金桶的层级结构。
- assignment：把 Securities instrument 分配到 taxonomy node；Cash 与 Derivatives 是固定系统桶，不接受 assignment。
- TargetSet：目标权重或风险预算集合。
- default planning taxonomy：Research 默认使用的规划体系。

维护顺序：

1. 建立或选择 planning taxonomy。
2. 维护 sleeve tree。
3. 给 Securities 做 assignment；Cash 与 Derivatives 无需分类。
4. 建立 TargetSet。
5. 检查目标权重或风险预算是否完整。
6. 设置 default planning taxonomy。
7. 进入 Research 运行求解。

TargetSet 的两个维度必须分开维护：

- `weight` 表示资本配置，Securities 节点、固定 Derivatives 与固定 Cash 都可以设置；
- `risk budget` 只给承担风险的 Securities sleeve 设置，风险份额合计 100%；
- Derivatives 与 Cash 的 Risk 固定为 `N/A`，不能建立 `0%` 占位值；
- Research 将两者作为 fixed-capital members：只分配资金权重，不产生收益序列，也不进入协方差或风险预算求解。

若 Research 报 scope 无成员、目标缺失或 top sleeve bounds 无法满足，通常要回到 Taxonomies 检查 assignment、TargetSet 和树结构。

Assignment 的键盘提交只在编辑区域内使用 `Ctrl/Cmd + Enter`；Tab 保持浏览器正常焦点导航，裸 Enter 不执行全局 mutation。

### 6.9 Research

Research 用于 target solve 和假设回测。它不改变真实持仓，结果用于研究和调仓建议。

研究资产有三种 lifecycle：`Held` 是当前持仓，`Observed` 是研究池成员，`Former` 是历史持有但已退出。页面同时显示 research eligibility。Former instrument 如果得到正目标，必须由 PM 显式批准；未批准时 run 可保留研究结果，但 execution readiness 显示 `PM review required`，不得把它当成已批准调仓建议。

运行前需要确认：

- planning taxonomy：使用哪套规划分类。
- scope：求解整个组合还是某个 sleeve。
- target dimension：使用 scope default、weight 或 risk budget。
- capital mode：`unit_notional`、`fixed_gross`、`target_volatility` 或 `volatility_cap`。
- lookback days：风险和收益窗口长度。
- calculation frequency：固定为 daily，不需要设置。
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

Research 列表默认只取 compact run summary；选择某一 run 后再加载完整结果。快速切换时以最后一次选择为准，旧请求不会覆盖新 run，加载期间也不会把上一 run 的明细误放到当前选择下。

## 7. 典型工作流

### 7.1 新基金进入观察池

1. Platform 搜索基金 ticker / ISIN / 名称。
2. 不存在时按产品性质新建 `public_fund` 或 `private_fund`；Tushare 来源属于公募，邮件来源属于私募。
3. 维护基金币种、primary identifier 和 `official_nav / total_return_nav`。
4. 系统会自动把 active 公募或私募同步到对应的 `All 公募 / All 私募`；需要进入其他名单时再人工添加。
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
3. 在 Transactions 直接搜索资产。公募、私募和指数来自 Registry；股票与 FMP ETF 直接搜索本地 FMP 目录，不要求先加入 Watchlist，首次选中时系统会核实交易所和报价币种、建立或确认 identity，并加载 EOD。
4. Transactions 新增 `buy`。
5. 填写 account、instrument、trade date、settlement date、quantity、price、gross amount、fee、tax、currency。
6. 保存后查看 ledger posting、Holdings 和 Overview。
7. 若快照 stale，等待刷新完成。

### 7.4 做一次组合区间复盘

1. Portfolio 进入 Performance。
2. 选择 Start Date、End Date 和 benchmark；除 funded-segment start 外，这是“起始日收盘到结束日收盘”的区间。
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
