# Frontend Design Baseline

本文档记录当前前端设计约束，作为 Investment Studio 入口及四个业务 App 后续 UI 修改的基线。

## Product Hierarchy

- `Investment Studio`
  是全屏登录页和轻量应用入口，提供 Watchlist、Portfolio、Regime、Briefing 四个入口；不提供后台数据维护页面。
- `Watchlist`
  是公募 / 私募 / ETF / 股票 / 指数工作区，详情页属于嵌套资产 detail，header 轻于 Portfolio。
- `Portfolio`
  是独立业务 app，header 和 workspace 层级可以更重；tabs 以下的内容节奏需要和 fund detail 保持一致。

同名页面不代表同一业务页面：

- Portfolio `Overview / Performance / Risk / Research` 属于 `/portfolios/:portfolioId/...`，数据来自组合账户、交易、持仓、现金流和组合 read models。
- Watchlist fund/instrument detail 的同名 tab 属于 `/instruments/:instrumentId`，数据来自单资产主档、NAV/price series、benchmark 和 Watchlist-local research facts。
- 两者可以共享视觉节奏或已经稳定的底层 UI primitive，但不得共享 TWR、现金流、归因、风险预算等业务计算，也不得用一个 app 的页面验收另一个 app。
- 修改前必须确认 app header、URL、源码目录和 API origin；相同的 `Performance` 标题不能作为页面身份。

## Visual System

- 页面背景使用白色，不再使用米黄、沙色、暖灰渐变或装饰性底色。
- 首页、Watchlist、Portfolio、Regime 和 Briefing 的页面外框统一居中、最大宽度 1680px，桌面左右内边距 18px；640px 及以下使用 14px。导航与主内容对齐，入口列表不再另设较窄的外框；固定预留滚动条空间，避免长短页面切换时横向跳动。
- 表格、panel、筛选区和图表容器以白底为主；层级通过黑色标题、灰色边线、字号和间距表达。
- `--band` / `--*-band` 只用于表头、分组行和轻量 hover，颜色限定为冷中性灰。
- 状态和涨跌保留红绿语义色，但状态 pill 不再依赖大面积彩色底。
- 主交互色使用蓝色，选中状态优先用下划线、边线或左侧 marker，不使用大面积色块。
- 语言选择与页面导航同行，和退出等工具按钮统一为 30px 高、直角、细灰边框；不单独占据一行页头。窄屏允许自然换行。

## Typography

- 正文字重保持 regular。
- 次级信息用更深的中性灰和 regular weight，不使用“灰色粗体”制造层级。
- 标题和关键数字最多使用 medium weight；表格内数值避免默认加粗。
- 页面级标题、section title、table header 的层级通过字号、线条和位置区分。

## UI Copy

- Loading 状态统一显示 `Loading`，不解释正在加载哪些资源或计算链路。
- 页面首载与 detail 子资源加载使用 skeleton 并设置 `aria-busy`; 未返回事实前不得用 `$0.00`、`0.00%` 或空图伪装已加载结果。刷新可以保留上一份已确认数据，但较旧请求不得覆盖较新选择；
- 空态使用短句，例如 `No data.`、`No rows.`、`No holdings.`；只有会阻塞用户决策的状态才展示更具体原因。
- 选项、modal、表格视图和字段选择器默认只显示名称；不展示解释性小字、备注提示或长 tooltip，除非缺少它会导致错误操作。
- 错误、校验失败、不可用原因可以保留，但必须是可执行或可诊断的信息。
- 不用页面内说明文字解释功能、键盘操作、内部计算流程或实现细节；这些内容放在文档或测试里。
- 无待办的提醒不展示横幅或空面板；状态说明邻近标题，实际操作使用明确的文字入口。交易的分红复核在当前页展开，说明图标本身不打开复核窗口或触发操作。成功反馈使用不占文档流的短暂通知。
- 非阻断的数据质量、口径、覆盖范围说明邻近标题或指标，统一用 16px 正圆叹号 `!`；普通说明与警告只以颜色区分，不混用字母、问号或闹钟。悬停、键盘聚焦与点击显示同一份锚定说明，不跳转、不打开模态窗口、不挤动图表、表格和筛选区。真实读取错误、无法计算及会计事实冲突保持可见，不能把未知伪装成无事项。
- Watchlist、Portfolio、Briefing 的说明图标复用共享 `InfoHint`；Regime 使用相同的原生交互和样式。点击可固定或关闭说明，Escape 和外点关闭，鼠标可移入说明阅读长内容；不叠加浏览器原生 title 提示。成功通知统一悬浮，滚动区域预留滚动条空间。
- Briefing 面包屑固定为 `Home / Market Briefing`（中文为 `首页 / 市场简报`）；日报与周报是页内切换，不作为额外导航层级。

## Portfolio Metric Presentation

