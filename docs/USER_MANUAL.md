# Investment Studio 使用手册

## 1. 系统入口

系统地址由维护人按环境提供，仓库文档不固定服务器 IP 或域名：

| 本机入口 | 地址 |
| --- | --- |
| Investment Studio 首页 | `http://127.0.0.1:5172/` |
| Watchlist | `http://127.0.0.1:5173/` |
| Portfolio | `http://127.0.0.1:5174/` |
| Regime | `http://127.0.0.1:3011/` |
| Briefing | `http://127.0.0.1:5175/` |

托管服务器使用维护人提供的受控 HTTPS 首页，四个 App 均可由首页进入。

推荐使用 Chrome 或 Edge。各入口可以加入浏览器收藏夹。日常只打开前端入口；内部 API 端口仅供服务与本机维护使用。

托管入口提供全屏登录，并由反向代理统一保护页面和 API。后台 API 端口只监听本机，不能直接暴露到公网。

Investment Studio 是系统总入口，首页提供 Watchlist、Portfolio、Regime 和 Briefing。页面打不开时，先确认网络连接，再刷新浏览器；仍不可用时记录访问地址、发生时间、浏览器、页面截图或错误提示，交给维护人排查。

维护人登录服务器后，可在 loopback 上用以下地址判断服务是否存活：

- 入口 API：`http://127.0.0.1:8102/api/health`
- Watchlist API：`http://127.0.0.1:8100/api/health`
- Portfolio API：`http://127.0.0.1:8101/api/health`
- Briefing API：`http://127.0.0.1:8110/health`

健康检查只用于判断服务状态，不用于业务操作。

### 1.1 账号与邀请

云端使用登录用户名和密码。管理员邀请时填写受邀人的显示名与团队角色；受邀人在一次性链接中核对自己的姓名、自行选择登录用户名，并设置至少 8 位密码。激活完成后页面显示用于登录的用户名；密码重置页显示已有用户名。链接 24 小时内有效，只能使用一次，过期后请管理员重新生成。

在首页的账号设置中可以修改登录名、显示名和密码。改名保留历史研究署名和组合权限；修改密码只需输入并确认新密码，不要求旧密码或额外验证码，成功后需要重新登录，其他设备也会退出。系统不使用二步验证。管理员邀请账号不会自动授予已有组合权限，组合管理者需在对应组合的 Settings 中添加成员。

本机显式免登录环境显示“本机全权限”，不需要登录；它与云端账号、权限及业务数据分别维护。完整的角色和资料范围见 [多账号体系](MULTI_ACCOUNT_SYSTEM.md)。

## 2. 系统分工

Investment Studio 的入口与业务应用：

- Investment Studio 首页：登录后进入 Watchlist、Portfolio、Regime、Briefing；数据维护不出现在前台。
- Watchlist：基金、ETF、股票和指数观察列表。用于资产池筛选、分组、单资产详情、投资研究、监控和导出。
- Portfolio：组合管理工作台。用于账户、交易、持仓、绩效、风险、分类体系和研究调仓。
- Regime：市场状态与信号面板，模型与运行独立维护，公共市场资料由 Studio 提供。
- Briefing：日报和周报。按日期选择报告，通过简短要点、标签、相关证券涨跌及原始链接浏览信息；原文证据按需查看。

各 App 共用 Studio 的公共数值与文本资料，私有业务数据分别保存；Regime 独立保存模型状态。本地和云端各自运行，账号、列表、研究记录及交易分别维护，不会互相覆盖。两端使用同一套功能代码，Portfolio Research 默认开放并沿用逐组合权限；维护人可按部署关闭入口。Watchlist 研究追踪还会因资产类型、已有研究和账号权限显示不同内容。

Watchlist 添加资产、Portfolio 交易录入以及 FCN／期权挂钩标的均支持搜索股票与 ETF 全市场目录，选中后通过共享数据维护入口按需建档；其他资产仍由后台 CLI 建档。FCN 和期权属于 Portfolio 的组合合约，仍在组合交易流程中创建。

市场数据按 instrument 固定一个 primary source：股票包括 A 股统一使用 FMP；A 股公募和 A 股 ETF 使用 DataHub Tushare，港股/美股 ETF 使用 FMP；A 股指数先核验 FMP 的精确代码和历史覆盖，核验成功才用 FMP，否则固定使用 Tushare。刷新时不会因空响应或错误切到第二 provider，也不双写同一序列。

核心数据分四类：

- 资产主档：名称、资产类型、币种、ticker、ISIN、内部 ID、生命周期状态。
- 市场行情：基金单位净值、分红再投资复权累计净值、指数 close、股票价格、FX spot。
- 组合事实：账户、交易、现金流、持仓、成本、费用、税费、内部转账。
- 分类与研究：Watchlist instrument taxonomy、按资产类型定义的投资研究判断、Portfolio planning taxonomy、sleeve tree、TargetSet。

系统不会用示例数据补空，也不会自动猜业务分类。字段为空通常代表数据确实缺失、口径不适用、日期不重叠或刷新尚未完成。

## 3. 数据维护原则

资产、行情、交易和 taxonomy 都是正式业务事实，保存前应确认含义、日期、币种和数值。不要把测试资产、测试交易或临时标签录入正式 watchlist / portfolio。需要试验功能时，先与维护人确认是否有专用测试组合或测试列表。

同一资产只维护一个 canonical instrument。新增前先在资产选择器或后台 CLI 搜索名称、ticker、ISIN 或其他 identifier，核对交易所、币种和份额类别；同一发行人的不同上市地或股份类别不能互相替代。若发现重复资产，不要继续新增下游数据，应先合并或停用重复记录。

日期要按实际业务日期填写。基金 NAV 使用净值日期，指数 close 使用收盘日期，交易使用 trade date 和必要的 settlement date。Portfolio 默认交易时间为 Asia/Shanghai 12:00；同一天多笔交易需要体现先后顺序时，应填写准确 trade time。

币种要与资产、账户和现金流一致。当前 Portfolio 交易币种支持 USD、HKD、CNY、EUR、GBP、CHF。跨币种组合需要维护 FX，否则组合市值、绩效和风险会出现缺口。

## 4. 后台数据维护

共享资产注册、数据接入、邮件解析、NAV 文件导入、人工修正和状态检查由后台 CLI 执行，没有数据管理网页或专用公开域名。Watchlist 添加资产和 Portfolio 交易／挂钩标的录入可由有编辑权限的用户触发股票/ETF 目录搜索及按需注册；搜索直接读取当前目录，注册复用相同的 CLI 维护入口。现有定时任务继续自动运行。维护人员参见 [Investment Studio CLI](../shared-data/README.md)。

## 5. Watchlist 使用

### 5.1 打开观察列表

从 Investment Studio 首页进入 Watchlist，或访问维护人提供的 Watchlist 地址。默认入口会进入 watchlist 选择或默认列表。每个 watchlist 是一个资产池，可用于基金池、ETF/股票候选池、指数池或专项研究池。

