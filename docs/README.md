# Portfolio Operations Workbench Docs

活动文档只保留当前工程边界、数据库工作流、运行手册和设计基线。完成使命但仍有溯源价值的记录放在 [`archive/`](./archive/)，不参与当前合同。

建议阅读顺序：

1. [README.md](../README.md)
   仓库概览、目录、当前边界、启动方式和常用校验命令。
2. [USER_MANUAL.md](./USER_MANUAL.md)
   面向公司同事的使用手册，说明 Platform / Watchlist / Portfolio 的日常使用方式。
3. [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)
   单库四 schema 的数据库拓扑、依赖顺序迁移、重建脚本和 PostgreSQL integration test 路径。
4. [PLATFORM_BOUNDARIES.md](./PLATFORM_BOUNDARIES.md)
   `Platform / Watchlist / Portfolio / instrument-core / instrument_registry / platform schema` 之间的当前职责和数据边界。
5. [FRONTEND_DESIGN_BASELINE.md](./FRONTEND_DESIGN_BASELINE.md)
   当前前端设计基线：白底数据终端、tab-to-content 节奏、字体层级和跨 app UI 边界。
6. [apps/portfolio/docs/01_CALCULATION_SPEC.md](../apps/portfolio/docs/01_CALCULATION_SPEC.md)
   Portfolio canonical 计算合同；Holdings 的逐字段映射另见 [03_HOLDINGS_FIELD_REFERENCE.md](../apps/portfolio/docs/03_HOLDINGS_FIELD_REFERENCE.md)。
7. [FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md](./FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md)
   私募基金单位净值、不可变行为/复投证据、复权因子、人工修订和下游重算的当前合同。
8. [SERVER_DEPLOYMENT.md](./SERVER_DEPLOYMENT.md)
   当前 Linux/systemd 服务部署、迁移、健康检查和运维边界。
9. [NEW_MACHINE_RESTORE.md](./NEW_MACHINE_RESTORE.md)
   新电脑从私有 GitHub 仓库恢复项目、数据库、运行环境和定时任务的步骤参考。
10. [LOCAL_MACOS_SERVICE.md](./LOCAL_MACOS_SERVICE.md)
   在 macOS 上安装、检查和移除本地 `launchd` 常驻服务与每日 21:00 刷新/重算任务。

按 app 深入时：

- [apps/platform/README.md](../apps/platform/README.md)
- [apps/watchlist/README.md](../apps/watchlist/README.md)
- [apps/portfolio/README.md](../apps/portfolio/README.md)
- [apps/portfolio/docs/archive/2026-07-15_OPTIMIZATION_HANDOFF_COMPLETED.md](../apps/portfolio/docs/archive/2026-07-15_OPTIMIZATION_HANDOFF_COMPLETED.md)
  已完成的 Portfolio 优化轮次历史记录；不作为当前实现入口或第二份永久规格。

历史归档：

- [archive/README.md](./archive/README.md)

当前约束：

- 描述“当前已实现”时以仓库实现为证据，不维护不存在的兼容路径；canonical 目标合同不能因当前错误实现而被反向改写。
- 描述当前真实运行方式，不保留过程复盘、临时排查记录或旧架构兼容说明。
- 不把归档记录或 Git 历史中的失败蓝图恢复成活动规格；新的跨轮次工作需要单独、限时的执行计划，完成后归档或删除。
- `packages/ui` 已经进入当前路径，先承载语言选择与跨 app 前端共享上下文；不是纯占位目录。
- 前端视觉调整以 [FRONTEND_DESIGN_BASELINE.md](./FRONTEND_DESIGN_BASELINE.md) 为准；不要再引入米黄、沙色或暖灰页面背景。
- `nav/` 和 `.env.example` 是仓库恢复资产；`data/migration/` 只记录恢复制品政策。真实数据库 dump、backend `.env`、runtime DB、构建产物、依赖目录和缓存均不得进入 Git。

提交前统一检查：

```bash
infra/scripts/verify_repository.sh all-local
```

该入口覆盖静态仓库卫生、活动文档本地链接、三个 backend suite、三个 frontend test/build 和全部便携 infra tests。涉及数据库迁移或 PostgreSQL 专属行为时，再执行 `infra/scripts/verify_repository.sh migration-heads` 与 `postgres-integration all`；完整环境要求见 [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)。