- 普通组合收益率、区间收益率和风险调整收益默认保留两位小数；不能因为内部计算或审计保留更多精度而把四位以上普通收益直接暴露到主页面。
- 单位净值、价格、输入参数和 Mean Daily Return 等有独立业务语义的字段可以使用不同精度；显示精度与源事实/计算精度分开管理。
- Weight Target / Current Drift 可以展示现金资本权重；Risk Target Gap 完全排除现金。若同一布局为上下文保留现金行，Risk Target 单元格显示 `— / N/A`，不能显示可配置的 `0.00%`。
- Performance 主页面保留 scorecard、风险摘要、区间图和 Calculation attribution。逐日计算审计、publication lineage、rounding trace 等内部诊断不得作为主页面常驻内容；必要的口径、样本与 unavailable reason 紧邻对应标题或指标，以小型 hover/focus 提示呈现，不单独占据内容行。真实的数据错误、范围截断或覆盖缺口仍使用可见告警。
- Overview、Holdings、Performance、Risk、Taxonomies、Research 等成熟页面发生结构或指标改动时，必须有真实渲染的 DOM/browser contract test；只读取 TSX 源码并做字符串断言不能作为页面回归保护。
- Holdings 分为 `Securities`、`FCN`、`Options`、`Cash & Settlement` 四个按内容显示的直接表面；Securities、FCN、Options 独立保存视图与可见字段，Cash & Settlement 使用固定字段，只有 Securities 提供排序与 `Group By`。FCN、Options 的系统视图均以 `Default` 开始，切换系统视图时保持表格框架宽度稳定。无 rows 的表面不渲染，全部为空时只显示一个统一空态；
- Portfolio 与 Watchlist 的表格工具统一使用 `View`、`Columns`、`Group By` 文案，以及 30px、高对比细边框、直角、透明背景的控制样式。View selector 为文字和箭头预留独立空间；Holdings instrument 数量紧邻表标题，不能混入按钮区；
- Overview 承载全组合 `Asset Mix`：固定汇总 `Securities / FCN / Options / Cash & Settlement`，并以单行 `Portfolio Total` 收尾。金额和权重按资产负债表符号展示，所有分类继续使用 canonical NAV 与同一全组合 Forward RC 分母；该高层汇总不在 Holdings 重复；
- Holdings 不再展示或导出第二套 `Portfolio Total`；页面顶部 portfolio headline 和 Overview `Asset Mix` 已分别承担总览与分类对账职责。Securities group/subtotal 的 base-currency 总未实现收益使用组内 historical-FX open cost 分母；
- Holdings group / subtotal 只计算有稳定业务含义的字段：绝对量加总、比例重算、当前权重 return、共同路径 risk 和同一全组合分母下的 Forward RC。Quantity、Book Avg Cost、Quote、Holding Since、Chart、Coverage、Held Max DD 等单标的字段留空；完整映射见 [Holdings 字段计算与分组标准](../apps/portfolio/docs/03_HOLDINGS_FIELD_REFERENCE.md)；
- Holdings Securities 的列目录固定按 `Identity / Quote / Instrument Trend / Position / Cost / P&L / Risk` 编排。整行不绑定详情跳转，只有 instrument / contract 名称可导航；普通数据单元格支持鼠标左右拖动横向浏览；
- Performance 的 Latest / Reset 与 MTD / QTD / YTD / 1Y / SI 是同一期间选择器的便捷入口；summary、chart、Calculation 和 Groups 必须共享同一个 resolved window，不能各自解释日期；
- Overview 的质量提示只在检测到真实问题时出现，并包含受影响对象/日期及可执行修复方向；不显示没有事实依据的通用 corporate-action 警告；
- Research 列表区分 `Held / Observed / Former` 与 eligibility。Former instrument 的正目标在未经 PM approval 时必须显示人工复核状态，不能作为普通已批准建议。

## Tabs And Content Rhythm

- Portfolio workspace tabs 和 Watchlist fund detail tabs 可以有不同 header，但 tabs 以下的间距、section title、图表和 facts 结构应保持同一语言。
- Portfolio tabs 下方由 `PortfolioWorkspaceLayout` 统一承载内容边界：页签下留 12px，绘制 2px 顶部分隔线，线下留 14px 再开始首组标题、筛选或图表；子页不重复绘制同一条顶线。后续独立 section 保留自身细分层级，避免筛选按钮贴线或切换页签时顶部间距变化。
- Portfolio 是更重的独立 app，因此顶部 portfolio selector / portfolio headline 可以比 fund detail 更强，但不要把这种层级扩散到 tabs 以下。
- Portfolio Holdings 的 instrument detail 固定分成 `Overview / Transactions / Position Lots`：Overview 承载行情与当前仓位摘要，Transactions 承载已确认交易事实，Position Lots 承载开放成本批次；matched exits 属于所选 lot 的上下文，不再作为与 lot 平级的顶层 tab。
- Portfolio Accounts 使用左侧账户目录和右侧单账户工作区。账户身份与余额摘要常驻；`Overview / Positions / Transactions / Ledger` 分别承载配置、当前仓位、源交易和派生分录，不能把四类内容纵向堆叠成一个长页面。页签写入 URL，刷新和深链必须保持当前上下文。
- 添加/编辑账户、记录/编辑交易及截图交易复核使用居中弹窗，与组合设置共享白底、细边框、标题和操作区风格；普通表单按内容确定高度，复杂交易可适度加宽。长表单仅内容区滚动，关闭与保存操作保持可见，不用固定满屏高度撑大短表单。账户表单不继承交易单的核对旁栏布局。
- 右上角 `风险提示` 和 `研究助手` 在当前页面打开右侧栏，保留 URL、筛选及选中对象；独立 `Risk` 页继续承担组合风险分析。助手会话在侧栏内切换，风险事项的“问助手”带入标的及问题，关闭助手后回到风险事项。侧栏与弹窗支持关闭按钮、遮罩及 Escape，关闭后焦点返回入口。
- 账户选择变化时保留目录及工作区外框，摘要只能显示该账户已确认的数据；详情未返回前使用右侧局部 skeleton，不把上一账户的持仓/交易挂到新账户名下，也不弹出页面级加载浮层。请求失败后目录仍可切换。
- 浏览器重新获得焦点时，登录与权限核验在后台进行；未发生身份或权限变化时，不清空缓存、不卸载页面，保留筛选、滚动位置及未提交输入。明确失效事件或核验确认失效时撤下受保护内容；切换身份后重新建立工作区。