Watchlist 的系统列表包括“全部标的”、`Index`、`All 公募`、`All 私募`。全部标的自动汇集有效登记的基金、ETF、股票、指数和加密资产现货；其余系统列表按类型同步。在 Portfolio 注册股票/ETF 后，打开或切回 Watchlist 会自动更新系统列表及数量，保留当前筛选和视图。自定义列表只组织成员，删除列表或移除成员不会注销标的，也不会改变标的自己的投资状态、设置和研究。相同标的在多个列表中共用同一份资料。资产登记与行情维护仍由共享数据后台完成。

有编辑权限的用户可以从自定义关注列表的菜单重命名，也可以在打开列表后修改名称。名称去除首尾空格后为 1–200 个字符，列表地址、成员和已保存视图保持不变；系统列表名称固定。自定义名称按用户输入显示，不随界面语言自动翻译。

管理员和普通成员都可以创建共享列表，只读成员可以查看。创建人用于区分列表来源，不改变团队共享范围；列表选择区和打开列表后显示创建人。复制列表时记录执行复制的人；历史上没有记录创建人的列表保留“未记录”，不根据当前操作者补写。

### 5.2 添加资产

在列表页面按代码或名称搜索，结果包括已有登记资产及股票／ETF 外部目录。选中未登记证券并点击“登记并添加”后，系统先登记并准备行情，再加入当前列表；仅搜索不会登记证券。基金、指数等其他资产仍需先经后台 CLI 登记。搜索不到时，检查市场、代码和目录覆盖，不要用相似名称创建重复资产。

加入后，资产会出现在主表中。若指标为空，先看资产详情页的数据状态，再看 Monitoring 是否提示缺失行情、缺失必填研究信息或 recalc 失败。

`Add From File` 支持 CSV、TSV、文本和 XLSX，读取 `Identifier`、`Ticker`、`ISIN`、`Ticker / ISIN` 或 `Instrument ID` 列；没有表头时读取第一列。文件中的 identifier 会先去重并全部到共享 Instrument Data 解析，存在未找到或不支持的类型时整批不添加，避免得到半截 watchlist。Excel 单元格必须是字面值，不能用公式生成 identifier。

### 5.3 主表浏览

主表支持分批显示、筛选、排序、列配置、分组和导出：

系统默认视图平铺资产，以分类、近期走势、今年收益、当前回撤和数据日期为主；风险事项在名称旁提示。需要分组时再选择 Group By。

- 分批显示：页面先显示前 `80` 行，可继续显示更多或全部；全选只选中当前已经显示的行。
- 排序：点击列头或使用当前 view 的默认排序。
- 筛选：按字段值缩小结果范围。
- Columns：选择展示字段和列顺序，`Name` 固定为第一列。
- Group By：所有列表统一提供不分组、分类、币种、投资状态四种方式，与列表是否为空或成员资产类型无关。
- Download：导出当前筛选和排序后的全量结果，不只导出当前页。

各应用的宽表使用相同的横向浏览方式：在普通数据单元格上按住鼠标左右拖动，或使用触控板、滚动条。需要选取文字时按住 `Shift` 再拖动；表头排序、列宽调整、按钮、链接和输入框保持各自操作，触屏保留原生滑动。

导出前应确认当前筛选、排序、分组和日期区间符合沟通口径。CSV/Excel 都导出当前 view 的可见字段和筛选、排序后的全量结果；涉及端点敏感指标时会同时带出 metric as-of、return kind、quote basis 和 series type。导出首列固定包含 `instrument_id`，因此可通过 `Add From File` 把这些成员加入另一 watchlist；导入只读取 identifier 列，其他分析字段不会回灌或覆盖 Instrument Data 事实。

Watchlist 允许不同 instrument 的最新数据日期不同。`1M / 3M / YTD` 等字段各自从该行显示的 `Metric As Of` 回看，不使用名单中最晚日期统一截断。页脚会显示当前结果的 as-of 范围；若同一分组内终点不同，收益和风险平均显示 `—`，不要把它理解为 0。Peer 排名只使用同一 as-of 的可比样本。

### 5.4 视图和字段

所有 Watchlist（包括新建空列表）共用18项显示字段，名称固定在首列，其余17项可勾选。目录按日常用途分为三组：

- 基本信息：名称、代码、资产类别、币种、分类、最新值、最新值日期、指标截至日。
- 收益表现：近1周、近1月、近3月、今年、近1年收益，以及近1月走势图。
- 风险与状态：当前回撤、投资状态、风险关注、数据新鲜度。

显示列用于快速观察与比较。研究细项标签、研究记录信息、持仓数据和重复分类层级留在各自详情中。6M、MTD、3Y、5Y、成立以来年化收益及1Y走势图不再占用列表菜单；最大回撤、波动率和Sharpe也留在详情，因为它们基于各标的长度不同的有效历史，不能直接当成同区间风险排名。完整逐项理由见 [Watchlist 列表字段合同](../apps/watchlist/docs/DATA_MODEL_AND_API.md)。

筛选统一为分类体系、资产类别、币种、投资状态、风险关注、数据新鲜度；具体可选值来自当前列表。分组提供不分组、分类、币种、投资状态。列表成员或资产类型筛选不会删减显示列目录，各视图分别保存列、分组、筛选和排序。

新建列表使用统一模板：Overview 默认八列——名称、资产类别、分类、近1月走势图、今年收益、当前回撤、最新值日期、指标截至日；默认不分组、不筛选。投资状态与风险关注按需添加为列，风险事项也会在名称旁提示。分类视图保留七列。打开旧视图时会移除已退出菜单的列、简单筛选和排序，原始资料与内部计算数据保留。

收益窗口各自截止到该行的指标截至日。近1年历史不足或字段不适用时显示 `—`；基金收益只依赖可信的分红再投资复权累计净值，缺失时不回退单位净值。当前回撤表示距离可用历史峰值的跌幅，用于观察当前状态；最新值可能是基金净值、证券价格或指数点位，绝对水平不用于判断贵贱。

需要长期收益或完整风险比较时，进入资产详情核对样本区间、数据截止日和收益口径。`3Y`、`5Y`为近三年、五年的年化收益，`Ann.`为成立以来年化收益；最大回撤、波动率、Sharpe使用各自有效历史，Sharpe采用零无风险利率。

列配置只调整展示，不改变底层数据。常用布局可保存为当前列表的视图。

### 5.5 分组和 taxonomy

`Group By` 只改变当前视图的组织方式，不写 taxonomy 或研究属性，也不支持通过拖拽改变资产分类。需要修改 taxonomy、投资状态或研究判断时，应进入资产详情页的对应编辑区。

Watchlist 在自己的 schema 内维护多资产 `instrument_taxonomy`，Instrument Data 不保存 taxonomy。公募与私募已经是不同的 `instrument_type`，各自在类型内使用基金/策略分类；ETF 使用 ETF 分类，股票按市场/交易所分类，指数使用指数分类。股票交易所分类由 Instrument Data 的 canonical `exchange_code` 映射，其余资产分类仍由研究或业务负责人在 Watchlist 内人工设置。

