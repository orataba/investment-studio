# Investment Studio

用于观察池研究、组合运营、市场状态与日报／周报的 monorepo。四个 App 共享本项目的公开市场数据，业务和私有事实分别持有；`home/` 负责账号、会话和导航。

## 系统一览

| 模块 | 长期职责 | 数据所有权 |
| --- | --- | --- |
| `home/` | 登录与四个业务 App 导航 | `identity`；不读取业务账本或数据源密钥 |
| `shared-data/` | CLI 数据接入、导入、修正与自动更新；无 HTTP 服务 | `instrument_data` 资产事实；`data_ingestion` 接入状态与原始证据 |
| `Watchlist` | 观察池、单资产研究、监控和本地 read model | `watchlist` |
| `Portfolio` | 账户、交易、账本、持仓、绩效、风险和研究 | `portfolio` |
| `apps/regime/` | 独立 Git 子模块；状态识别与私有输入物化 | 独立 `market_data` 数据库与 Regime runtime |
| `apps/briefing/` | 日报／周报与 DeepSeek Harness | `briefing` 报告与输入引用 |
| `shared-data/market/` | 公共数值全市场采集、文本包接收与两端复制 | `market_data`、`market_text` 与不可变 Parquet／原文 |
| `shared-data/instruments/` | 数据层内的共享资产模型、类型合同与迁移 | 共享 Python/TypeScript contract 与 `instrument_data` |
| `packages/ui` | 已在多个应用中稳定复用的前端基础能力 | 无业务事实 |

```text
FMP / Tushare / 权威网站 → Studio 共享数值库
市场资讯采集服务 → 原文/事件包 → Studio 共享文本库
                              ↓
                Watchlist / Portfolio / Regime / Briefing
邮件 / 私有材料 → 各应用私有摄取与业务事实
```

Watchlist 和 Portfolio 直接读取 `instrument_data`，不通过维护 HTTP 获取共享事实。两者独立计算收益和账本；标的风险跟进由 Watchlist 统一保存，Portfolio 经其 API 读取和更新。Watchlist 研究助手可通过显式配置的只读接口取得 Portfolio 持仓和 Regime 状态。FCN 和 Option 是 Portfolio-local 合约，不进入共享资产数据。

## 从哪里开始

- 新开发者或 AI：[Developer Guide](./docs/DEVELOPER_GUIDE.md)
- 使用系统的同事：[User Manual](./docs/USER_MANUAL.md)
- 全部权威文档及维护规则：[Documentation Index](./docs/README.md)
- 登录与主页：[Home](./home/README.md)
- 共享数值与资讯运行：[Market Data Pipeline](./docs/MARKET_DATA_PIPELINE.md)
- 日报／周报：[Briefing](./apps/briefing/README.md)
- 后台 CLI 数据维护：[Data](./shared-data/README.md)
- Watchlist 开发：[apps/watchlist/README.md](./apps/watchlist/README.md)
- Portfolio 开发：[apps/portfolio/README.md](./apps/portfolio/README.md)

## 本地准备

仓库锁定 Python `3.12.13`、uv `0.11.28` 和 Node.js `24.18.0` 作为可复现版本。首次安装或锁文件变化后，从仓库根目录执行：

```bash
infra/scripts/sync_python_env.sh
npm --prefix shared-data/instruments/ts ci
npm --prefix home/frontend ci
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/briefing/frontend ci
```

运行时 secrets 只能来自显式进程环境或仓库外的受控目录。源码目录只保留 `.env.example`，不得创建真实 `.env` 或软链接；数据库密码通过权限为 `0600` 的 `.pgpass` 提供，不写入 URL、文档或 Git。主页使用 `home.env`，数据维护使用 `data.env`，两者不共享配置文件。

- 新机器恢复既有业务数据：[New Machine Restore](./docs/NEW_MACHINE_RESTORE.md)
- macOS 长期运行：[macOS Local Service](./docs/LOCAL_MACOS_SERVICE.md)
- Linux 服务器：[Server Deployment](./docs/SERVER_DEPLOYMENT.md)
- 数据库迁移、重建和 PostgreSQL 测试：[Database Workflow](./docs/DATABASE_WORKFLOW.md)

## 应用与运行分组

`home/apps.json` 只定义导航卡片；修改它即可增删入口，不创建或删除数据库。
部署地址由 `home.env` 中的 `INVESTMENT_STUDIO_HOME_APP_URLS` JSON 对象覆盖。

```bash
bin/investment-studio services status all
bin/investment-studio services restart home
bin/investment-studio services restart investments
bin/investment-studio services restart regime
bin/investment-studio services restart briefing
```

`investments` 同时管理 Watchlist、Portfolio 和共享数据更新；`regime` 委托自己的服务工具。
停止某组不会删除其数据。首次安装仍分别使用 Studio 与 Regime 的安装流程。

Regime 的代码位于 `apps/regime` Git 子模块。已有仓库执行
`git submodule update --init apps/regime`；新机器可使用 `git clone --recurse-submodules`。
Studio 与 Regime 都是私有仓库，递归检出需要同时具有两个仓库的读取权限。Regime 的代码和 UI 回归测试在子模块内提交，先推送子模块，再提交 Studio 的引用；Studio CI 不代替 Regime 自身的 CI。
子模块内部有自己的 `main` 与远端；先在其中提交并同步，再在根仓库记录子模块版本。
根仓库提交或推送不会自动推送 Regime。父仓库测试也不代替 Regime 自己的测试与发布校验。
移除入口只需修改导航；删除子模块和其运行数据是另外的显式操作。

## 统一验证

普通提交的统一入口：

```bash
infra/scripts/verify_repository.sh all-local
```

涉及 migration、search path、cross-schema FK、PostgreSQL constraint 或连接事务状态时，按
[Database Workflow](./docs/DATABASE_WORKFLOW.md) 准备独立测试数据库，再运行 `migration-heads` 和
`postgres-integration`。前者会实际应用 migration，不是只读源码检查；两个门禁都不得指向真实业务数据库。

## 仓库边界

- 数据库结构由七条 Alembic migration chain 管理（共享数值／文本共用一条），发布时使用依赖感知的统一迁移入口。
- `nav/` 是明确保留的恢复资产；数据库 dump、checksum、运行日志、研究产物、依赖目录和构建产物不进入 Git。
- 文档只保存当前合同和可执行 runbook。Review 记录、测试数字、发布快照和一次性交接说明留在 commit、PR 或任务记录中，不在仓库建立文档归档。
- 新共享层必须有当前的跨应用消费者；不为假设中的兼容需求提前抽象。
- Watchlist/Portfolio 数据库与角色统一为 `investment_studio`，服务、容器与外部目录使用 `investment-studio`。历史迁移版本与数据库内部技术字段不为品牌命名而重写。
- 真实数据库、密钥、附件、日志与计算产物不放在源码仓库；实际位置见 [Architecture](./docs/ARCHITECTURE.md)。
