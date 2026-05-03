# Yungu Docs

这组文档只保留当前工程边界、数据库工作流和少量阶段记录。

建议阅读顺序：

1. [README.md](../README.md)
   仓库概览、目录、当前边界、启动方式和常用校验命令。
2. [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)
   单库多 schema 的数据库拓扑、迁移入口、重建脚本和 PostgreSQL integration test 路径。
3. [PLATFORM_BOUNDARIES.md](./PLATFORM_BOUNDARIES.md)
   `Platform / Watchlist / Portfolio / asset-core / shared_asset` 之间的当前职责和数据边界。
4. [FRONTEND_DESIGN_BASELINE.md](./FRONTEND_DESIGN_BASELINE.md)
   当前阶段的前端设计基线：白底数据终端、tab-to-content 节奏、字体层级和跨 app UI 边界。
5. [PHASE1_PLATFORM_SETUP_PLAN.md](./PHASE1_PLATFORM_SETUP_PLAN.md)
   早期阶段记录，只用于回看项目是如何迁入 monorepo 的；不要把它当成当前实施说明。

按 app 深入时：

- [apps/platform/README.md](../apps/platform/README.md)
- [apps/watchlist/README.md](../apps/watchlist/README.md)
- [apps/portfolio/README.md](../apps/portfolio/README.md)

当前约束：

- 以仓库实现为准，不再维护“旧架构兼容说明”。
- 描述当前真实运行方式，不用文档掩盖历史改造痕迹。
- 历史阶段文档必须显式标记为历史记录，而不是当前操作手册。
- `packages/ui` 已经进入当前路径，先承载语言选择与跨 app 前端共享上下文；不是纯占位目录。
- 前端视觉调整以 [FRONTEND_DESIGN_BASELINE.md](./FRONTEND_DESIGN_BASELINE.md) 为准；不要再引入米黄、沙色或暖灰页面背景。
- `nav/`、runtime DB、构建产物和缓存都按本地工作产物处理，不作为文档或源码资产提交。

提交前快速检查：

```bash
(cd apps/platform/backend && pytest)
(cd apps/portfolio/backend && pytest)
(cd apps/watchlist/backend && pytest)
npm --prefix apps/platform/frontend run build
npm --prefix apps/portfolio/frontend run build
npm --prefix apps/watchlist/frontend run build
```
