# Yungu Docs

这组文档只保留当前工程边界、数据库工作流和少量阶段记录。

建议阅读顺序：

1. [README.md](../README.md)
   仓库概览、目录、当前边界、启动方式和常用校验命令。
2. [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)
   单库多 schema 的数据库拓扑、迁移入口、重建脚本和 PostgreSQL integration test 路径。
3. [PLATFORM_BOUNDARIES.md](./PLATFORM_BOUNDARIES.md)
   `Platform / Watchlist / Portfolio / asset-core / shared_asset` 之间的当前职责和数据边界。
4. [PHASE1_PLATFORM_SETUP_PLAN.md](./PHASE1_PLATFORM_SETUP_PLAN.md)
   早期阶段记录，只用于回看项目是如何迁入 monorepo 的；不要把它当成当前实施说明。

按 app 深入时：

- [apps/platform/README.md](../apps/platform/README.md)
- [apps/watchlist/README.md](../apps/watchlist/README.md)
- [apps/portfolio/README.md](../apps/portfolio/README.md)

当前约束：

- 以仓库实现为准，不再维护“旧架构兼容说明”。
- 描述当前真实运行方式，不用文档掩盖历史改造痕迹。
- 历史阶段文档必须显式标记为历史记录，而不是当前操作手册。
