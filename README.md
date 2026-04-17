# Yungu

`Yungu` 是一个包含 `Platform`、`Watchlist`、`Portfolio` 的 monorepo 工作区。

当前状态：

- `apps/platform`
  极轻平台入口，提供 landing、app switcher、共享 `Instruments / FX` API。
- `apps/watchlist`
  已有可运行的前后端、数据库迁移、测试与文档，继续承载 watchlist / fund detail / facts / recalc / copilot 基线。
- `apps/portfolio`
  已有可运行的前后端、数据库迁移、交易与绩效内核，以及成体系的领域文档；部分 workspace 页面仍为占位实现。

当前阶段仍然坚持：

- 提供统一的平台目录
- 保持两个 app 可独立开发
- 只抽最小公共底座

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

## 当前边界

- `apps/watchlist`
  承载 watchlist / fund detail / facts / read model / recalc / copilot 语境。
- `apps/portfolio`
  承载 portfolio / account / transaction / performance / review 语境。
- `apps/platform`
  极轻平台 landing / app switcher，只提供平台入口，不承载共享业务路由。
- `packages/asset-core`
  未来只放最小共享资产底座：`asset_id`、`name`、identifiers、`asset_type`、`currency`、typed `market_data` 与最小 `quote_selection_policy`。
- `packages/ui`
  未来放统一设计系统和通用 UI primitives。
- `packages/copilot`
  未来放平台级 copilot plumbing。

## 当前原则

- 两个 app 保持解耦，不做业务模型融合。
- 共享资产身份与 typed market facts / selector policy，不共享上层业务 read model。
- `Watchlist` 继续 fund/watchlist 语境。
- `Portfolio` 继续 portfolio/account/transaction/risk/review 语境。

## 开发工作流

### 数据库

```bash
cd /home/shaw/yungu/infra/postgres
docker compose up -d
```

### 后端迁移

```bash
cd /home/shaw/yungu/apps/platform/backend
PYTHONPATH=. alembic upgrade head

cd /home/shaw/yungu/apps/portfolio/backend
PYTHONPATH=. alembic upgrade head

cd /home/shaw/yungu/apps/watchlist/backend
PYTHONPATH=. alembic upgrade head
```

### 后端测试

三个后端都使用顶层包名 `app`，所以测试和脚本都应在各自 backend 目录内执行。

```bash
cd /home/shaw/yungu/apps/platform/backend
pytest

cd /home/shaw/yungu/apps/portfolio/backend
pytest

cd /home/shaw/yungu/apps/watchlist/backend
pytest
```

### 前端构建

```bash
cd /home/shaw/yungu/apps/platform/frontend && npm run build
cd /home/shaw/yungu/apps/portfolio/frontend && npm run build
cd /home/shaw/yungu/apps/watchlist/frontend && npm run build
```
