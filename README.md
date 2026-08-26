# Portfolio Operations Workbench

`Portfolio Operations Workbench` 是一个包含 `Platform`、`Watchlist`、`Portfolio` 的 monorepo 工作区，用于投资组合运营、资产主数据、观察池研究和组合绩效/风险管理。

当前状态：

- `apps/platform`
  平台入口与 `Database Dashboard`，维护 `instrument_registry` 中的 `Instruments / FX / NAV` 主数据，并在私有 `platform` schema 保存邮件抓取、原始证据、解析与重试状态；不是其他 app 的运行时依赖。
- `apps/watchlist`
  已有可运行的前后端、数据库迁移、测试与文档，承载公募 / 私募 / ETF / 股票 / 指数 watchlist、local detail、facts、recalc 与 read model。
- `apps/portfolio`
  已有可运行的前后端、数据库迁移、交易、绩效、持仓、风险与研究工作台；Transactions 内置受限截图助手，使用 Agent 分析、Preview、人工复核和最终 Commit 的同一交易合同。

当前架构约束：

- 提供统一的平台目录
- 保持 Platform、Watchlist、Portfolio 三个运行面可独立开发与独立运行
- 共享数据库只抽最小公共底座

## 目录

```text
portfolio-operations-workbench/
  apps/
    platform/
    watchlist/
    portfolio/
  packages/
    instrument-core/
    ui/
  data/
    migration/
  docs/
  nav/
```

## 文档入口

- [docs/README.md](./docs/README.md)
  顶层文档索引，串起数据库工作流、平台边界和设计基线。
- [docs/USER_MANUAL.md](./docs/USER_MANUAL.md)
  面向公司同事的使用手册，覆盖 Platform / Watchlist / Portfolio 的日常操作边界。
- [docs/FRONTEND_DESIGN_BASELINE.md](./docs/FRONTEND_DESIGN_BASELINE.md)
  当前前端设计基线，约束白底数据终端、字体层级、tabs 与内容区节奏。
- [docs/NEW_MACHINE_RESTORE.md](./docs/NEW_MACHINE_RESTORE.md)
  新电脑从 GitHub 私有仓库恢复项目的步骤参考。
- [docs/LOCAL_MACOS_SERVICE.md](./docs/LOCAL_MACOS_SERVICE.md)
  macOS 本地后台服务、每日 21:00 行情刷新与自动重算任务的安装、状态检查、日志和卸载说明。
- [docs/SERVER_DEPLOYMENT.md](./docs/SERVER_DEPLOYMENT.md)
  Linux/systemd 服务部署、迁移和运行检查手册。
- [apps/platform/README.md](./apps/platform/README.md)
  Platform app 的职责、启动命令和前端运行时配置。
- [apps/watchlist/README.md](./apps/watchlist/README.md)
  Watchlist app 的当前实现基线、启动方式和行为边界。
- [apps/portfolio/README.md](./apps/portfolio/README.md)
  Portfolio app 的领域范围、启动方式和导入/校验说明。
- [docs/PORTFOLIO_COPILOT_HARNESS.md](./docs/PORTFOLIO_COPILOT_HARNESS.md)
  内部截图助手的 Harness 选择、MCP 工具边界、人工复核和运行方式。
- [apps/portfolio/docs/archive/2026-07-15_OPTIMIZATION_HANDOFF_COMPLETED.md](./apps/portfolio/docs/archive/2026-07-15_OPTIMIZATION_HANDOFF_COMPLETED.md)
  已完成的 2026-07-15 Portfolio 优化轮次历史记录；当前口径以 canonical 计算、GIPS、设计和用户文档为准。

## 当前边界

- `apps/watchlist`
  承载公募 / 私募 / ETF / 股票 / 指数 watchlist、local detail、facts、read model 与 recalc。
- `apps/portfolio`
  承载 portfolio / account / transaction / performance / risk / research 语境。
