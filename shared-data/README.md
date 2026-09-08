# Investment Studio Data

这里管理 Studio 公共数值、文本、共享资产事实，以及私有数据接入、解析、导入、修正和更新。四个业务 App 使用公共市场资料；Regime 自有模型数据库仍独立。对外只提供 CLI 和定时作业，不提供网页、HTTP 服务或公开端口；系统部署和服务运维属于 `infra/`。

私有摄取与资产维护配置前缀为 `INVESTMENT_STUDIO_DATA_`，公共市场层为 `INVESTMENT_STUDIO_MARKET_`。登录与导航独立放在 [Home](../home/README.md)，两者没有运行时依赖。

## 代码组织

```text
shared-data/
  studio_data/      数据接入、解析、数据操作与 CLI
  scripts/          数据导入与定时更新入口
  alembic/          data_ingestion 接入状态迁移
  market/           全市场数值、PIT、文本原文版本与数据包
  instruments/
    python/         共享资产模型、存储与数据合同
    ts/             前端共享数据类型
    alembic/        instrument_data 资产事实迁移
  tests/            数据处理与迁移测试
```

共享资产字段和读写边界见 [Instruments](./instruments/README.md)。这里统一的是数据代码，不把业务账本、观察列表或 Regime 数据库并入数据后台。

## 数据所有权

| 分区 | 职责 |
| --- | --- |
| `data_ingestion`（数据接入） | 供应商目录、邮件游标、附件、解析、候选路由、原始证据 |
| `instrument_data`（资产数据） | 资产身份、identifier、price/NAV/FX、公司行动、资产资料快照及数据血缘 |
| `market_data` / `market_text` | 公共数值目录、当前投影、PIT、文本版本及来源；历史 Parquet/raw 文件独立保存 |
| `briefing` | 日报周报、冻结输入与引用版本 |
| `watchlist` | 观察列表、分类、研究、监控及计算结果 |
| `portfolio` | 账户、交易、FCN/Option 合约、账本、持仓及组合计算 |

七个分区属于 Studio 同一数据库，写入所有权独立。Regime 使用 Studio 公共市场层，保留自身模型与运行数据库。完整数据流、时钟和文件恢复范围见 [Market Data Pipeline](../docs/MARKET_DATA_PIPELINE.md)。Watchlist 和 Portfolio 直接读取共享资产事实；新增市场资产、改价、净值导入、数据源配置均由后台维护。新增 FCN/Option 合约和记账仍是 Portfolio 的业务操作。

## CLI 维护

从仓库根目录调用 `bin/investment-studio`。`data` 命令读取仓库外 `data.env` 与 `market.env`，无需启动主页或任何 HTTP 服务。默认配置目录为 `~/.config/orataba/secrets/investment-studio`；服务器可通过 `ENV_ROOT` 指定配置目录。数据库密码、FMP/DataHub/邮箱密钥不进入仓库。

```bash
bin/investment-studio data --help
bin/investment-studio data instruments list --search AAPL
bin/investment-studio data instruments show INSTRUMENT_ID
bin/investment-studio data securities search AAPL
bin/investment-studio data status data
bin/investment-studio data status email --limit 100
```

需要 JSON 输入的操作使用 `--input /absolute/request.json`，也可 `--input -` 从标准输入读取。所有单项写命令都需 `--apply` 才执行；不加时只校验输入结构，不保证业务条件满足。股票/ETF 通过已同步的 FMP 目录建档：

```json
{"instrument_type":"equity","catalog_provider":"fmp","catalog_symbol":"AAPL","refresh_eod":true}
```

```bash
bin/investment-studio data securities add --input /absolute/security.json --apply
bin/investment-studio data instruments create --input /absolute/fund.json --apply
bin/investment-studio data instruments source-set INSTRUMENT_ID --input /absolute/source.json --apply
bin/investment-studio data instruments quote-policy-set INSTRUMENT_ID --input /absolute/policy.json --apply
bin/investment-studio data quotes set INSTRUMENT_ID --input /absolute/quote.json --apply
bin/investment-studio data fx set --input /absolute/fx.json --apply
bin/investment-studio data instruments archive INSTRUMENT_ID --input /absolute/operator.json --apply
```

输入合同位于 `studio_data/contracts.py`。正式市场数据写入必须包含明确的 `status`；不创建示例资产，不猜测净值口径。

### 净值文件、解析与修正

```bash
bin/investment-studio data nav preview FUND_ID /absolute/nav.xlsx
bin/investment-studio data nav import FUND_ID /absolute/nav.xlsx --status complete --apply
bin/investment-studio data nav import-text FUND_ID --input /absolute/nav-text.json --apply
bin/investment-studio data nav import-csv --help
bin/investment-studio data nav candidates FUND_ID --include-history
bin/investment-studio data nav candidate-confirm --help
bin/investment-studio data nav action-revise --help
bin/investment-studio data nav evidence-revise --help
```

`nav import` 不带 `--apply` 会读取文件并给出真实预览。多基金 CSV 使用原有受控导入器，自带预览及 `--apply`。修正保留 revision、原始证据、幂等键和下游重算边界；不要直接修改正式数据库表。数据已保存但下游通知失败时，CLI 明确报错并返回退出码 3，不把它说成回滚。

### 自动更新与运行状态

```bash
bin/investment-studio data jobs run --channel all --require-downstream-success --fail-on-item-failure --json
bin/investment-studio data jobs run --channel email --json
bin/investment-studio data jobs run --channel reference --fail-on-item-failure --json
bin/investment-studio data reference show INSTRUMENT_ID
bin/investment-studio data reference refresh INSTRUMENT_ID --apply
```

`jobs run` 是立即执行的维护作业，不是预览。现有 launchd/systemd 定时任务继续自动运行；`all` 包含邮件/行情/基金净值投影以及资料快照更新。Watchlist 从自己的只读接口读取已采集的共享资料，不因打开页面调用供应商。目录、附件和解析失败仍保留在原来的持久状态中，可用状态命令检查并通过作业重试。

## 迁移与运行

共享资产事实迁移位于 `shared-data/instruments`；私有接入状态位于 `shared-data/alembic`；公共数值和文本共用 `shared-data/market/alembic` 迁移链。统一使用 `infra/scripts/migrate_all.sh`；保留已有迁移链和版本标识，目录改名不复制或删除数据。

部署与本地服务分别见 [Server Deployment](../docs/SERVER_DEPLOYMENT.md) 和 [Local Service](../docs/LOCAL_MACOS_SERVICE.md)。价格、净值与收益语义仍遵循 [Fund NAV Event Model](../docs/FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md)。