### 5.6 基金详情页

基金详情页提供“总览 / 投资研究 / 经理观点 / 业绩与风险 / 基金档案”。总览呈现净值与摘要；基金档案按公募、私募保留团队、策略、持仓、条款和材料。经理观点保存人工判断与历史，投资研究呈现研究员持续维护的完整分析；两者的作者和内容分别保留。

基金、ETF、股票和指数详情页右上角的 `Settings` 同时维护投资状态和适用于该资产类型的 taxonomy。投资状态可选择未设置、观察、拟投、在投、暂停或退出；这些设置在 Watchlist 内按 instrument 共享，并不写回 Instrument Data。

常用区域：

- 总览：查看名称、代码、分类、净值主图和关键状态。
- 经理观点：维护带作者、日期、来源及修订历史的个人投资判断。
- 投资研究：先看当前结论、吸引力、风险和重要变化，再阅读适用的领域分析、研究问题及相关依据。
- 业绩与风险：查看区间回报、年度收益、回撤、波动、基准与同类比较。
- 基金档案：查看产品、团队、策略、披露持仓、费用、申赎及原始材料。

选择 benchmark 后，图表会展示基金与 benchmark 的相对表现。benchmark 本身必须有可用行情，且双方收益语义必须明确并一致；否则只保留可验证的独立展示，不计算相对统计。

基金自身指标默认截至自己的最新观测日。选择 benchmark 后，比较矩阵只使用双方日期完全相同的共同观测收盘点：双方都截到最晚共同观测日，SI 从最早共同观测日开始，中间收益也按同一对起止收盘计算。少于一个完整日历年的历史不显示年化收益。若 daily 序列缺少应有交易日，端点收益仍可用，但回撤、波动率、Sharpe 等路径风险指标会显示不可用；周末和交易所休市日不会被当成缺点。

### 5.7 ETF、股票和指数详情页

ETF、股票、指数和加密资产现货提供“总览 / 投资研究 / 经理观点 / 业绩与风险”。投资研究共用页面，按照实际底层、管理方式及产品条款组合分析模块：单股票研究经营与股权价值，权益 ETF 增加成份与整体结构，债券基金使用利率信用方法。BTC 现货独立使用美元报价、UTC 完整日线和全年交易日历，不与同名 ETF 混用。没有结果的模块显示尚待建立，重要资料缺失会明确列出，不以空白推定没有风险。

“编辑研究框架”可以修改专属背景、研究方法、重点和用户约束，保存作者与修订历史；公共方法与专属补充分开。研究助手和自动研究读取同一份档案，研究员不能覆盖用户明确要求。研究区域提供报告阅读模式，将同一底稿整理为便于连续阅读的页面，保留数据日期、来源和图表口径。旧综合分析作为带原日期的历史资料保留，不被新框架重新标记为已经完成的研究。

Briefing 的“行业研究”入口目前提供美股科技（XLK），以实际成份为核心，产业链公司仅作背景。阅读入口进入同一标的报告，沿用 Watchlist 的登录与团队权限；它不独立生成另一套研究。公募、私募可从净值及共同样本比较开展研究，缺少持仓或合同的部分保持未知。

行情分析可基于共享资产数据的 quote selection policy 选中的序列计算：

- `1W / 1M / 3M / 6M / MTD / YTD / 1Y` 和年化收益。
- 最大回撤、当前回撤。
- 年化波动率、Sharpe。
- 与 benchmark 或其他资产的图表对比。

`close / last` 只是字段身份；是否为 total return 或 price return 必须由 Instrument Data `return_semantics` 明确声明。未声明时可以保留独立行情展示，但需要同类收益语义的相对指标会失败关闭。若指标为空，优先检查 共享资产数据中是否有 policy 允许的完整行情、日期是否覆盖分析区间、return semantics 是否明确，以及 Watchlist recalc 是否完成。

### 5.8 Monitoring 和 recalc

Monitoring 页面用于发现需要处理的问题，包括缺失报价、缺失必填研究信息、需要刷新、正在运行或失败的 recalc job。处理顺序：

1. 先通过后台 CLI 补充资产主档或行情。
2. 再补 Watchlist taxonomy / attributes。
3. 触发或等待 recalc。
4. 回到主表或详情页确认字段恢复。

recalc 失败时，不要手工改计算结果。应查看失败资产、失败字段和错误信息，补齐源数据后重新计算。

### 5.9 研究对话与风险跟进

研究页按专题保存问题、对话和材料，可选择观察列表、标的及关联组合。上传的原件与已提取文字分别保留；扫描 PDF 没有文字层时需要补充摘要。研究助手按问题读取现有证据并调用计算工具，回答保存为待复核内容，不修改行情、交易或组合配置。观察列表成员不能当作实际持仓；关联组合只提供当前持仓上下文。

风险侧栏显示数据覆盖限制、区间跌幅和人工记录，可补充跟进意见、复核日期并标为处理中或已处理。自动信号是否解除由新数据判断；“已处理”不会取消仍在触发的风险条件。Portfolio 的标的风险入口与此处共享记录，组合层面的风险计算仍在 Portfolio。

近 1 日、1 周、1 月和1季的跌幅复核线首次由合格日频样本生成，此后保持固定并可人工调整；它们是复核容忍线，不是 VaR、止损指令或尾部概率。低频、缺口或未确认分红断点会使相应读数不可用，具体口径见 [Watchlist 收益与风险合同](../apps/watchlist/docs/RETURN_SERIES_CONTRACT.md)。

## 6. Portfolio 使用

### 6.1 创建与打开组合

从 Investment Studio 首页进入 Portfolio，或访问维护人提供的 Portfolio 地址。进入后选择具体组合。组合页面通常包含 Overview、Holdings、Accounts、Transactions、Performance、Risk、Taxonomies、Research。

创建组合时必须明确选择基础币种（USD、HKD、CNY、EUR、GBP 或 CHF）。基础币种决定组合 NAV、汇总市值、绩效和风险金额的统一表达口径；各账户和交易仍保留自己的事实币种，非基础币种现金与持仓按对应 as-of date 的 Instrument Data FX 换算。应按实际投资汇报口径选择基础币种，不能因为某一笔交易使用港币就改成港币，也不能在缺少 FX 时把不同币种金额直接相加。

有组合编辑权限的用户可以在组合目录菜单或组合页 `Settings` 中重命名。名称去除首尾空格后为 1–200 个字符，组合 ID、地址、账户、交易、研究记录和授权保持不变。重命名不会触发金融结果重算；只读用户可以查看名称，不能修改。

Portfolio 以交易和行情为事实来源。持仓、市值、绩效、风险和研究结果都由这些事实计算得出。不要直接修改结果表来“修正”展示值，应回到交易、账户、行情或 taxonomy 源头处理。

