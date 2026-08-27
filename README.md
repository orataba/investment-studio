# Portfolio Operations Workbench

用于资产主数据、观察池研究和投资组合运营的 monorepo。系统由三个可独立运行的应用组成，并共享同一个 PostgreSQL 数据库中的 canonical 资产底座。

## 系统一览

| 模块 | 长期职责 | 数据所有权 |
| --- | --- | --- |
| `Platform` | 平台入口、Instrument Registry、行情/NAV/FX 摄取 | `instrument_registry` canonical facts；`platform` 摄取状态与原始证据 |
| `Watchlist` | 观察池、单资产研究、监控和本地 read model | `watchlist` |
| `Portfolio` | 账户、交易、账本、持仓、绩效、风险和研究 | `portfolio` |
| `instrument-core` | 跨应用稳定复用的资产与行情合同 | 共享 Python/TypeScript contract |
| `packages/ui` | 已在多个应用中稳定复用的前端基础能力 | 无业务事实 |

```text
供应商 / 邮件 / 人工文件
           |
           v
Platform ingestion -> instrument_registry
                         /           \
                        v             v
                 Watchlist         Portfolio
```

Watchlist 和 Portfolio 直接读取 `instrument_registry`，不通过 Platform HTTP 获取共享事实，也不互相调用业务 API。FCN 和 Option 是 Portfolio-local 合约，不进入 Registry。

## 从哪里开始

- 新开发者或 AI：[Developer Guide](./docs/DEVELOPER_GUIDE.md)
- 使用系统的同事：[User Manual](./docs/USER_MANUAL.md)
- 全部权威文档及维护规则：[Documentation Index](./docs/README.md)
- Platform 开发：[apps/platform/README.md](./apps/platform/README.md)
- Watchlist 开发：[apps/watchlist/README.md](./apps/watchlist/README.md)
- Portfolio 开发：[apps/portfolio/README.md](./apps/portfolio/README.md)

## 本地准备

仓库锁定 Python `3.12.13`、uv `0.11.28` 和 Node.js `24.18.0` 作为可复现版本。首次安装或锁文件变化后，从仓库根目录执行：

```bash
infra/scripts/sync_python_env.sh
npm --prefix packages/instrument-core/ts ci
npm --prefix apps/platform/frontend ci
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/portfolio/frontend ci
```

运行时 secrets 只能来自显式进程环境或仓库外的受控目录。三个 backend 目录只保留 `.env.example`，不得创建真实 `.env` 或软链接；密码通过权限为 `0600` 的 `.pgpass` 提供，不写入 URL、文档或 Git。

- 新机器恢复既有业务数据：[New Machine Restore](./docs/NEW_MACHINE_RESTORE.md)
- macOS 长期运行：[macOS Local Service](./docs/LOCAL_MACOS_SERVICE.md)
- Linux 服务器：[Server Deployment](./docs/SERVER_DEPLOYMENT.md)
- 数据库迁移、重建和 PostgreSQL 测试：[Database Workflow](./docs/DATABASE_WORKFLOW.md)

## 统一验证

普通提交的统一入口：

```bash
infra/scripts/verify_repository.sh all-local
```

涉及 migration、search path、cross-schema FK、PostgreSQL constraint 或连接事务状态时，按
[Database Workflow](./docs/DATABASE_WORKFLOW.md) 准备独立测试数据库，再运行 `migration-heads` 和
`postgres-integration`。前者会实际应用 migration，不是只读源码检查；两个门禁都不得指向真实业务数据库。

## 仓库边界

- 数据库结构只由四条 Alembic migration chain 管理，发布时使用依赖感知的统一迁移入口。
- `nav/` 是明确保留的恢复资产；数据库 dump、checksum、运行日志、研究产物、依赖目录和构建产物不进入 Git。
- 文档只保存当前合同和可执行 runbook。Review 记录、测试数字、发布快照和一次性交接说明留在 commit、PR 或任务记录中，不在仓库建立文档归档。
- 新共享层必须有当前的跨应用消费者；不为假设中的兼容需求提前抽象。