- `apps/platform`
  平台 landing / app switcher 与 `Database Dashboard`；维护共享资产和自己的数据摄取工作流，不承载其他 app 的业务编排。
- `packages/instrument-core`
  当前承载共享资产 contract、持久化 model 与 shared store helper：`instrument_id`、`name`、identifiers、`instrument_type`、`currency`、typed `market_data` 与最小 `quote_selection_policy`。
- `packages/ui`
  当前承载已经稳定复用的跨 app 前端基础能力，包括语言、确认弹窗、通知、下载格式菜单、表格导出、迷你图与请求/任务工具；业务布局和业务组件仍由各 app 自己维护。

## 当前原则

- Watchlist 与 Portfolio 两个业务 app 保持解耦，不做业务模型融合；Platform 只提供统一入口、共享主数据维护和自己的摄取状态。
- `Watchlist` 与 `Portfolio` 直接访问同一个 PostgreSQL 中的 `instrument_registry` + 各自私有 schema，不通过 app-to-app HTTP 互相取数。
- 共享资产身份与 typed market facts / selector policy，不共享上层业务 read model。
- 私募基金 canonical NAV 只有单位净值 `official_nav` 与分红再投资复权累计净值 `total_return_nav`；现金分红简单累加值只保留为私有原始证据，不能进入回报曲线。
- `Watchlist` 面向公募 / 私募 / ETF / 股票 / 指数的观察、筛选与单资产分析，不承载 portfolio 业务事实。
- FCN 与期权不是可复用市场资产，不进入 `instrument_registry`；Portfolio 以本地不可变合约及事件交易记录其条款、现金流与已实现结果。合约引用的标的或交付证券仍使用 Registry instrument。
- `Portfolio` 继续 portfolio/account/transaction/performance/risk/research 语境。
- 前端视觉基线统一为白底、冷中性灰线条和表格优先的信息密度；不要再引入米黄、沙色或暖灰页面背景。

## 开发工作流

以下命令默认从仓库根目录执行；如果你在其他目录，先进入自己的本地 clone。真实 backend `.env` 不进入仓库，也不要在 backend 目录建立文件或软链接；本机统一从 `~/.config/orataba/secrets/portfolio-operations-workbench/` 安全加载。不要把密钥值粘贴到 shell 命令、聊天、issue、PR 描述或提交信息里。

### 数据库

```bash
(cd infra/postgres && docker compose up -d)
```

新电脑从零恢复时，优先按 [docs/NEW_MACHINE_RESTORE.md](./docs/NEW_MACHINE_RESTORE.md) 执行：先通过受控私有渠道取得项目级 dump 与校验文件，再把绝对路径显式交给恢复脚本。原始数据库备份不得进入 Git。不要在恢复后运行 `./infra/postgres/rebuild_local_schemas.sh`，除非明确要清空恢复数据并重建空 schema。

下方数据库 URL 都故意不含密码；先通过交互方式配置当前用户权限为 `0600` 的
`.pgpass`，不要把明文密码写进 URL 或 shell history。

默认单库 schema 划分：

- `instrument_registry`
  共享资产、行情、净值、FX 等主事实。
- `platform`
  邮件游标、原始证据、附件解析、重试与候选路由等 Platform 私有运行状态。
- `watchlist`
  watchlist 自己的 read model、facts、recalc job 等私有数据。
- `portfolio`
  portfolio / account / transaction / performance / research 等私有数据。

### 后端迁移

已停止写入的干净安装或受控发布环境可使用统一迁移入口：

```bash
ENV_ROOT="$HOME/.config/orataba/secrets/portfolio-operations-workbench" \
  infra/scripts/migrate_all.sh
```

说明：

- 统一迁移入口只读取显式进程环境或上述仓库外目录中的
  `platform.env / portfolio.env / watchlist.env`；Platform、Instrument Registry、Portfolio、Watchlist 的 database URL 必须显式存在并指向同一个 canonical PostgreSQL。