组合页右上角 `Settings` 可以修改组合的 Reporting / Base Currency。修改只改变汇报口径，不改写账户、交易、证券或合约的事实币种；系统会清除旧口径的派生快照，并从组合 inception date 按新币种重算 NAV、收益、归因、风险与损益。若历史 FX 不完整，依赖它的结果应显示不可用，不能把缺失汇率当作 1。切换前应确认新币种是实际长期汇报基准，而不是为了配合某一笔交易临时切换。

Portfolio Taxonomies 是 Portfolio 自己的规划和分析分类。它与 Watchlist taxonomy 在节点身份、层级、assignment、版本和生命周期上都完全独立，二者没有映射或继承关系；即使名称相同也只表示文字碰巧相同，不能据此复制、推断或同步分类。Portfolio 的 Holdings、Performance、TargetSet 和 Research 只消费 Portfolio taxonomy。

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
- cost basis method：成本法，支持 `fifo` 和 `moving_average`；已有持仓历史后不能直接切换。
- cash purpose：仅现金账户使用，区分普通结算、保证金、抵押与融资。抵押余额不是自由交易现金，保证金/融资账户的负余额是负债；内部借还、抵押转移不记为外部入金或收益。
- collateral reference：已抵押资金的确认依据。账户用途按真实安排填写，不用于消除负现金提示。
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

FCN 与期权使用 Portfolio 本地合约，不从 共享资产列表中选择一个“衍生品资产”：

- 首笔 FCN/期权交易同时创建本地合约，记录合约名称、币种、账户和条款；Option 条款必须用 `underlying_instrument_id` 明确引用 Instrument Data 股票或 ETF，不能只在合约名或 note 中描述标的；后续交易动作只选择同一 `derivative_contract_id`。
- 期权动作明确区分买入开仓、卖出平仓、卖出开仓、买入平仓，并分别支持长短仓期初、到期作废、现金结算和实物结果。数量单位是合约张数，权利金总额按张数、每单位权利金和合约乘数计算。经确认的结算方式、行权风格和行权币种保存在合约条款中并参与校验；未知条款留空，不猜测。
- FCN 支持买入、提前退出、票息、敲入观察，以及正常到期、敲入、敲出关闭。敲入观察不关闭仓位、不动现金；最终实物兑付在同一记录填写交付证券、数量、确认总价值及汇率，系统建立证券成本和来源关联，不另造现金兑付与证券买入。系统记录已确认事件，不自动判断障碍是否触发。
- FCN 合约主条款记录名义本金、年化票息率、发行日、最终观察日、到期日、发行人和对手方；每个 underlying 单独记录 Instrument Data security、初始参考价、strike/knock-in/knock-out 百分比水平和是否可交付。
- 新建 FCN／期权和截图复核的挂钩证券输入框可按代码或名称搜索外部目录，显式选择后登记并回填证券。登记只准备证券资料和行情，不提交交易；已确认合约的修订不能替换原挂钩标的。
- 实物行权或被指派时，在交易页的期权入口选择已有合约及实物结果；一次确认原子生成零现金期权关闭腿和按行权价的股票腿，并建立一对一关联。期权过期且仍有未关闭数量时，也可从页头待确认入口处理。Call/Put 与买卖方向决定股票交付方向，乘数决定股票数量；费用、费用分类和税费只记在股票腿。权利金与股票币种可以不同，但股票腿必须使用明确的行权币种，且与标的报价及交付账户币种一致；不支持的跨币种转换不能改记成虚构现金结算。
- 先买股票再交付，填写各自真实交易时间；券商确认先形成股票空头时，明确选择该结果，后续按实际成交买回，不改造历史时间。指定批次用于 FIFO 账户的处置；实物行权页选择的是期权多头开仓批次，不是交付股票批次，留空按账户成本法。
- 合约条款补充或纠错使用合约详情中的审计修订入口，提交完整条款、确认人及依据；系统保留前后版本并核对历史。不能在后续交易中覆盖条款，也不能用修订将原合约替换为另一个产品。

买入、卖出和证券期初持仓以 quantity 与 gross amount 作为份额和成交金额事实，隐含成交价按 `gross amount / quantity / price scale` 计算；输入 price 可以是该隐含价格保留四位小数的展示值。系统接受精确乘积或与隐含价格四位小数一致的价格，但不会用舍入后的 `quantity × price` 反写 gross amount。普通 Instrument Data 证券的 price scale 为 `1`；期权由本地合约 multiplier 决定。卖出、合约关闭和仓位转移会校验可用数量。分红、FCN 利息、费用、税费等若带 entitlement date，日期不能晚于 trade date。settlement date 不能早于 trade date。FCN 的建仓、退出、期初、票息及敲入/敲出必须发生在 issue date 至 maturity date（含）之间，到期事件不得早于 maturity date。

Portfolio 创建时必须确定 inception date。任何交易都不得早于该日期；所有 `opening_balance` 的 trade date 与 settlement date 固定为该日期。已有资产可保留更早的 acquisition date，零成本 opening position 允许 gross amount 为 0；除此之外，Option long/writer expiry 与实物行权/指派的期权关闭腿允许零 gross amount。运行期新增现金应录为 Deposit，新增持仓应录为实际 Buy 或 Transfer，不能用 opening balance 绕过外部现金流。Cash Fee / Tax 不填写 entitlement date；资产关联费用要求 entitlement date 当日存在 long position 或 written-option obligation。Option 独立 Fee / Tax 是合约级现金费用，不改变 long lot 或 writer obligation。Return of Capital 用 trade date 表示 entitlement/record date、settlement date 表示到账日。

费用应选择可证明的 fee category；来源无法分类时保留 `Unknown`，不要猜测。重复提交会通过 idempotency key 去重；若页面提示记录版本冲突，说明事实已在别处更新，应刷新后重新核对，不能覆盖较新版本。

页面右上角把文件操作 `Export / Import / Template` 与录入操作 `From Screenshot / Record Transaction` 分组展示。Export 和 Template 都可选 CSV 或 Excel；Export 始终包含组合的全部交易命令，不受当前筛选影响，导出的任一格式都可再次 Import。Excel 文件使用 `Transactions` 工作表。Import 会先显示逐行和整批校验结果，只有全部通过后才能原子写入；CSV 与 Excel 使用同一字段、账户/币种规则、仓位校验和 preview digest。不要把数据库行 ID、内部 transfer legs 或页面筛选结果另做成第二种导入格式。

`From Screenshot` 只处理当前打开的 Portfolio。一次可上传 1–10 张 PNG、JPEG 或 WebP；重叠截图应放在同一批次，由 Agent 结合当前组合账户、持仓、合约、Instrument Data 标的和既有交易一起理解。上传后自动开始分析，结果是一份可直接修改的草稿，不会自行创建交易。用户核对账户、日期、数量、价格、金额、重复关系及衍生品条款后，点击一次 `Confirm & record`；页面先运行 Preview，校验不通过就保留草稿和错误供修改，通过才立即原子入账。AI 问题只是提示，不需要逐项打勾；历史批次和详细 AI 说明位于次要入口。持仓或现金快照可以只形成初始化/对账候选，不会被自动伪造成历史成交。

