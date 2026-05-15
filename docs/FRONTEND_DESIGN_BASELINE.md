# Frontend Design Baseline

本文档记录当前前端设计约束，作为 `Platform / Watchlist / Portfolio` 后续 UI 修改的基线。

## Product Hierarchy

- `Platform`
  是平台入口和 `Database Dashboard`，信息密度低于业务 app，但仍然使用数据终端式布局。
- `Watchlist`
  是 fund-only 工作区，详情页属于嵌套资产 detail，header 轻于 Portfolio。
- `Portfolio`
  是独立业务 app，header 和 workspace 层级可以更重；tabs 以下的内容节奏需要和 fund detail 保持一致。

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
- 空态使用短句，例如 `No data.`、`No rows.`、`No holdings.`；只有会阻塞用户决策的状态才展示更具体原因。
- 选项、modal、表格视图和字段选择器默认只显示名称；不展示解释性小字、备注提示或长 tooltip，除非缺少它会导致错误操作。
- 错误、校验失败、不可用原因可以保留，但必须是可执行或可诊断的信息。
- 不用页面内说明文字解释功能、键盘操作、内部计算流程或实现细节；这些内容放在文档或测试里。

## Tabs And Content Rhythm

- Portfolio workspace tabs 和 Watchlist fund detail tabs 可以有不同 header，但 tabs 以下的间距、section title、图表和 facts 结构应保持同一语言。
- tabs 到首个内容块之间不留大空白；首个内容块直接从细线、section title 或 chart/table 开始。
- Portfolio 是更重的独立 app，因此顶部 portfolio selector / portfolio headline 可以比 fund detail 更强，但不要把这种层级扩散到 tabs 以下。

## Chart And Data Surfaces

- Overview chart 使用白底、细灰 grid、蓝色主线和克制 tooltip。
- Portfolio overview 主图使用 `Portfolio Value / TWR Index` 口径；drawdown 是主图下方的附属区，并固定基于 TWR，不和资产规模曲线混为同级。
- 组合价值、fund quote、performance、risk 等图表优先保持可扫读，不使用渐变背景或装饰性卡片。
- Benchmark 对比曲线只在双方有重叠日期窗口时展示；图表横轴按真实日期比例定位，不把缺口期压缩成等距样本。
- 数据 palette 避免棕色、橙色、米黄色作为主视觉；必要的警示含义用文字色或边框表达。

## Data Tables

- 宽表的首列如果承载主要对象名称，应在横向滚动时冻结；冻结列需要显式背景和右侧细线，避免透出后方单元格。
- 短列名优先，例如 `YTD`、`1M VOL`、`3M VOL`；缺少窗口锚点等不可用原因放在 hover title 或诊断状态里，不拉长表头。

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
