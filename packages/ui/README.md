# Portfolio Operations Workbench UI

用于承载跨 app 的前端共享能力。

当前已落地：

- `LanguageProvider`
  统一保存当前语言，默认英文，支持英文与简体中文。
- `LanguageSelector`
  给 `platform / portfolio / watchlist` 提供同一套语言切换控件。
- `language.css`
  语言切换控件的基础样式。
- `Sparkline` / `sparkline.css`
  Portfolio 与 Watchlist 共用的小型价格/NAV 趋势图，统一表格内 chart 列的渲染、空态、颜色与尺寸。
- `ConfirmDialog` / `useModalDialog` / `modalStack`
  跨 app 的确认弹窗、焦点管理和嵌套弹窗栈。
- `NoticeToast`
  统一的非阻塞操作结果通知。
- `DownloadFormatMenu` / `tableExport`
  下载格式选择与表格导出基础能力。
- `requestIdentity` / `serialTaskQueue`
  防止过期请求覆盖当前状态，并串行化需要按次序完成的前端任务。

当前边界：

- 前端视觉基线记录在 [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)。
- `packages/ui` 只承载已经在两个以上 app 中稳定复用的能力；布局、tabs、table 等更大的 primitives 暂不提前抽象。
- app 私有业务布局和领域组件不进入共享包，避免为了单页视觉修复制造全局依赖。