- 迁移开始前会比较四个 runtime URL、所有显式 Alembic override，以及可选的
  `PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE`。主机、端口或数据库名不一致会直接
  拒绝执行，诊断信息不会输出用户名或密码。
- `instrument_registry` schema 由 `infra/instrument_registry` 统一管理。
- `platform` backend 直接使用共享表，但不拥有 Registry 的迁移入口；`apps/platform/backend/alembic` 只管理私有 `platform` schema。
- 统一迁移入口会在 destructive NAV cleanup 前先建立 Platform 原始证据表，不要把它替换为各 migration chain 无序的独立 `upgrade head`。
- 本机现有真实数据库不要直接运行上述命令；使用
  `PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql://portfolio_ops@127.0.0.1:5432/portfolio_ops' infra/launchd/install_local_services.sh`，由安装器统一停写、备份四个 schema、迁移、重建因版本或输入血缘失效的 Portfolio 派生快照、执行只读完整性审计、失败恢复和健康检查。
- `portfolio@20260809_0045` 会改写 FCN 生命周期事实，后续 clean-cut migrations 也删除旧模型；这些 revision 明确拒绝 downgrade。恢复必须使用安装器生成的迁移前四 schema 备份。

如果本地库已经跑脏或迁移链断过，直接执行：

```bash
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  ./infra/postgres/rebuild_local_schemas.sh --confirm-destroy-project-schemas
```

这个脚本会销毁并重建 `instrument_registry / platform / portfolio / watchlist` 四个 schema；必须
显式给出目标 URL 与破坏性确认 flag。它会把同一 URL 交给 `psql` 和四条 migration
chain，并在破坏性阶段失败时保持托管服务停止。

重建完成后：

- `portfolio` 不会再自动生成默认 demo 组合；需要时请在 `/portfolios` 页面显式创建并选择组合基础币种，或运行导入脚本。
- `watchlist` 不会再自动写入示例标签值；标签与研究判断都只来自显式录入或后续真实数据处理。

### 统一质量门

仓库固定使用 Python `3.12.13`、uv `0.11.28` 和 Node.js `24.18.0` 作为 CI 可复现版本。依赖安装完成后，提交前从仓库根目录运行：

```bash
infra/scripts/verify_repository.sh all-local
```

这条命令依次校验仓库静态卫生和本地 Markdown 链接、三个后端快速测试、三个前端测试与生产构建，以及全部基础设施测试。其中数据库恢复/失败回滚测试会创建两个临时 PostgreSQL 数据库；本机需要已有可连接、可建库的测试角色。也可以只运行某一层或某个 app：

```bash
infra/scripts/verify_repository.sh static
infra/scripts/verify_repository.sh backend portfolio
infra/scripts/verify_repository.sh frontend watchlist
infra/scripts/verify_repository.sh infra portable
PORTFOLIO_OPS_TEST_DB_USER='test_role' \
PORTFOLIO_OPS_TEST_DB_PASSWORD='...' \
  infra/scripts/verify_repository.sh infra postgresql
```

PostgreSQL 迁移链和 integration tests 使用独立测试角色。该角色既要能创建/删除临时数据库，也要对连接串指向的专用控制数据库拥有 `CREATE` 权限，因为 Platform suite 会在该数据库内创建并清理隔离 schema。不要把低权限生产角色或真实业务数据库当作测试控制面；推荐让测试角色拥有一个空的专用数据库：

```bash
infra/scripts/verify_repository.sh migration-heads
PORTFOLIO_OPS_TEST_POSTGRES_URL='postgresql+psycopg://test_role@127.0.0.1:5432/portfolio_ops_test_control' \
  infra/scripts/verify_repository.sh postgres-integration all
```

