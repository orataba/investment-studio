# Portfolio Operations Workbench

`Portfolio Operations Workbench` 是一个包含 `Platform`、`Watchlist`、`Portfolio` 的 monorepo 工作区，用于投资组合运营、资产主数据、观察池研究和组合绩效/风险管理。

当前状态：

- `apps/platform`
  平台入口与 `Database Dashboard`，维护 `instrument_registry` schema 中的 `Instruments / FX / NAV` 主数据，支持手工录入、CSV/Excel 导入、邮件刷新与历史查看，但不是其他 app 的运行时依赖。
- `apps/watchlist`
  已有可运行的前后端、数据库迁移、测试与文档，承载 fund/ETF/index watchlist、local detail、人工研究评级、monitoring、recalc 与 read model；Copilot 当前只保留后端扩展接口，默认 UI 不对外开放。
- `apps/portfolio`
  已有可运行的前后端、数据库迁移、交易、绩效、持仓、风险、Allocation Lab 与 Policy Replay，以及成体系的领域文档。

当前架构约束：

- 提供统一的平台目录
- 保持两个 app 可独立开发与独立运行
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
    copilot/
  data/
    migration/
  docs/
  nav/
```

## 文档入口

- [docs/REFACTORING_BLUEPRINT.md](./docs/REFACTORING_BLUEPRINT.md)
  面向基金经理个人及小型协作团队的目标架构与不兼容重构计划，明确基金研究、人工评级、Allocation Research、外部 ETF Live 集成和交易纠错边界。
- [docs/CALCULATION_PUBLICATION_SPEC.md](./docs/CALCULATION_PUBLICATION_SPEC.md)
  Phase 3B 的精确 Decimal 计算、sealed input manifest、durable worker 与原子 publication 实施契约。
- [docs/CI.md](./docs/CI.md)
  统一质量门禁、本地复现命令与 PostgreSQL 零跳过集成测试规范。
- [docs/MARKET_DATA_RELIABILITY_SPEC.md](./docs/MARKET_DATA_RELIABILITY_SPEC.md)
  Phase 2 的 canonical quote series、append-only observation revision、freshness/reliability 与 TWR fail-closed 唯一规范。
- [docs/README.md](./docs/README.md)
  顶层文档索引，串起数据库工作流、平台边界和设计基线。
- [docs/USER_MANUAL.md](./docs/USER_MANUAL.md)
  面向公司同事的使用手册，覆盖 Platform / Watchlist / Portfolio 的日常操作边界。
- [docs/FRONTEND_DESIGN_BASELINE.md](./docs/FRONTEND_DESIGN_BASELINE.md)
  当前前端设计基线，约束白底数据终端、字体层级、tabs 与内容区节奏。
- [docs/MAC_MIGRATION_FREEZE.md](./docs/MAC_MIGRATION_FREEZE.md)
  WSL 到 Mac 迁移冻结清单，记录 Git 承载范围、本地状态边界和恢复步骤。
- [docs/NEW_MACHINE_RESTORE.md](./docs/NEW_MACHINE_RESTORE.md)
  新电脑从 GitHub 私有仓库恢复项目的步骤参考。
- [docs/LOCAL_MACOS_SERVICE.md](./docs/LOCAL_MACOS_SERVICE.md)
  macOS 本地后台服务、每日 21:00 行情刷新与自动重算任务的安装、状态检查、日志和卸载说明。
- [apps/platform/README.md](./apps/platform/README.md)
  Platform app 的职责、启动命令和前端运行时配置。
- [apps/watchlist/README.md](./apps/watchlist/README.md)
  Watchlist app 的当前实现基线、启动方式和行为边界。
- [apps/portfolio/README.md](./apps/portfolio/README.md)
  Portfolio app 的领域范围、启动方式和导入/校验说明。

## 当前边界

- `apps/watchlist`
  承载 fund/ETF/index watchlist / local detail / manual research rating / monitoring / read model / recalc 语境；Copilot 仅保留后端接口边界，不作为当前已发布 UI 能力。
- `apps/portfolio`
  承载 portfolio / account / transaction / performance / risk / allocation research 语境；资产配置入口统一命名为 `Allocation Lab`，历史政策模拟统一命名为 `Policy Replay`。
- `apps/platform`
  平台 landing / app switcher 与 `Database Dashboard`；只维护共享资产，不承载其他 app 的业务编排。
- `packages/instrument-core`
  当前承载共享资产 contract、持久化 model 与 shared store helper：`instrument_id`、`name`、identifiers、`instrument_type`、`currency`、typed `market_data` 与最小 `quote_selection_policy`。
- `packages/calculation-core`
  承载跨领域 calculation run、sealed manifest、durable job/fencing、immutable publication 与 current pointer 的共享数据库契约和 lifecycle repository；不承载 Portfolio/Risk/Watchlist 公式。
- `packages/ui`
  当前承载跨 app 的前端共享能力：语言上下文、语言选择器和通用样式；后续再扩展统一设计系统和 UI primitives。
- `packages/copilot`
  预留的平台级 copilot plumbing 目录；当前主路径不依赖它。

## 当前原则

- 两个 app 保持解耦，不做业务模型融合。
- `Watchlist` 与 `Portfolio` 直接访问同一个 PostgreSQL 中的 `instrument_registry`、共享 `calculation_registry` + 各自私有 schema，不通过 app-to-app HTTP 互相取数。
- 共享资产身份与 typed market facts / selector policy，不共享上层业务 read model。
- `Watchlist` 继续 fund/ETF/index 的研究与监测语境，不承载 portfolio 业务事实。
- `Portfolio` 继续 portfolio/account/transaction/performance/risk/allocation research 语境。
- Portfolio 创建和修改必须显式选择 `standard_taxonomy` 或 `external_etf_rotation`。前者以 taxonomy、`TargetSet`、Allocation Lab 和 `Allocation Policy Drift` 管理配置政策；后者仍使用账本、持仓、绩效、滚动风险和相关性，但不适用 allocation planning 能力。系统不根据组合名称、持仓或 ETF 类型推断运行模式。
- 前端视觉基线统一为白底、冷中性灰线条和表格优先的信息密度；不要再引入米黄、沙色或暖灰页面背景。

## 开发工作流

以下命令默认从仓库根目录执行；如果你在其他目录，先进入自己的本地 clone。真实 backend `.env` 不进入 Git；本机统一从 `~/.config/orataba/secrets/portfolio-operations-workbench/` 提供。纯开发态可在各 backend 目录建立被 Git 忽略的 `.env` 软链接；macOS 后台服务只从外部秘密目录安全加载，安装前必须移除这些仓库内 `.env` 文件或链接。不要把密钥值粘贴到聊天、issue、PR 描述或提交信息里。

### 数据库

```bash
(cd infra/postgres && docker compose up -d)
```

新电脑从零恢复时，优先按 [docs/NEW_MACHINE_RESTORE.md](./docs/NEW_MACHINE_RESTORE.md) 执行：先启动 PostgreSQL，再通过安全恢复包装器校验并恢复 `data/migration/` 里的当前项目级 dump。不要绕过包装器直接 `pg_restore`；也不要在恢复后运行 `./infra/postgres/rebuild_local_schemas.sh`，除非明确要清空恢复数据并重建空 schema。

默认单库 schema 划分：

- `instrument_registry`
  共享资产、行情、净值、FX 等主事实。
- `calculation_registry`
  共享计算运行、输入封存、任务租约/fencing 与不可变发布生命周期；领域 dependency/output 仍留在各自 schema。
- `watchlist`
  watchlist 自己的 read model、research rating、monitoring 和 recalc job 等私有数据。
- `portfolio`
  portfolio / account / transaction / performance / allocation research 等私有数据。

### 后端迁移

```bash
export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=portfolio_ops
(cd infra/instrument_registry && alembic upgrade head)
(cd infra/calculation_registry && PYTHONPATH=../../packages/calculation-core/python alembic upgrade head)
(cd apps/portfolio/backend && PYTHONPATH=. alembic upgrade head)
(cd apps/watchlist/backend && PYTHONPATH=. alembic upgrade head)
```

PostgreSQL migration 会在任何 DDL 前同时核对 URL 中的数据库名与实时
`current_database()`；该确认只能来自当前进程，不能由应用 `.env` 自动授权。

说明：

- `instrument_registry` schema 由 `infra/instrument_registry` 统一管理。
- `platform` backend 直接使用这套共享表，但不再拥有 instrument registry schema 的迁移入口。

如果本地库已经跑脏或迁移链断过，直接执行：

```bash
CONFIRM_REBUILD_DATABASE=portfolio_ops \
PORTFOLIO_OPS_LOCAL_POSTGRES_URL='postgresql://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  ./infra/postgres/rebuild_local_schemas.sh
```

这个脚本会销毁并重建 `instrument_registry / calculation_registry / portfolio / watchlist` 四个 schema。

重建完成后：

- `portfolio` 不会再自动生成默认 demo 组合；需要时请在 `/portfolios` 页面显式创建，或运行导入脚本。
- `watchlist` 不会再自动写入示例标签值；标签与研究判断都只来自显式录入或后续真实数据处理。

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

- Platform/Watchlist 的可移植测试仍使用 isolated path；Portfolio 普通 DB/API tests 使用已迁移、已 seed 的 session PostgreSQL template，并为每个测试克隆隔离数据库，需显式提供 `PORTFOLIO_OPS_TEST_POSTGRES_URL`。
- `postgresql_integration` marker 继续覆盖专属迁移、约束、search_path、worker 并发和恢复生命周期，命令见 [docs/DATABASE_WORKFLOW.md](./docs/DATABASE_WORKFLOW.md)。

### 统一质量门禁

CI 与本地验证共用同一个入口，避免工作流和开发命令逐渐分叉：

```bash
infra/scripts/verify_repository.sh backend-fast all
infra/scripts/verify_repository.sh infra-portable
infra/scripts/verify_repository.sh frontend all
```

PostgreSQL integration、空库 migration heads 和数据库发布/恢复生命周期的环境要求及命令见
[docs/CI.md](./docs/CI.md)。CI 中任何 PostgreSQL 测试 skip 或零收集都会失败。

### 前端构建

```bash
npm --prefix apps/platform/frontend run build
npm --prefix apps/portfolio/frontend run build
npm --prefix apps/watchlist/frontend run build
```

### macOS 本地后台服务

本机 PostgreSQL 就绪后，可一次完成迁移、前端构建、九个 `launchd`
常驻服务（含三个独立 worker）及每日 `21:00` 刷新/重算任务的安装或更新：

```bash
CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
  infra/launchd/install_local_services.sh
