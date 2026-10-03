# Investment Studio UI

用于承载跨 app 的前端共享能力。

当前已落地：

- `InstrumentRiskPanel` / `instrumentRisk` / `instrument-risk.css`
  Watchlist 与 Portfolio 共用的标的风险事项、复核线和跟进界面。各 app 提供数据入口与上下文，事件统一保存在 Watchlist。

- `LanguageProvider`
  统一保存当前语言，支持英文与简体中文；按 URL、localStorage、语言 cookie、支持的浏览器语言依次选择，最后回退英文。
- `LanguageSelector`
  给 `home / portfolio / watchlist / briefing` 提供同一套语言切换控件。
- `systemMessages.ts` / `matchesSystemLabel`
  系统字段、选项、提示的中英文文案，以及支持两种语言的字段搜索。基金、股票、组合名称与用户笔记等内容用 `translate="no"` 保留原文；下拉选项只翻译显示文字，不修改保存值。
- `WorkspaceSwitcher` / `workspace-switcher.css`
  四个工作区的紧凑原生选择器，与语言和账号入口使用一致的轻量文字导航样式，保留箭头与键盘焦点；复用地址和语言合同，选择进入目标入口，浏览器返回保留原 URL。
- `navigation.ts`
  统一首页及各工作区的本地端口、局域网主机和部署域名解析，跨工作区链接携带当前语言。Regime 独立运行，在其静态页面中实现同样的导航和语言约定。
- `language.css`
  语言切换控件的基础样式。
- `Sparkline` / `sparkline.css`
  Portfolio 与 Watchlist 共用的小型价格/NAV 趋势图，统一表格内 chart 列的渲染、空态、颜色与尺寸。
- `ConfirmDialog` / `useModalDialog` / `modalStack`
  跨 app 的确认弹窗、焦点管理和嵌套弹窗栈。
- `RiskOfficerPanel` / `InstrumentRiskPanel` / `RiskChangeAudit`
  Watchlist 与 Portfolio 共用风险工作面。团队范围手动研判先说明共享对象和可能状态变化；组合判断不改团队风险。状态读取失败保留已知任务并只读恢复，完成与变更时间统一 UTC；风险变更从同一留存收据展示发起人、任务、范围及前后状态，不推断旧记录的未知归属。
- `RequestRecovery`
  统一账号或业务读取失败时保留当前目标，并提供本地化的重试、首页和请求诊断编号。暂时依赖失败不推断为退出登录；原有会话明确失效时仍卸载受保护内容。
- `NoticeToast`
  成功、错误和一般通知使用非阻塞右侧堆叠，配有语义图标与浅色底；错误可关闭，成功自动收起。通知通过 portal 挂在页面根部，弹窗内调用也不改变布局或被遮挡。`LoadingNotice` 在调用处的内容区域居中显示，表单操作使用紧凑模式，不进入通知栏；已有 skeleton 的加载不重复显示通知。字段校验和长期数据覆盖说明仍留在相关字段/数据区域。
- `studio-theme.css`
  维护中性色、交互、通知和图表的共享语义颜色。青绿承载主交互，紫色承载一般提示，琥珀承载警告；图表使用可区分的对比序列，金融涨跌保留正绿负红。
- `WorkspaceLoadingDrawer`
  风险／研究抽屉代码尚未加载时保持原抽屉位置、关闭操作与焦点交接，避免局部加载反馈推移页面导航。
- `InfoHint` / `info-hint.css`
  标题或指标旁的 16px 正圆提示：默认 `kind="explanation"` 使用紫色问号说明定义、口径和方法；`kind="attention"` 使用琥珀色叹号标注需要关注的数据限制或待处理事项。实际风险事项继续使用 `WorkspaceToolIcon` 的盾牌标记和风险面板，三者不混用。提示与相邻文字保持 6px 间距；普通行内内容由组件提供左侧间距，已有 flex 布局的标题组设置 `gap: 6px; --info-hint-inline-gap: 0`，避免双重间距。悬停、聚焦和点击显示同一份锚定说明，点击可固定，Escape / 外点关闭，不改变页面布局或打开模态窗口。Portfolio、Watchlist、Briefing 共用；Regime 以原生页面实现相同交互。
- `DownloadFormatMenu` / `tableExport`
  下载格式选择与表格导出基础能力。
- `HorizontalTableScroll` / `useHorizontalTablePan`
  宽表滚动容器与鼠标横移交互。只在实际溢出时启用；控件、表头及原生拖放保留自身操作，Shift 拖动保留文字选择，触摸板和触屏保持原生滚动。包装组件支持调用方 ref，并在溢出时提供键盘聚焦。
- `requestIdentity` / `serialTaskQueue`
  防止过期请求覆盖当前状态，并串行化需要按次序完成的前端任务。

- `diagnostics`
  四个前端共用的浏览器加载、请求和错误定位记录，经 Home 已认证入口写入服务日志；不上传表单、URL查询、响应或异常消息。

当前边界：

- 前端视觉基线记录在 [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)。
- `packages/ui` 只承载已经在两个以上 app 中稳定复用的能力；布局、tabs、table 等更大的 primitives 暂不提前抽象。
- 仅单 app 使用的业务布局和领域组件不进入共享包，避免为了单页视觉修复制造全局依赖。
