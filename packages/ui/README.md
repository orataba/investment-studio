# Investment Studio UI

用于承载跨 app 的前端共享能力。

当前已落地：

- `InstrumentRiskPanel` / `instrumentRisk` / `instrument-risk.css`
  Watchlist 与 Portfolio 共用的标的风险事项、复核线和跟进界面。各 app 提供数据入口与上下文，事件统一保存在 Watchlist。

- `LanguageProvider`
  统一保存当前语言，默认英文，支持英文与简体中文。
- `LanguageSelector`
  给 `home / portfolio / watchlist` 提供同一套语言切换控件。
- `systemMessages.ts` / `matchesSystemLabel`
  系统字段、选项、提示的中英文文案，以及支持两种语言的字段搜索。基金、股票、组合名称与用户笔记等内容用 `translate="no"` 保留原文；下拉选项只翻译显示文字，不修改保存值。
- `navigation.ts`
  统一首页及各工作区的本地端口、局域网主机和部署域名解析，跨工作区链接携带当前语言。Regime 独立运行，在其静态页面中实现同样的导航和语言约定。
- `language.css`
  语言切换控件的基础样式。
- `Sparkline` / `sparkline.css`
  Portfolio 与 Watchlist 共用的小型价格/NAV 趋势图，统一表格内 chart 列的渲染、空态、颜色与尺寸。
- `ConfirmDialog` / `useModalDialog` / `modalStack`
  跨 app 的确认弹窗、焦点管理和嵌套弹窗栈。
- `NoticeToast`
  统一的非阻塞操作结果通知。
- `InfoHint` / `info-hint.css`
  标题或指标旁的说明图标：悬浮摘要、点击浮层、Escape / 外点关闭，不改变页面布局。Portfolio、Watchlist、Briefing 共用；Regime 以原生页面实现相同交互。
- `DownloadFormatMenu` / `tableExport`
  下载格式选择与表格导出基础能力。
- `requestIdentity` / `serialTaskQueue`
  防止过期请求覆盖当前状态，并串行化需要按次序完成的前端任务。

当前边界：

- 前端视觉基线记录在 [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)。
- `packages/ui` 只承载已经在两个以上 app 中稳定复用的能力；布局、tabs、table 等更大的 primitives 暂不提前抽象。
- 仅单 app 使用的业务布局和领域组件不进入共享包，避免为了单页视觉修复制造全局依赖。
