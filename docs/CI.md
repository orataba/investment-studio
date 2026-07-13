# Continuous Integration

仓库的必需质量门禁名为 `Required quality gate`。它只有在应用测试、前端测试与构建、
PostgreSQL 迁移/集成测试/恢复生命周期测试全部成功后才通过。

## 固定运行环境

- Python 版本由根目录 `.python-version` 固定；uv 版本由 `uv.toml` 的 `required-version` 固定；依赖只从带 hashes 的
  `requirements/python.lock` 同步。editable workspace package 使用锁定的 setuptools/wheel 且关闭 build isolation。
- Node.js 版本由根目录 `.node-version` 固定，三个前端都只使用各自提交的 `package-lock.json` 与 `npm ci`。
- GitHub runner 固定为 Ubuntu 24.04，Actions 固定到完整 commit SHA，不跟随可移动 major tag。
- PostgreSQL CI 的服务端固定为 PostgreSQL 17.10，客户端固定并校验主版本 17，保证
  `pg_dump` / `pg_restore` 生命周期测试不会依赖 runner 预装版本；客户端 patch 由 PGDG 当前安全版本提供。

依赖安装与验证命令故意分开。验证脚本不会在运行过程中修改 lock file 或偷偷安装依赖。

## CI 分区

`Application tests` 执行三个后端的快速测试和可移植基础设施测试。快速测试显式排除
`postgresql_integration` marker，数据库专属行为不会在 SQLite 环境中伪装成已覆盖。

`Frontend tests and builds` 对 Platform、Portfolio、Watchlist 分别执行测试与生产构建。

`PostgreSQL migrations, integration, and recovery` 在临时 PostgreSQL 服务中：

1. 创建无 `CREATEDB` 的运行角色和仅具有 `CREATEDB` 的测试角色；
2. 将三条 Alembic 链从空库升级至全部 head，并用 `alembic current --check-heads` 复核；
3. 执行 PostgreSQL 专属约束测试；
4. 解析 JUnit 报告，任何零收集或 skip 都使门禁失败；
5. 执行数据库发布回滚和 dump 恢复回滚生命周期测试。

CI 不连接本机正式库、生产库或外部投资数据源，也不把空库当作可通过业务数据审计的正式库。
真实数据迁移仍须经过发布编排器及发布后零告警审计。

## 本地统一验证

首次运行或锁文件变化后：

```bash
infra/scripts/sync_python_env.sh
npm --prefix apps/platform/frontend ci
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/watchlist/frontend ci
```

随后可按与 CI 相同的入口运行：

```bash
infra/scripts/verify_repository.sh backend-fast all
infra/scripts/verify_repository.sh infra-portable
infra/scripts/verify_repository.sh frontend all
```

PostgreSQL 验证必须由调用进程显式提供隔离测试角色的 URL；测试会创建并删除随机数据库：

```bash
export PORTFOLIO_OPS_TEST_POSTGRES_URL="$YOUR_ISOLATED_TEST_DATABASE_URL"
infra/scripts/verify_repository.sh postgres-integration all
infra/scripts/verify_repository.sh database-lifecycle
```

空库迁移验证还需要按 [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md) 设置三套应用连接和
`PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE`，然后运行：

```bash
infra/scripts/verify_repository.sh migration-heads
```

不要把测试 URL 放入 backend runtime `.env`，也不要用具有正式数据访问权的角色运行集成测试。
