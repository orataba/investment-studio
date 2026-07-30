# Frontend Design Baseline

本文档记录当前前端设计约束，作为 `Platform / Watchlist / Portfolio` 后续 UI 修改的基线。

## Product Hierarchy

- `Platform`
  是平台入口和 `Database Dashboard`，信息密度低于业务 app，但仍然使用数据终端式布局。
- `Watchlist`
  是 fund/index 工作区，详情页属于嵌套资产 detail，header 轻于 Portfolio。
- `Portfolio`
  是独立业务 app，header 和 workspace 层级可以更重；tabs 以下的内容节奏需要和 fund detail 保持一致。

同名页面不代表同一业务页面：

- Portfolio `Overview / Performance / Risk / Research` 属于 `/portfolios/:portfolioId/...`，数据来自组合账户、交易、持仓、现金流和组合 read models。
- Watchlist fund/instrument detail 的同名 tab 属于 `/instruments/:instrumentId`，数据来自单资产主档、NAV/price series、benchmark 和 Watchlist-local research facts。
- 两者可以共享视觉节奏或已经稳定的底层 UI primitive，但不得共享 TWR、现金流、归因、风险预算等业务计算，也不得用一个 app 的页面验收另一个 app。
- 修改前必须确认 app header、URL、源码目录和 API origin；相同的 `Performance` 标题不能作为页面身份。

## Visual System

- 页面背景使用白色，不再使用米黄、沙色、暖灰渐变或装饰性底色。
- 表格、panel、筛选区和图表容器以白底为主；层级通过黑色标题、灰色边线、字号和间距表达。
- `--band` / `--*-band` 只用于表头、分组行和轻量 hover，颜色限定为冷中性灰。
- 状态和涨跌保留红绿语义色，但状态 pill 不再依赖大面积彩色底。
- 主交互色使用蓝色，选中状态优先用下划线、边线或左侧 marker，不使用大面积色块。

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

## Portfolio Metric Presentation

- 普通组合收益率、区间收益率和风险调整收益默认保留两位小数；不能因为内部计算或审计保留更多精度而把四位以上普通收益直接暴露到主页面。
- 单位净值、价格、输入参数和 Mean Daily Return 等有独立业务语义的字段可以使用不同精度；显示精度与源事实/计算精度分开管理。
- Weight Target / Current Drift 可以展示现金资本权重；Risk Target Gap 完全排除现金。若同一布局为上下文保留现金行，Risk Target 单元格显示 `— / N/A`，不能显示可配置的 `0.00%`。
- Performance 主页面保留 scorecard、风险摘要、区间图和 Calculation attribution。逐日计算审计、publication lineage、rounding trace 等内部诊断不得作为主页面常驻内容；必要时进入受控 drilldown、日志或开发诊断面。
- Overview、Holdings、Performance、Risk、Taxonomies、Research 等成熟页面发生结构或指标改动时，必须有真实渲染的 DOM/browser contract test；只读取 TSX 源码并做字符串断言不能作为页面回归保护。
- Holdings 的 `Portfolio Total` 是当前持仓状态合计，不是 portfolio TWR。`1W / 1M / 3M / 6M / MTD / YTD / 1Y Return` 在当前成员市值和 total-return series 100% 覆盖、return currency 可共同解释时，展示当前权重持仓篮子收益；覆盖不足时显示 `—`。该总行必须与 Performance TWR 明确区分；
- Holdings group / subtotal / total 只计算有稳定业务含义的字段：绝对量加总、比例重算、当前权重 return、共同路径 risk 和同一全组合分母下的 Forward RC。Quantity、Avg Cost、Quote、Holding Since、Chart、Coverage、Held Max DD 等单标的字段留空；完整映射见 [Holdings 字段计算与分组标准](../apps/portfolio/docs/03_HOLDINGS_FIELD_REFERENCE.md)；
- Performance 的 Latest / Reset 与 MTD / QTD / YTD / 1Y / SI 是同一期间选择器的便捷入口；summary、chart、Calculation 和 Groups 必须共享同一个 resolved window，不能各自解释日期；
- Overview 的质量提示只在检测到真实问题时出现，并包含受影响对象/日期及可执行修复方向；不显示没有事实依据的通用 corporate-action 警告；
- Research 列表区分 `Held / Observed / Former` 与 eligibility。Former instrument 的正目标在未经 PM approval 时必须显示人工复核状态，不能作为普通已批准建议。