## Chart And Data Surfaces

- Overview chart 使用白底、细灰 grid、蓝色主线和克制 tooltip。
- Portfolio overview 主图使用 `Portfolio Value / TWR Index` 口径；drawdown 是主图下方的附属区，并固定基于 TWR，不和资产规模曲线混为同级。
- 组合价值、fund quote、performance、risk 等图表优先保持可扫读，不使用渐变背景或装饰性卡片。
- 图表时间跨度选项放在标题右侧；时间滑块紧贴图表下沿，不另设底色卡片。轨道使用 2px 浅灰线，范围选择用灰蓝色细线；手柄为白底直边矩形，无阴影，保留蓝色 hover / 键盘焦点反馈。
- Benchmark 对比曲线只在双方有重叠日期窗口时展示；图表横轴按真实日期比例定位，不把缺口期压缩成等距样本。
- Risk correlation 默认 scope 为 `Current Holdings`，`Full Universe` 是显式可选分析。矩阵只在成员、period start/end、日期顺序与完整窗口全部一致时渲染；常数序列、缺成员或缺日期显示结构化 unavailable reason，不能 zero-fill 或 pairwise fallback；
- 数据 palette 避免棕色、橙色、米黄色作为主视觉；必要的警示含义用文字色或边框表达。

## Data Tables

- 宽表的首列如果承载主要对象名称，应在横向滚动时冻结；冻结列需要显式背景和右侧细线，避免透出后方单元格。
- 被锁定的对象名称列不应在 column picker 中再次作为普通 checkbox 字段出现；列配置应用前必须去重并保留锁定列顺序。
- 短列名优先，例如 `YTD`、`1M VOL`、`3M VOL`；缺少窗口锚点等不可用原因放在 hover title 或诊断状态里，不拉长表头。
- Holdings 与 Research 的列表请求默认返回 compact rows。图表 sparkline 只保留有界采样点，重型 return/detail arrays 通过显式 detail 请求懒加载；切换选中项不得把旧 detail 短暂显示到新对象上。
- Accounts 的 transaction 表必须同时显示 trade date 与 position recognition / settlement date；基金 `buy / sell` 在活动标签中显示为 `Subscription / Redemption`，但不得改写底层交易类型。Ledger 只组合展示实际存在的 cash、pending、quantity、cost delta，长 note 限制在两行并保留到源交易的链接。
- Taxonomies 不得注册全局 Tab 或裸 Enter mutation。键盘变更只在对应编辑 scope 获得焦点时生效，并使用 `Ctrl/Cmd + Enter` 等带 modifier 的提交组合；浏览器和辅助技术的默认 Tab 导航必须保留。

## Shared UI Boundary

- `packages/ui` 只承载已经在多个 app 中稳定复用的基础能力：语言、确认弹窗与焦点管理、通知、下载格式菜单、表格导出、Sparkline 以及请求/串行任务工具。
- 业务布局、业务表格和领域组件继续留在各自 app；只有形成稳定跨 app contract 后才沉淀到共享包。
- 不为单次视觉修复创建新的全局 override 层；优先修改现有变量和已有模块样式。
- Portfolio 和 Watchlist 代码边界保持分离，不通过复制业务组件或跨 app import 来“统一”。两边的 toolbar controls 采用一致视觉 contract：30px 高度、0 radius、透明背景、细边框、regular 字重、蓝色 hover/focus、disabled 透明度；各 app 用自己的局部 class 落地。

## Pre-Commit Check

提交前至少执行：

```bash
npm --prefix home/frontend run build
npm --prefix apps/watchlist/frontend run build
npm --prefix apps/portfolio/frontend run build
git diff --check
```

涉及后端或数据库时，再补跑对应 backend tests 和 migration check，具体命令见 [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)。
