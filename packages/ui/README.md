# Yungu UI

用于承载跨 app 的前端共享能力。

当前已落地：

- `LanguageProvider`
  统一保存当前语言，默认英文，支持英文与简体中文。
- `LanguageSelector`
  给 `platform / portfolio / watchlist` 提供同一套语言切换控件。
- `language.css`
  语言切换控件的基础样式。

当前边界：

- 前端视觉基线记录在 [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)。
- `packages/ui` 还没有承载通用 component library；新增布局、tabs、table、chart primitives 前，先确认已经在两个以上 app 中稳定复用。
- 当前跨 app 共享只放语言能力，避免为了单页视觉修复把 app 私有样式提前抽象成全局依赖。