## Tabs And Content Rhythm

- Portfolio workspace tabs 和 Watchlist fund detail tabs 可以有不同 header，但 tabs 以下的间距、section title、图表和 facts 结构应保持同一语言。
- tabs 到首个内容块之间不留大空白；首个内容块直接从细线、section title 或 chart/table 开始。
- Portfolio 是更重的独立 app，因此顶部 portfolio selector / portfolio headline 可以比 fund detail 更强，但不要把这种层级扩散到 tabs 以下。
- Portfolio Holdings 的 instrument detail 固定分成 `Overview / Transactions / Position Lots`：Overview 承载行情与当前仓位摘要，Transactions 承载已确认交易事实，Position Lots 承载开放成本批次；matched exits 属于所选 lot 的上下文，不再作为与 lot 平级的顶层 tab。

## Chart And Data Surfaces

- Overview chart 使用白底、细灰 grid、蓝色主线和克制 tooltip。
- Portfolio overview 主图使用 `Portfolio Value / TWR Index` 口径；drawdown 是主图下方的附属区，并固定基于 TWR，不和资产规模曲线混为同级。
- 组合价值、fund quote、performance、risk 等图表优先保持可扫读，不使用渐变背景或装饰性卡片。
- Benchmark 对比曲线只在双方有重叠日期窗口时展示；图表横轴按真实日期比例定位，不把缺口期压缩成等距样本。
- Risk correlation 默认 scope 为 `Current Holdings`，`Full Universe` 是显式可选分析。矩阵只在成员、period start/end、日期顺序与完整窗口全部一致时渲染；常数序列、缺成员或缺日期显示结构化 unavailable reason，不能 zero-fill 或 pairwise fallback；
- 数据 palette 避免棕色、橙色、米黄色作为主视觉；必要的警示含义用文字色或边框表达。

## Data Tables

- 宽表的首列如果承载主要对象名称，应在横向滚动时冻结；冻结列需要显式背景和右侧细线，避免透出后方单元格。
- 被锁定的对象名称列不应在 column picker 中再次作为普通 checkbox 字段出现；列配置应用前必须去重并保留锁定列顺序。
- 短列名优先，例如 `YTD`、`1M VOL`、`3M VOL`；缺少窗口锚点等不可用原因放在 hover title 或诊断状态里，不拉长表头。
- Holdings 与 Research 的列表请求默认返回 compact rows。图表 sparkline 只保留有界采样点，重型 return/detail arrays 通过显式 detail 请求懒加载；切换选中项不得把旧 detail 短暂显示到新对象上。
- Taxonomies 不得注册全局 Tab 或裸 Enter mutation。键盘变更只在对应编辑 scope 获得焦点时生效，并使用 `Ctrl/Cmd + Enter` 等带 modifier 的提交组合；浏览器和辅助技术的默认 Tab 导航必须保留。

## Shared UI Boundary

- `packages/ui` 当前只承载语言上下文、语言选择器和通用语言样式。
- 还没有抽出跨 app component library；新增 UI 先遵守本文档，再考虑是否沉淀到 `packages/ui`。
- 不为单次视觉修复创建新的全局 override 层；优先修改现有变量和已有模块样式。
- Portfolio 和 Watchlist 代码边界保持分离，不通过复制业务组件或跨 app import 来“统一”。两边的 toolbar controls 采用一致视觉 contract：30px 高度、0 radius、透明背景、细边框、regular 字重、蓝色 hover/focus、disabled 透明度；各 app 用自己的局部 class 落地。

## Pre-Commit Check

提交前至少执行：

```bash
npm --prefix apps/platform/frontend run build
npm --prefix apps/watchlist/frontend run build
npm --prefix apps/portfolio/frontend run build
git diff --check
```

涉及后端或数据库时，再补跑对应 backend tests 和 migration check，具体命令见 [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)。