CI 对 pull request 和 `main` push 强制执行相同的三块门禁：应用/仓库、前端、PostgreSQL。便携基础设施测试在应用任务执行；真实数据库恢复/失败回滚测试在隔离的 PostgreSQL 17 任务执行。PostgreSQL integration suite 若收集为 0 或出现 skip，门禁直接失败，不能把数据库未配置伪装成通过。

### 后端测试

三个后端已经改成独立顶层包名，但测试和脚本仍建议在各自 backend 目录内执行，以复用本地 Alembic 配置和相对路径。

首次安装或依赖变更后，先按统一锁文件同步环境：

```bash
infra/scripts/sync_python_env.sh
```

```bash
(cd apps/platform/backend && pytest)
(cd apps/portfolio/backend && pytest)
(cd apps/watchlist/backend && pytest)
```

补充：

- 上面这组测试主要是快速 SQLite / isolated path。
- `instrument_registry` 的 cross-schema FK 和 search_path 需要额外用 PostgreSQL integration tests 验证，命令见 [docs/DATABASE_WORKFLOW.md](./docs/DATABASE_WORKFLOW.md)。

### 前端测试与构建

```bash
npm --prefix packages/instrument-core/ts ci
npm --prefix packages/instrument-core/ts run typecheck
npm --prefix apps/platform/frontend test
npm --prefix apps/portfolio/frontend test
npm --prefix apps/watchlist/frontend test
npm --prefix apps/platform/frontend run build
npm --prefix apps/portfolio/frontend run build
npm --prefix apps/watchlist/frontend run build
```

`packages/instrument-core/ts` 是 Platform 前端直接消费的共享 contract package；它有独立
`tsconfig.json` 和 typecheck gate。CI 和 `infra/scripts/verify_repository.sh frontend all`
都会先执行该 gate，不能只依赖某个页面恰好被编译到来判断 shared contract 完整。

### macOS 本地后台服务

本机 PostgreSQL 就绪后，可一次完成迁移、前端构建、六个 `launchd`
常驻服务及每日 `21:00` 刷新/重算任务的安装或更新：

```bash
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  infra/launchd/install_local_services.sh
```

安装器要求数据库与角色已经存在。它会在迁移前停服并创建可校验的项目 schema
备份；迁移、构建、plist 安装或健康检查失败时先回滚数据库和旧 plist，再恢复原服务，
回滚失败则保持全部托管服务停止。

安装后从 `http://127.0.0.1:5172` 进入 Platform；详细状态、日志和卸载命令见
[docs/LOCAL_MACOS_SERVICE.md](./docs/LOCAL_MACOS_SERVICE.md)。

## 仓库卫生

- `nav/` 是冻结迁移要保留的 NAV 附件图片数据，已纳入 Git；后续新增大批量原始材料前先确认是否应进入仓库。
- `data/migration/` 只记录恢复制品政策；真实 dump 与校验文件必须保存在 Git 外的加密、受控存储中，且 dump 只允许覆盖 `instrument_registry`、`platform`、`portfolio`、`watchlist`。
- 三个 backend 目录只保留 `.env.example` 作为键名模板，不创建 `.env` 文件或软链接；真实 secrets 位于仓库外的 `~/.config/orataba/secrets/portfolio-operations-workbench/`，运行时必须显式提供指向同一 PostgreSQL 的 canonical database URL。
- `node_modules/`、`dist/`、`*.db`、`*.sqlite*`、`__pycache__/`、`.pytest_cache/` 都是本地产物，不应进入提交。
- `.local-pg/`、`ref/`、虚拟环境和用户级 `systemd --user` unit 都是本机状态，不作为跨机器 Git 迁移载体；`.env.example` 模板仍保留在 Git 里用于说明配置项。
- 提交前以 `infra/scripts/verify_repository.sh all-local` 为统一入口；涉及 migration、cross-schema FK 或 PostgreSQL 专属约束时，再执行 [docs/DATABASE_WORKFLOW.md](./docs/DATABASE_WORKFLOW.md) 中的 migration-head 与 integration tests。
