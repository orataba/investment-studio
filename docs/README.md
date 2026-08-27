# Documentation Index

这里是仓库文档的唯一总入口。文档只描述当前合同、当前支持边界和可执行 runbook；Git 历史、PR 或任务记录承担过程与发布证据的追溯职责。

## 按问题找文档

| 需要解决的问题 | 权威文档 |
| --- | --- |
| 第一次接手项目、定位代码和评估修改影响 | [Developer Guide](./DEVELOPER_GUIDE.md) |
| Platform、Registry、Watchlist、Portfolio 的职责和数据流 | [Platform Boundaries](./PLATFORM_BOUNDARIES.md) |
| 同事如何使用三个应用 | [User Manual](./USER_MANUAL.md) |
| 数据库拓扑、迁移、重建、恢复与 PostgreSQL 测试 | [Database Workflow](./DATABASE_WORKFLOW.md) |
| macOS 常驻服务、定时刷新、日志和卸载 | [macOS Local Service](./LOCAL_MACOS_SERVICE.md) |
| Linux/systemd 部署、网络和访问控制 | [Server Deployment](./SERVER_DEPLOYMENT.md) |
| 新机器恢复代码、secrets、数据库和托管服务 | [New Machine Restore](./NEW_MACHINE_RESTORE.md) |
| Portfolio 数据表、字段和写入所有权 | [Portfolio Database Dictionary](./PORTFOLIO_DATABASE_DICTIONARY.md) |
| 基金 NAV 事件、复投证据、投影和重算 | [Fund NAV Event Model](./FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md) |
| 外部系统通过 JSON Preview/Commit 写入交易 | [Transaction Import API Guide](./TRANSACTION_IMPORT_API_GUIDE.md) |
| 截图助手的 Harness、工具权限和人工确认边界 | [Portfolio Copilot Harness](./PORTFOLIO_COPILOT_HARNESS.md) |
| 前端视觉、交互和跨应用 UI 约束 | [Frontend Design Baseline](./FRONTEND_DESIGN_BASELINE.md) |

## 按模块深入

| 模块 | 入口 |
| --- | --- |
| Platform | [apps/platform/README.md](../apps/platform/README.md) |
| Watchlist | [apps/watchlist/README.md](../apps/watchlist/README.md) |
| Portfolio | [apps/portfolio/README.md](../apps/portfolio/README.md) 与 [Portfolio 文档索引](../apps/portfolio/docs/README.md) |
| Instrument Core | [packages/instrument-core/README.md](../packages/instrument-core/README.md) 与 [Instrument Core Contract](../packages/instrument-core/INSTRUMENT_CORE_CONTRACT.md) |
| Shared UI | [packages/ui/README.md](../packages/ui/README.md) |
| Instrument Registry migrations | [infra/instrument_registry/README.md](../infra/instrument_registry/README.md) |

Watchlist 的详细产品与计算文档由其 app README 直接索引；Portfolio 指标、Holdings 字段、交易操作和 GIPS 方法由 Portfolio 文档索引管理。根目录 README 和 app README 只负责导航、范围和启动方式，不重复完整公式或字段字典。

## 文档生命周期

1. 一个概念只能有一份 canonical 文档。其他文档只保留一句边界和链接。
2. 新需求优先修改现有文档。只有面对稳定、独立受众且无法合理归入现有文档时，才新增文件。
3. 新文档必须在本索引或对应 app README 中登记，并在同一改动中删除被替代的文档。
4. 仓库不保存日期化 Review、发布验收、handoff、排查日志或测试数量快照，也不建立 `docs/archive`。这些记录由 Git、PR、issue 或任务系统保存。
5. 会随 checkout 变化的 migration head、测试数量、数据规模、服务状态和供应商实测结果，不写成长期“当前值”；文档提供取得当前值的命令或代码位置。
6. 公式、账务语义、日期边界和数据血缘必须进入对应领域合同；不能只存在于 README、页面文案或一次性报告中。
7. 代码和合同变化必须在同一改动中更新。移除能力时删除旧说明，不保留假想兼容分支。

## 提交前检查

```bash
infra/scripts/verify_repository.sh static
```

该检查验证仓库内 Markdown 链接、文档发现路径以及不允许重新引入的日期化归档。完整应用验证使用：

```bash
infra/scripts/verify_repository.sh all-local
```
