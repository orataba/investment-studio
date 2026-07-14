# Portfolio Operations Workbench Docs

活动文档只保留当前工程边界、数据库工作流、运行手册、设计基线，以及一个临时执行 handoff。完成使命但仍有溯源价值的记录放在 [`archive/`](./archive/)，不参与当前合同。

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
6. [SERVER_DEPLOYMENT.md](./SERVER_DEPLOYMENT.md)
   当前 Linux/systemd 服务部署、迁移、健康检查和运维边界。
7. [NEW_MACHINE_RESTORE.md](./NEW_MACHINE_RESTORE.md)
   新电脑从私有 GitHub 仓库恢复项目、数据库、运行环境和定时任务的步骤参考。
8. [LOCAL_MACOS_SERVICE.md](./LOCAL_MACOS_SERVICE.md)
   在 macOS 上安装、检查和移除本地 `launchd` 常驻服务与每日 21:00 刷新/重算任务。

按 app 深入时：

- [apps/platform/README.md](../apps/platform/README.md)
- [apps/watchlist/README.md](../apps/watchlist/README.md)
- [apps/portfolio/README.md](../apps/portfolio/README.md)
- [apps/portfolio/docs/03_OPTIMIZATION_HANDOFF.md](../apps/portfolio/docs/03_OPTIMIZATION_HANDOFF.md)
  当前仅针对 `apps/portfolio` 的下一轮优化临时执行入口；不得与 Watchlist fund/instrument detail 的同名页面混用，完成后按文档内规则退役。

历史归档：

- [archive/README.md](./archive/README.md)

当前约束：

- 描述“当前已实现”时以仓库实现为证据，不维护不存在的兼容路径；canonical 目标合同和 active handoff 已明确列出的实现偏差，不能因当前错误实现而被反向改写。
- 描述当前真实运行方式，不保留过程复盘、临时排查记录或旧架构兼容说明。
- 不把归档记录或 Git 历史中的失败蓝图恢复成活动规格；仍有效的待办只写入一个 active handoff。
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