交易截图识别等外部系统使用同一 JSON Preview/Commit 接口，同样先校验、后确认并原子入账；页面、CSV/Excel、截图助手和外部接口共用交易动作、账户/币种、持仓历史、来源去重和幂等规则。具体对接合同见 [Portfolio 标准交易记录 JSON 接口对接说明](TRANSACTION_IMPORT_API_GUIDE.md)，Agent 运行边界见 [Portfolio Copilot Harness 设计](PORTFOLIO_COPILOT_HARNESS.md)。

AI 草稿、JSON、CSV 和 Excel 中的新 Option 都必须把挂钩证券写入合约条款的 `underlying_instrument_id`，并由预览核对 Instrument Data 身份、币种和条款。文件支持 `physical_long` / `physical_written` 加 `option_delivery_json`，一行原子生成两腿；不能再导入同笔交付股票。AI 复核可将误识别的现金结果改为实物结果并补填交付账户、费用分类，也可添加、修改或移除 FCN 交付证券和指定批次；缺失信息必须依据回单人工补齐，预览通过后才入账。

Transactions 的 Activity 旁叹号显示分红状态说明；选择 `Review distributions`（分红复核）在本页展开复核区。Fund Distribution Review 默认只读取已经保存的事项，不会因为打开页面而写入新任务。需要核对新增分红事件时，先点击 `Refresh Distribution Events`，再逐项核对；刷新只建立待审事项，不会自动生成现金或份额交易。

内部转账用于组合内账户之间移动现金或持仓。现金转账填写金额；持仓转账填写 instrument、quantity，必要时填写 transferred cost basis。内部转账会生成 transfer in/out 配对记录，不应手工分别录入两边。页面或文件填写的 `source_system + external_reference` 是整笔 Transfer 的来源身份，系统只把它保存在 transfer-out 腿；导出折叠和再次导入仍会保留并查重。

Holdings 若显示 `Negative settled cash` critical alert，表示账本已有负现金但没有明确融资事实。应核对并补录缺失的 Deposit、Transfer 或真实融资记录；当前系统不会自动把负数解释为保证金融资。

删除交易会影响由该交易派生的现金、持仓、成本和绩效。内部转账按 transfer group 成对删除；期权实物交割的期权腿和股票腿也必须成对删除，任一腿不能单独修改。删除前应确认该交易不是后续复盘口径的一部分。

### 6.4 Holdings

Holdings 展示当前或指定 as-of 的持仓、数量、价格、市值、权重、成本和未实现盈亏。持仓来自交易、行情和账户计算，不手工录入。若持仓数量不对，优先检查 Transactions；若市值不对，优先检查 共享行情和 FX；若成本不对，检查账户成本法和历史交易顺序。

Securities 字段按 `Identity / Quote / Instrument Trend / Position / Cost / P&L / Risk` 七组管理，不把不同口径堆在一起：

- Position 的市值和权重以组合基准币种为主；本币 Position Value 仍可按需查看。
- Cost 同时区分本币 book cost、按当前汇率换算的 base cost、按各批次交易日汇率形成的 base cost，以及扣除已卖回款和已实现收入后的 `Net Invested / Break-even Price`。普通 dividend / coupon 不改 book cost，但会降低经济回本价；只有 `return_of_capital` 冲减 book cost。
- P&L 把本币价格未实现、base-currency 价格未实现、base-currency 汇兑未实现和 base-currency 总未实现分开，并保证总额等于价格项加汇兑项。FIFO / moving average 只影响剩余成本、已实现/未实现账面盈亏和 lot，不影响组合 TWR。
- Instrument Trend 的 `1W / 1M / 3M / 6M / MTD / YTD / 1Y Total Return` 与 Vol / Drawdown 使用标的自身已确认的本币 total-return series，包含分红等复权、不含汇率变化，也不读取历史买卖份额或成本。

FCN 和 long option 在已记录事件之间按 transaction cost carrying；short option 以剩余 premium liability 进入 NAV。它们不接收期权/FCN 的虚构实时 fair value，不计算日常衍生品未实现盈亏、Greeks、协方差、Risk Budget 或 Research 序列；相关现金、费用、coupon 和已实现盈亏仍完整进入组合 NAV 与经营绩效。市场风险收益链会把衍生品现金结果、FCN coupon 和衍生品费用从风险收益分子中剔除，并把衍生品资本与本币现金一样保留在总 NAV 分母中，作为 0-return capital。Holdings 风险列可使用 Instrument Data 的真实 underlying spot 计算期权 moneyness/intrinsic、written backing，以及 FCN 的实际 strike/KI/KO 价格。FCN `Delivery buffer = spot / strike price - 1`；所有可交付标的的行情和 strike 都完整时取其中最小值，任一缺失则不发布这个聚合指标。它只表示当前现价相对接票价的距离，不代表衍生品估值、从现价到接票价的跌幅或已经发生的 barrier/delivery event。

页面使用四个直接表面：`Securities`、`FCN`、`Options`、`Cash & Settlement`，不再额外套一层 Derivatives，也不重复展示组合总计。每类只有在存在对应持仓时才显示；全部为空时只显示一个空态。instrument 数量紧邻标题。Securities、FCN 与 Options 可以独立使用 `View` 和 `Columns`；FCN、Options 均以 `Default` 为日常视图，并提供面向条款和估值的系统视图，系统视图切换不改变表格框架宽度。`Cash & Settlement` 使用固定字段，不提供没有实际价值的视图或列配置。每个 settled cash 行对应一个具体账户和币种，并展示该账户从历史流入汇率形成的未实现汇兑损益；组合内部现金划转沿用原成本，不在划转日重置，真正换汇则按录入的成交金额和汇率建立新币种成本。`Group By` 只对 Securities 做当前 taxonomy、instrument type、currency 等二级分组，taxonomy 是当前管理分类，不随 Holdings 日期回放。四表共用同一 as-of workspace 和组合 NAV，拆开展示不会把各表权重重新归一。

只有资产、合约或现金/结算行的名称可以打开详情；点击普通字段不会跳页。在表格数据区按住鼠标左右拖动可以横向浏览宽表，减少误触。

CSV/Excel 同样只为非空的 `Securities`、`FCN`、`Options`、`Cash & Settlement` 输出独立表头；Securities、FCN、Options 跟随当前视图的可见字段，Cash & Settlement 使用与页面一致的固定字段，Securities 额外跟随当前筛选、排序和可选 Group。这是当前持仓分析文件，不是交易导入文件。

Security Group/subtotal 是**当前持仓篮子**：金额加总，比例用组级分子分母重算，Return 用当前 signed base value 合成，Vol / Drawdown 用内部连续、起止完全一致且尾部仍新鲜的共同历史区间先生成当前权重篮子路径再算。每个 group/subtotal 使用自身 base value 分母；非本币资产必须有可共同解释的 base-currency return/FX overlay，否则显示 `—`。Forward RC 仍使用全组合风险模型的共同分母。当前成员覆盖不足、共同路径缺段或尾部陈旧时显示 `—`，不剔除缺失成员后重新归一。