```

安装器会停止所有应用与刷新 writer，创建已校验的发布前备份，迁移并全量重建
Portfolio/Watchlist，通过零失败零告警审计并创建发布后备份后才启动新服务。停止服务后
任一步失败都会保持服务停止。数据库发布门禁本身若在变更后失败，会先尝试回滚到发布前
备份；门禁已通过、随后前端构建或服务启动失败时，保留已审计的新数据库并继续停服。

安装后从 `http://127.0.0.1:5172` 进入 Platform；详细状态、日志和卸载命令见
[docs/LOCAL_MACOS_SERVICE.md](./docs/LOCAL_MACOS_SERVICE.md)。

## 仓库卫生

- `nav/` 是冻结迁移要保留的 NAV 附件图片数据，已纳入 Git；后续新增大批量原始材料前先确认是否应进入仓库。
- `data/migration/` 存放可恢复的当前项目级数据库 dump 与校验文件；dump 只覆盖 `instrument_registry`、`portfolio`、`watchlist`，不要把 raw PostgreSQL data directory 或其他项目的共享基础库数据放进 Git。
- `apps/platform/backend/.env`、`apps/watchlist/backend/.env`、`apps/portfolio/backend/.env` 是本机秘密配置，必须保持 Git ignored；仓库只保留 `.env.example`，真实值放在 `~/.config/orataba/secrets/portfolio-operations-workbench/`。
- `node_modules/`、`dist/`、`*.db`、`*.sqlite*`、`__pycache__/`、`.pytest_cache/` 都是本地产物，不应进入提交。
- `.local-pg/`、`ref/`、虚拟环境和用户级 `systemd --user` unit 都是本机状态，不作为跨机器 Git 迁移载体；`.env.example` 模板仍保留在 Git 里用于说明配置项。
- 提交前至少跑一次三个后端测试和三个前端 build；PostgreSQL cross-schema 行为按需补跑 [docs/DATABASE_WORKFLOW.md](./docs/DATABASE_WORKFLOW.md) 里的 integration tests。
