# Instruments

这里集中放置 Data、Watchlist 和 Portfolio 共享的资产模型、类型合同和数据库迁移。它只承载已经跨应用稳定复用的资产身份、typed market facts、quote selection policy、FX contract、corporate actions 和基金 NAV lineage。

完整字段、不变量和 selector 规则只在 [Instrument Core Contract](./INSTRUMENT_CORE_CONTRACT.md) 维护。

## 目录

```text
shared-data/instruments/
  python/investment_studio_instrument_core/  SQLAlchemy models、contracts 与 store
  ts/src/                               前端共享类型
  alembic/                              instrument_data schema 迁移
  INSTRUMENT_CORE_CONTRACT.md            canonical 语义合同
```

## 边界

共享层不包含：

- Watchlist membership、taxonomy、research 或 read model；
- Portfolio、account、transaction、ledger、holdings 或 risk；
- FCN、Option 和直接债券合约；
- 为单一调用方预留的兼容模型。

Watchlist 和 Portfolio 读取共享事实，但在各自 schema 内生成自己的业务派生结果。`instrument_data` schema 必须位于当前仓库 migration head；运行时不探测或修补旧物理 schema，升级由根目录统一迁移入口负责，见 [Migrations](./MIGRATIONS.md)。

`security_catalog` 复用市场目录查询与按需登记的请求合同，并桥接到数据维护端的 `investment-studio data securities` 命令。Portfolio 与 Watchlist 在各自 API 校验用户写权限和明确选择后调用，不持有另一套登记逻辑；搜索只读，登记与行情刷新仍由数据层负责。

## 验证

Python 合同随相关 backend 测试运行。TypeScript 合同单独检查：

```bash
npm --prefix shared-data/instruments/ts run typecheck
```

涉及 Registry migration、cross-schema FK 或 PostgreSQL constraint 时，按 [Database Workflow](../../docs/DATABASE_WORKFLOW.md) 运行 migration-head 和 PostgreSQL integration gates。