组合 NAV 等总览已经在页面顶部；四类 signed NAV 构成和 `Portfolio Total` 对账统一到 Overview 的 `Asset Mix` 查看。组合真实历史表现仍到 Performance 查看。相同 instrument、相同 as-of 和 total-return basis 下，Holdings 行级窗口收益应与 Watchlist 相同，但两个 app 各自计算、互不调用。完整字段标准见 [Holdings 字段计算与分组标准](../apps/portfolio/docs/03_HOLDINGS_FIELD_REFERENCE.md)。

默认列表使用 compact payload；sparkline 是有界采样，打开持仓详情后按资产类型加载相关交易、lots 和图表。子资源尚未返回时显示 Loading/skeleton，不把 `$0.00` 当成真实数据；真正缺失或不适用的指标显示 `—`。已平仓的衍生品保留合约和交易历史，不退回普通证券模板或展示虚构的当前风险。

详情页根据对象采用不同结构。普通 Security detail 展示标的行情、当前仓位、分账户开放 lots、股票自身交易与已实现/未实现损益，同时单列挂钩期权的交易和损益；实物交割的股票腿与期权腿可相互核对。Option detail 展示合约属性、underlying 真实价格走势图与 strike、到期 payoff、intrinsic value、moneyness、剩余 premium/liability、到期和 backing 风险；没有可靠数据时不估造 time value、fair value 或 Greeks。FCN detail 按每个 underlying 展示真实价格路径及 initial/strike/KI/KO 参考线和距离，并集中呈现期限与价格区域风险；当前价格越线不等于系统确认历史 barrier event。Cash detail 只展示指定账户的本币余额、基准币价值、历史汇率成本、未实现汇兑损益、可用性和相关账本事实；Settlement detail 展示应收/应付金额、确认日、结算日、状态和关联资产。Cash 与 Settlement 不展示证券图表或 position lots。

### 6.5 Overview

Overview 是组合默认首页，展示组合市值、TWR index、回撤、Asset Mix、sleeve 结构、top holdings 和 benchmark 对比。页面只在数据完整时展示计算结果；缺少 fresh snapshot、关键行情或 FX 时，不会用部分数据硬算。

`Asset Mix` 使用与 Holdings 相同的 as-of workspace，把当前组合汇总为 `Securities`、`FCN`、`Options`、`Cash & Settlement` 四类，并以 `Portfolio Total` 作为表尾。金额和权重保留资产负债表符号，负的 FCN 或 option liability 因而显示负金额与负权重；各分类权重、Day Change 和 Forward RC 都使用完整组合 NAV / 风险模型作为共同分母，不把分类各自重新归一成 100%。若某类缺少 base-currency valuation，或存在无法识别为 FCN / Option 的 derivative row，图形整体不可用，表格保留逐类 coverage 状态，不用已覆盖部分拼出伪完整结构。

Overview 的质量提示只在检测到实际问题时出现，并给出受影响的资产/日期和处理方向。没有检测到 corporate action 问题时不会显示通用警告。若请求日期晚于最后一个可靠估值日，页面会使用后端返回的 effective as-of，并解释 clamp 原因。

常见使用方式：

- 查看组合总市值和近期变化。
- 查看 Securities、FCN、Options、Cash & Settlement 的 signed NAV 构成及总和是否与组合 NAV 对上。
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

Performance 反映真实历史组合。Risk 的实际组合滚动视角使用同一条市场风险收益链；当前持仓回溯、当前风险贡献与尾部情景则使用当前持仓假设。Research 属于规划求解和假设模拟，比较前应先确认口径。

当组合含事件记账型衍生品时，`Total Portfolio Operational Return` 继续完整反映 NAV、衍生品现金结算、coupon、费用和已实现盈亏，用于对账与经营复盘；它不冒充公允价值衍生品收益。`Market Risk Return` 以同一总 NAV 为分母，把衍生品和本币现金视为 0-return capital：衍生品买入/费用/现金结算、FCN coupon 与现金利息从风险 P&L 中剔除，普通证券的价格变化、普通证券 dividend/coupon 和非本币现金 FX 仍保留。只有本币现金或衍生品、没有任何可建模市场资产或非本币货币风险的组合，风险指标显示 unavailable，而不是实际波动率 0。Calculation 的分组波动率、相关性、Beta 与 Realized RC 使用同一条分组市场风险链，不读取经营收益后再按资产类别过滤。

### 6.7 Risk

Risk 在同一页面依次展示当前持仓风险、滚动风险、相关性、尾部风险与目标偏离，来源覆盖和样本明细按需展开。概览与当前风险贡献回答“现在持仓的风险结构如何”；滚动风险默认反映实际组合的市场风险，也可切换为当前持仓回溯。两个视角分别标识，不替代 Performance 的历史归因。

现金和衍生品都会进入组合 NAV，但都不设置 Risk Target。Cash 可设置独立的 NAV 现金预留，衍生品只显示实际账面资本，不设置可编辑目标。现金包含所有账户的现金余额与待交收，衍生品使用 Holdings 的 carrying/liability amount；两者都作为固定系统桶汇总，不读取 taxonomy assignment，也不会重复计数。前瞻组合波动率使用 `market exposure / total NAV`，因此本币现金与衍生品按 0-return capital 稀释组合风险；非本币现金缺少 FX total-return series 时风险不可用。`Risk Target Gap` 只比较承担市场风险的 Securities sleeve；Cash 与 Derivatives 不显示 risk-target row，不进入风险预算 100% 分母。

常用内容：

- 风险概览：当前模型波动率、建模覆盖、集中度摘要、风险贡献与目标偏离。历史来源缺口可展开查看标的和日期；它不等同于当前模型不可用。
- 滚动风险：观察窗口直接显示在工具栏。“实际组合”按每日真实持仓与资金变动后的市场风险收益计算；“当前持仓回溯”固定今天的持仓权重重放历史。两者都显示样本日期、观测数及不可用原因。
- 相关性：当前持仓、全部标的或自定义分类使用同一个完整共同样本。非叶分类比较其直接子分类篮子，叶分类比较其中各资产；只有一个成员时明确提示无法比较，不能误解为数据缺失。历史观察截止日可直接选择，当前权重不会因此变成当时的历史持仓。
- 尾部风险：当前持仓在共同证券与汇率历史情景中的 VaR / ES，明确区分请求回看上限和实际可用历史。
- 集中度明细位于 Holdings 的“敞口与集中度”视图；Overview 仅提示超限和已设上限但数据不足的项目。Risk 的 Top-3/HHI 是建模证券内部的持仓分布摘要，不套用 NAV 集中度上限。

