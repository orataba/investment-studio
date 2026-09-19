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

需要 JSON 输入的操作使用 `--input /absolute/request.json`，也可 `--input -` 从标准输入读取。所有单项写命令都需 `--apply` 才执行；不加时只校验输入结构，不保证业务条件满足。股票/ETF 通过已同步的 FMP 目录建档。FMP screener 目录请求显式包含全部股份类别（`includeAllShareClasses=true`），保留 GOOG/GOOGL 等同发行人的独立上市证券；不能把发行人去重结果当作完整证券目录。Watchlist 添加、Portfolio 直接交易、FCN/Option 底层证券和 FCN 实物交付选择复用这套 CLI 入口，并仅在用户明确选中或确认添加证券后按需建档；不新增独立的数据 HTTP 服务。注册和既有盘后更新会为 FMP 股票/ETF 补齐缺失行情；单标的补采复用共享数值采集器及其原始/复权数据合同，不改全市场采集角色或发行配置：

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

### 比特币现货

BTC/USD现货使用独立`crypto`身份和已采集的`market_series_daily/BTCUSD`，沿用共享数值库的来源版本与原文引用。`refresh instrument`的FMP路径将已完成UTC日线投影到Instrument Data；全年保留周末，未结束当日不发布close。不把供应商单位未明确的aggregate volume写成BTC数量或股票股数，不使用股票的拆股复权通道。完整保留的序列重放也会更新较早的来源修订，未变化的数据写入幂等。

FMP 股票/ETF 每次登记或周期更新都先检查共享历史是否覆盖最近已结束的交易日，缺少时按该标的补采，再重放已保留的完整 EOD 历史，使股息、拆股后的较早复权价格修订进入收益计算。重放本身只读本地共享事实；本机独有的公共证券也沿此路径持续更新，不需要复制 Watchlist 或 Portfolio 的业务状态到云端。明确的供应商失败记录为该标的失败，周期任务继续处理其他标的。FMP、BTC/USD 与 Tushare 的 canonical prices 和 raw OHLC 按单一资产、同一批次原子提交；写入失败同时回滚两侧事实和更新水位。

DataHub 的 `index_daily` 长期历史/缺失 OHLC 修复按已验证可接受的 366 个日历日窗口读取，每窗继续独立的 offset 分页，全部完成后才发布。窗口之间不重叠、不遗漏日期；后续窗口失败不提交前半份历史。其他接口保留各自已有的请求合同。

## 迁移与运行

共享资产事实迁移位于 `shared-data/instruments`；私有接入状态位于 `shared-data/alembic`；公共数值和文本共用 `shared-data/market/alembic` 迁移链。统一使用 `infra/scripts/migrate_all.sh`；保留已有迁移链和版本标识，目录改名不复制或删除数据。

部署与本地服务分别见 [Server Deployment](../docs/SERVER_DEPLOYMENT.md) 和 [Local Service](../docs/LOCAL_MACOS_SERVICE.md)。价格、净值与收益语义仍遵循 [Fund NAV Event Model](../docs/FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md)。
