# Yungu

`Yungu` 是一个包含 `Platform`、`Watchlist`、`Portfolio` 的 monorepo 工作区。

当前状态：

- `apps/platform`
  平台入口与 `Database Dashboard`，维护 `shared_asset` schema 中的 `Instruments / FX / NAV` 主数据，支持手工录入、CSV/Excel 导入、邮件刷新与历史查看，但不是其他 app 的运行时依赖。
- `apps/watchlist`
  已有可运行的前后端、数据库迁移、测试与文档，继续承载 fund-only watchlist / fund detail / facts / recalc 基线；Copilot 当前只保留后端扩展接口，默认 UI 不对外开放。
- `apps/portfolio`
  已有可运行的前后端、数据库迁移、交易与绩效内核，以及成体系的领域文档；部分 workspace 页面仍为占位实现。

当前阶段仍然坚持：

- 提供统一的平台目录
- 保持两个 app 可独立开发与独立运行
- 共享数据库只抽最小公共底座

## 目录

```text
yungu/
  apps/
    platform/
    watchlist/
    portfolio/
  packages/
    asset-core/
    ui/
    copilot/
  docs/
```

## 文档入口

- [docs/README.md](./docs/README.md)
  顶层文档索引，串起数据库工作流、平台边界和历史阶段记录。
- [apps/platform/README.md](./apps/platform/README.md)
  Platform app 的职责、启动命令和前端运行时配置。
- [apps/watchlist/README.md](./apps/watchlist/README.md)
  Watchlist app 的当前实现基线、启动方式和行为边界。
- [apps/portfolio/README.md](./apps/portfolio/README.md)
  Portfolio app 的领域范围、启动方式和导入/校验说明。

## 当前边界

- `apps/watchlist`
  承载 fund-only watchlist / fund detail / facts / read model / recalc 语境；Copilot 仅保留后端接口边界，不作为当前已发布 UI 能力。
- `apps/portfolio`
  承载 portfolio / account / transaction / performance / review 语境。
- `apps/platform`
  平台 landing / app switcher 与 `Database Dashboard`；只维护共享资产，不承载其他 app 的业务编排。
- `packages/asset-core`
  当前承载共享资产 contract、持久化 model 与 shared store helper：`asset_id`、`name`、identifiers、`asset_type`、`currency`、typed `market_data` 与最小 `quote_selection_policy`。
- `packages/ui`
  未来放统一设计系统和通用 UI primitives。
- `packages/copilot`
  预留的平台级 copilot plumbing 目录；当前主路径不依赖它。

## 当前原则

- 两个 app 保持解耦，不做业务模型融合。
- `Watchlist` 与 `Portfolio` 直接访问同一个 PostgreSQL 中的 `shared_asset` + 各自私有 schema，不通过 app-to-app HTTP 互相取数。
- 共享资产身份与 typed market facts / selector policy，不共享上层业务 read model。
- `Watchlist` 继续 fund/watchlist 语境。
- `Portfolio` 继续 portfolio/account/transaction/risk/review 语境。

## 开发工作流

### 数据库

```bash
cd /home/shaw/yungu/infra/postgres
docker compose up -d
```

默认单库 schema 划分：

- `shared_asset`
  共享资产、行情、净值、FX 等主事实。
- `watchlist`
  watchlist 自己的 read model、facts、recalc job 等私有数据。
- `portfolio`
  portfolio / account / transaction / performance / research 等私有数据。

### 后端迁移

```bash
cd /home/shaw/yungu/infra/shared_asset
alembic upgrade head

cd /home/shaw/yungu/apps/portfolio/backend
PYTHONPATH=. alembic upgrade head

cd /home/shaw/yungu/apps/watchlist/backend
PYTHONPATH=. alembic upgrade head
```

说明：

- `shared_asset` schema 由 `infra/shared_asset` 统一管理。
- `platform` backend 直接使用这套共享表，但不再拥有 shared schema 的迁移入口。

如果本地库已经跑脏或迁移链断过，直接执行：

```bash
cd /home/shaw/yungu
./infra/postgres/rebuild_local_schemas.sh
```

这个脚本会销毁并重建 `shared_asset / portfolio / watchlist` 三个 schema。

重建完成后：

- `portfolio` 不会再自动生成默认 demo 组合；需要时请在 `/portfolios` 页面显式创建，或运行导入脚本。
- `watchlist` 不会再自动写入示例标签值；标签与研究判断都只来自显式录入或后续真实数据处理。

### 后端测试

三个后端已经改成独立顶层包名，但测试和脚本仍建议在各自 backend 目录内执行，以复用本地 Alembic 配置和相对路径。

```bash
cd /home/shaw/yungu/apps/platform/backend
pytest

cd /home/shaw/yungu/apps/portfolio/backend
pytest

cd /home/shaw/yungu/apps/watchlist/backend
pytest
```

补充：

- 上面这组测试主要是快速 SQLite / isolated path。
- `shared_asset` 的 cross-schema FK 和 search_path 需要额外用 PostgreSQL integration tests 验证，命令见 [docs/DATABASE_WORKFLOW.md](/home/shaw/yungu/docs/DATABASE_WORKFLOW.md:55)。

### 前端构建

```bash
cd /home/shaw/yungu/apps/platform/frontend && npm run build
cd /home/shaw/yungu/apps/portfolio/frontend && npm run build
cd /home/shaw/yungu/apps/watchlist/frontend && npm run build
```