风险结果依赖资产收益序列。Production Risk Model 的 `1M / 3M / 6M / 12M / 24M` 从 holdings as-of date 按自然月回看，不是 30/90 个交易日；起止日都是 EOD boundary，只使用 `(start EOD, as-of EOD]` 的收益行。计算使用日频，来源更新安排按 Instrument Data 的 expected frequency 与 market calendar 区分正常停更、休市和缺点；即使所有持仓共同漏掉同一个预期交易日，也不会把“彼此仍对齐”误当成完整数据。若 start 落在周末或休市日，首条收益从此前最近有效收盘连接到此后首个有效交易日；不会虚构非交易日 close，也不会因为最新数据较早就把窗口整体向前挪。缺少历史行情、只有期末净值但没有对应期初净值、期间起止不一致、尾部数据陈旧或缺少 FX 时，结果会明确显示 unavailable。

Forward RC 先对当前 leaf instruments 运行一次组合级 covariance；taxonomy/sleeve 只加总这些资产相对于同一组合 variance 的贡献，不会在分组后重新估计一套风险。当前持仓滚动风险和相关矩阵只检查所选成员、所选窗口内的完整起止期间与日期序列。窗口外的旧缺口不会阻断完整的短窗口，窗口内缺口仍不能补 0 或静默取交集。实际组合滚动风险则使用已发布的市场风险收益与覆盖标识，不受买入前的资产历史缺口阻断。相关系数遇到常数收益时不可定义；合法的零波动率仍可展示。样本不足、起点不足、日期不齐与零波动分别说明。

Benchmark 对比要求 benchmark 与组合本币一致，并且收益语义已确认为 total return 或 price return。价格收益可以查看，但页面会提示其不含分红；币种不一致或收益语义未知时不绘制比较曲线。

集中度中，单证券按各账户绝对市值合计；单 FCN 按剩余名义本金；分类集中度把 FCN 本金按挂钩标的归属分配，默认等分，也可自定义合计 100% 的比例。FCN 分配不加入单证券限额，不表示潜在接票股数。期权、现金、待结算不计入这项指标的分子，组合 NAV 分母保持不变。未分类、缺汇率或缺本金会显示覆盖限制，不能解释为没有风险。

集中度是敞口占 NAV 的上限提醒，不会自动成为 Research 的硬约束。在 Taxonomies 树表逐项填写上限：单证券和单 FCN 的上限全组合共用，同一资产出现在不同分类树时仍是同一条限额；分类节点各自维护上限，每套分类另有提醒开关。关闭分类提醒保留其节点上限，不关闭单证券或单 FCN 提醒。空白表示不限制，0 表示不允许正敞口，只有超过上限才提示超限；不存在通用值、继承、覆盖或关注线。

Holdings 明细显示敞口、上限、余量及来源，可切换单证券、单 FCN 和各分类。FCN 本金分配在单 FCN 视图内编辑。目标与限额可在树表统一保存；限额从页面所展示估值日起生效，保存旁明确显示日期。历史查看使用该日已生效的限额，原修订保留审计；修改限额或分类提醒开关不会使 Research 过期。
VaR / ES 默认最多回看三年、95% 置信度，可切一/三/五年及 95%/99%。回看选项不是已具备历史年限的承诺；面板并列展示请求与实际起止日期、共同情景数、尾部有效样本和单个情景对 ES 的影响。短历史可形成披露了覆盖限制的估计，不能称作完整三年风险统计。前段历史不足、内部缺口、尾端缺失和无法核验日历分别披露。仅覆盖真实日频证券/FX 情景，不包括 FCN/Option 公允价值变化；查看百分比时同时查看未建模金额。共同样本及尾部观察过少时会披露限制或不返回数值。

### 6.8 Taxonomies

Taxonomies 管理分类树、证券归属、目标和逐项集中度上限。分类可按国家、行业、策略等需要建立；没有全局“默认规划分类”或“允许规划”开关，各页面选择所需分类，Research 保存自己的分类选择。仅为 FCN 关联而出现的标的可以分类，但不会自动成为可投资研究候选；通过“添加标的”明确加入时，会纳入组合观察范围，允许配置其直投目标。

一张默认完整展开的树表同时显示分类、证券、FCN、现金、未分类资产和 SAA/TAA 两列目标。每个父行的“配置依据”控制其直接成员：Weight 是成员占父层证券资本的比例，Risk 是成员占父层全局 Euler 风险贡献的比例。SAA 与 TAA 共用这个依据，每阶段只填一组数值；不同父层可使用不同依据。切换依据改变这些数值的解释，不保留一套隐藏的旧维度目标，保存后需要重新研究。

SAA 证券向量应逐项完整且合计 100%。TAA 整层空白时继承该层完整 SAA；开始填写后，必须提交完整向量，不逐成员拼接或把部分 TAA 静默回退为 SAA。明确 0% 与空白不同。只有一个证券成员的层级，其内部比例唯一为 100%。全组合风险目标仅在层级比例能够直接推出时展示；经过多成员 Weight 分叉，不能把资金比例当作全局风险目标。

根层 Cash 是独立的 NAV 现金预留，不参与证券目标的 100% 合计。未填现金预留按 0 处理；TAA 整层继承时一并继承 SAA 现金预留。根 Weight 可明确设置现金 100%、证券全为 0。FCN/Option 只展示实际账面资本，Research 按实际持仓冻结，不编辑衍生品目标；Cash 与 Derivatives 的风险贡献目标均为 N/A。

根行或分类行的右键菜单管理结构；“添加标的”在同页搜索并选择叶节点归属，已有归属会显示当前路径并显式移动。目标、各层依据和逐项限额共用一次 Save/Cancel；失败全部回滚。分类限额是“敞口 / NAV”，不与父内目标比例混用。根行的集中度提醒开关只控制这套分类节点。

分类、归属、目标都只有当前配置，无须填写生效日期。修改这些配置会使相关动态历史展示重算、已有研究结果提示过期；原始交易、现金流、行情和净值事实不改写，已保存研究和报告保持原快照。仅改限额不触发该重算或研究失效。限额自身保留日期与修订，日期显示在保存操作旁。

删除分类节点须输入名称确认，范围包括全部子节点、归属及相关目标。其他节点不自动重新分配比例，受影响证券回到未分类。删除后同名重建获得新身份，不继承旧对象的限额；新节点默认没有上限，旧修订和研究快照保留原引用。

市场风险覆盖由真实持仓、估值和收益／汇率数据决定，不设置人工风险资格、业绩范围或估值标签。未分类或目标为零的实际证券持仓仍参与可支持的风险计算；缺数据或模型不支持时，显示未覆盖敞口和原因。Research 所需分类与目标不完整时明确不可求解，不以等权或零风险替代。

### 6.9 Research

Research 用于 target solve 和假设回测。它不改变真实持仓，结果用于研究和调仓建议。

Research 可以选择任意活动分类；无目标分类可以浏览和返回分类设置，正式运行要求当前已保存的目标完整。每次运行将当前分类、标的归属、目标和研究成员保存为一份快照，当前求解与整段历史回测均使用这份快照。目标不再按历史生效日期切换。

页面中的日期只表示行情和持仓数据截止日，可以使用最新可用数据或指定历史截止日；目标始终使用本次运行时的当前配置。历史曲线回答“当前目标在这段历史中会怎样”，不表示当时已经采用该目标。旧结果保持原样并展示存档方法；重新运行才会采用新目标。交易、目标、分析设置、求解方法或所需行情更新后，旧结果会提示过期；计算过程中输入改变会终止本次发布，保留上一份有效结果。旧求解方法及旧目标合同的结果仍可阅读，但必须重新运行后才可作为当前结果。

