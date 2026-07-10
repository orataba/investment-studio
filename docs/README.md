# Portfolio Operations Workbench Docs

这组文档只保留当前工程边界、数据库工作流和设计基线。

建议阅读顺序：

1. [README.md](../README.md)
   仓库概览、目录、当前边界、启动方式和常用校验命令。
2. [USER_MANUAL.md](./USER_MANUAL.md)
   面向公司同事的使用手册，说明 Platform / Watchlist / Portfolio 的日常使用方式。
3. [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)
   单库多 schema 的数据库拓扑、迁移入口、重建脚本和 PostgreSQL integration test 路径。
4. [PLATFORM_BOUNDARIES.md](./PLATFORM_BOUNDARIES.md)
   `Platform / Watchlist / Portfolio / instrument-core / instrument_registry` 之间的当前职责和数据边界。
5. [FRONTEND_DESIGN_BASELINE.md](./FRONTEND_DESIGN_BASELINE.md)
   当前前端设计基线：白底数据终端、tab-to-content 节奏、字体层级和跨 app UI 边界。
6. [MAC_MIGRATION_FREEZE.md](./MAC_MIGRATION_FREEZE.md)
   WSL 到 Mac 迁移冻结清单：Git 承载范围、数据 dump、NAV 附件和服务重建边界。
7. [NEW_MACHINE_RESTORE.md](./NEW_MACHINE_RESTORE.md)
   新电脑从私有 GitHub 仓库恢复项目、数据库、运行环境和定时任务的步骤参考。

按 app 深入时：

- [apps/platform/README.md](../apps/platform/README.md)
- [apps/watchlist/README.md](../apps/watchlist/README.md)
- [apps/portfolio/README.md](../apps/portfolio/README.md)

当前约束：

- 以仓库实现为准，不维护不存在的兼容路径。
- 描述当前真实运行方式，不保留过程复盘、临时排查记录或旧架构兼容说明。
- `packages/ui` 已经进入当前路径，先承载语言选择与跨 app 前端共享上下文；不是纯占位目录。
- 前端视觉调整以 [FRONTEND_DESIGN_BASELINE.md](./FRONTEND_DESIGN_BASELINE.md) 为准；不要再引入米黄、沙色或暖灰页面背景。
- `nav/`、`data/migration/` 和 `.env.example` 是仓库恢复资产；真实 backend `.env` 位于本机受限秘密目录，runtime DB、构建产物、依赖目录和缓存仍按本地工作产物处理。

提交前快速检查：

```bash
(cd apps/platform/backend && pytest)
(cd apps/portfolio/backend && pytest)
(cd apps/watchlist/backend && pytest)
npm --prefix apps/platform/frontend run build
npm --prefix apps/portfolio/frontend run build
npm --prefix apps/watchlist/frontend run build
```