Research 对所选范围内全部叶证券一次联合求解，用同一个收益样本和协方差同时满足各层 Weight／Risk 目标；不会先各自求解子组合再拼接。每层诊断、最终权重和风险贡献来自同一全局解。整组合与单个 sleeve 运行会明确标识风险归因范围；选中 sleeve 的结果不包含范围外持仓。Cash 和 FCN/Option 仍是模型外资本，固定衍生品与冻结持仓不能为满足目标而自动调仓。目标冲突、硬约束或数据不足会在结果中披露，不以数值收敛代替配置可执行性。

若冻结或显式上下限使目标不能完全命中，满足硬约束且收敛的结果可标为“约束下解”并继续研究模拟，同时保留目标偏差；这不表示目标已达成或获得全局最优证明。没有有效风险分母、硬约束不满足或未收敛的目标偏差不能作为可执行解。

研究资产有三种 lifecycle：`Held` 是当前持仓，`Observed` 是研究池成员，`Former` 是历史持有但已退出。页面同时显示 research eligibility。Former instrument 如果得到正目标，必须由 PM 显式批准；未批准时 run 可保留研究结果，但 execution readiness 显示 `PM review required`，不得把它当成已批准调仓建议。

运行前需要确认：

- planning taxonomy：使用哪套规划分类。
- scope：求解整个组合还是某个 sleeve。
- 配置依据：沿用 Taxonomies 中各父节点已保存的 Weight/Risk，不在 Research 再覆盖一遍。
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
- hierarchy constraint diagnostics：同一全局解在各层的 Weight／Risk 目标、达成情况和偏差。
- top sleeve bounds status：约束是否满足。
- backtest curve：按所选频率调仓后的假设曲线。
- drawdown、YTD、Calmar、volatility、Sharpe 等指标。
- benchmark comparison：与 benchmark 的同期表现。
- relative metrics：组合相对 benchmark 的收益、波动和信息比率。

Research 失败不会覆盖上一轮成功结果。失败后先看页面上的失败原因，再检查 Taxonomies、TargetSet、缺失收益政策、行情覆盖、benchmark 和 top sleeve bounds。不要为通过求解而随意放宽约束；约束应反映真实投资纪律。

Research 列表默认只取 compact run summary；选择某一 run 后再加载完整结果。快速切换时以最后一次选择为准，旧请求不会覆盖新 run，加载期间也不会把上一 run 的明细误放到当前选择下。

## 7. 典型工作流

### 7.1 新基金进入观察池

1. 后台 CLI 搜索基金 ticker / ISIN / 名称。
2. 不存在时按产品性质新建 `public_fund` 或 `private_fund`；Tushare 来源属于公募，邮件来源属于私募。
3. 维护基金币种、primary identifier 和 `official_nav / total_return_nav`。
4. 系统会自动把 active 公募或私募同步到对应的 `All 公募 / All 私募`；需要进入其他名单时再人工添加。
5. 在基金详情页维护 taxonomy、Current Investment View、manual rating 和逐条 Research Record。
6. 触发或等待 recalc。
7. 在主表配置字段、排序和筛选，导出或保存 view。

### 7.2 新指数用于比较

1. 后台 CLI 新建或确认 index instrument，并验证 FMP 是否有精确 symbol 与历史 EOD。
2. 验证成功则固定 FMP；否则固定 Tushare。维护 close、return semantics 和指数基础档案。
3. Watchlist 加入指数池，检查 Performance / Risk。
4. Portfolio Overview、Performance、Risk 或 Research 中搜索并选择该指数作为 benchmark。
5. 若 benchmark 没有曲线，检查 close 日期是否覆盖组合或回测区间。

### 7.3 录入一笔买入交易

1. Portfolio 进入对应组合。
2. Accounts 确认证券账户和默认结算现金账户存在。
3. 在 Transactions 按代码或名称搜索：结果包含已登记资产及股票/ETF 全市场目录，不要求先加入 Watchlist。选择未登记的股票/ETF 后，系统通过共享维护入口建立唯一资产身份、配置行情来源并补齐行情，再回填交易表单。搜索本身不建档，选择证券也不会提交交易。账户币种与交易类型仍限制可选证券；其他资产由维护人员通过后台 CLI 建档。
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

- 找不到资产：先核对账户币种、交易类型和目录搜索错误。股票/ETF 可在 Watchlist 添加、交易录入、FCN／期权挂钩标的及 FCN 交付标的中直接搜代码或名称，并核对交易所和股份类别；仍无结果时由维护人员检查目录覆盖、active 状态和 identifier。其他资产通过后台 CLI 登记。
- Watchlist 指标为空：检查资产类型是否适用、行情是否覆盖、recalc 是否完成、字段是否属于该 instrument scope。
- 指数没有风险指标：检查 close 序列是否足够长，日期是否连续或覆盖所选区间。
- 基金图表没有 benchmark：benchmark 资产需要可用 NAV 或 close，且日期与基金有交集。
- Portfolio 市值不对：检查交易数量、价格、FX、持仓日期和 snapshot 状态。
- 成本不对：检查账户成本法、历史交易顺序、期初持仓和 return of capital。
- 卖出保存失败：通常是 trade date 时点持仓数量不足，或账户/资产不匹配。
- 组合 as-of 没更新：等待 snapshot 刷新；若长期 stale，检查最近行情、FX 和失败任务。
- Performance 与 Risk 不一致：Performance 是历史组合复盘，Risk 是当前权重风险，不是同一口径。
- Research 失败：检查 TargetSet、scope assignment、缺失收益政策、benchmark 行情和 top sleeve bounds。
- 保存目标后 Research 仍显示旧结果：检查过期标识并重新运行；研究与回测始终使用最新保存的目标，已保存的旧结果不会被自动改写。
- 页面加载慢或操作报错：记录应用、页面、操作、发生时间及错误提示；如有请求编号一并提供。维护人可关联页面、API、数据库和后台任务的耗时定位，不需要提供密码或复制研究内容。
- 导出结果和页面不一致：确认导出前的筛选、排序、日期和 view 是否与页面一致。

## 9. 使用规范

正式数据保存前先确认来源。行情要保留可追溯 provider 或来源说明；交易要保留业务票据或记录；taxonomy 和评级要有研究依据。

不要为了让图表显示而录入猜测值，不要用零值代替缺失值，不要把不适用字段强行填满。缺失就是重要信息，应通过补源数据或调整分析口径解决。

不要删除已有历史事实来修正展示。需要更正时，优先按业务规则修改原交易、补录冲销或维护正确行情；不确定时先记录问题，不直接操作正式数据。

导出结果适合会议沟通、数据复核和阶段性留档。投资判断仍需结合数据来源、覆盖期、缺失提示、人工研究和风险约束。
