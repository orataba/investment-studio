# Developer Guide

本文是新开发者或 AI 接手项目时的第一入口。目标是先建立正确的系统模型，再定位到细节文档和代码；
它不复制计算规格、数据库字典或部署手册中的完整内容。

## 1. 十五分钟建立项目模型

依次阅读：

1. 本文：知道模块归属、数据流、计算时钟和变更检查范围。
2. [ARCHITECTURE.md](./ARCHITECTURE.md)：知道什么可以共享、什么必须留在 app 私域。
3. [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md)：知道单库八 schema、迁移顺序和测试数据库边界。
4. 对应 app 的 README 和 docs：只深入当前任务涉及的模块。
5. 涉及 Portfolio 指标时，先查
   [01_CALCULATION_SPEC.md](../apps/portfolio/docs/01_CALCULATION_SPEC.md)，不要从页面文案或单个函数反推口径。

接手任何任务先做三个只读检查：

```bash
git status --short --branch
git log -5 --oneline
infra/scripts/verify_repository.sh static
```

不要把任务记录、旧 commit、测试数量或生产数据量当作当前状态。当前 checkout 以 Git 和 migration
源码为准，当前运行数据以只读 audit 为准。

## 2. 一张图理解边界

```text
数据供应商 / 人工文件 / 邮件证据
                  |
                  v
        Data ingestion (`data_ingestion`)
                  |
                  v
共享身份与 canonical 市场事实 (`instrument_data`)
             /                         \
            v                           v
Watchlist (`watchlist`)          Portfolio (`portfolio`)
观察、单资产研究、监控            账户、交易、账本、组合计算、研究
```

八个 schema 在同一个 PostgreSQL database 中；Home 只在 `identity` 持有账号、会话与服务凭证。Watchlist 和 Portfolio 直接读取 `instrument_data`；四个业务 App 的公共数值与文本证据由 `market_data`、`market_text` 和外部不可变文件提供，Briefing 报告写入 `briefing`。Home 只承担账号、会话和导航，核心共享事实不经由 Home API 获取。研究、风险与页面助手按明确合同调用其他 App 的只读接口。共享数据维护和摄取由 CLI/定时任务执行。

外部 Market Information Feed 只负责采集、清洗和传送文本包。Studio 自己采集数值、保留 PIT 与原始响应，不依赖其他项目的数据库。完整数据流见 [Market Data Pipeline](./MARKET_DATA_PIPELINE.md)。

### 2.1 写入所有权

| 事实或状态 | 唯一写入方 | 不能放在哪里 |
| --- | --- | --- |
| instrument、identifier、canonical NAV/price/FX、quote policy | 后台 CLI → `instrument_data` | Watchlist 或 Portfolio 的私有表 |
| 邮件游标、附件证据、解析任务、候选路由 | 后台摄取 → `data_ingestion` | `instrument_data` |
| watchlist membership、单资产研究、monitoring、recalc read model | Watchlist | Instrument Data 或 Portfolio |
| portfolio、account、transaction、FCN/Option contract、ledger、snapshot、taxonomy、research run | Portfolio | Instrument Data 或 Watchlist |
| 稳定共享资产合同与 DB helper | `shared-data/instruments` | app 之间复制一份近似合同 |
| 公共数值、PIT、文本原文版本与数据包目录 | `shared-data/market` → `market_data` / `market_text` + 不可变文件 | 其他项目数据库或各 App 私有表 |
| 日报周报及绑定的证据版本 | Briefing → `briefing` | 采集服务器 |
| 已经跨 app 稳定复用的 UI 基础能力 | `packages/ui` | 为单一页面预先建立通用框架 |

FCN 和 Option 是 Portfolio-local 不可变合约，不是共享市场资产。只有 underlying 或 deliverable
证券引用 Instrument Data。直接债券当前不进入 Instrument Data、Watchlist 或 Portfolio 交易主链路。

## 3. 目录与代码定位

```text
home/                         登录与导航主页，不是业务 App
  backend/home_api/
  frontend/src/
shared-data/                  Watchlist/Portfolio 的 CLI 数据维护与定时作业，无 HTTP
  studio_data/
  alembic/
apps/watchlist/                watchlist、单资产详情、monitoring、recalc
  backend/watchlist_app/
  frontend/src/
apps/portfolio/                组合运营、计算、风险、研究
  backend/portfolio_app/
  frontend/src/
apps/briefing/                日报周报、引用与阅读界面
shared-data/market/            公共数值、PIT、文本与数据包
apps/regime/                  独立 Git 子模块、自有模型与运行；安装 Studio market 包读取公共数据
shared-data/instruments/       共享资产合同、模型、store helper 与 Alembic 迁移
packages/ui/                   小而稳定的跨 app UI 基础能力
infra/launchd/                 macOS 托管服务和每日刷新
infra/systemd/                 Linux 用户级 systemd 部署
infra/scripts/                 统一迁移、审计和质量门
docs/                          当前仓库级合同与手册
```

主页包为 `home_api`，只持有 `identity` 的账号、会话与服务凭证，不读取业务账本。后台维护包为 `studio_data`、`studio_market`，业务包为 `watchlist_app`、`portfolio_app`、`briefing_app`；各包在同一个 PostgreSQL 数据库中维护明确分区。Regime 继续使用独立 Python 环境。

### 3.1 修改什么，至少检查什么

| 修改类型 | 主要代码 | 同步检查 |
| --- | --- | --- |
| 新增或修改资产类型、identifier、quote contract | `shared-data/instruments`、Instrument Data migration、后台 CLI | Watchlist/Portfolio DB constraints、API contract、前端类型、搜索/导入、跨 schema PostgreSQL tests |
| 修改供应商或市场数据选择 | Data ingestion/Instrument Data store | source entitlement、币种/scale、availability/freshness、quote policy、下游 stale/recalc |
| 修改 Watchlist 指标 | Watchlist canonical recalc/read model | `RETURN_SERIES_CONTRACT.md`、详情页和主表字段、monitoring、导出 |
| 修改交易 | Portfolio command/store/ledger | Preview/Commit、日期与金额合同、lots/obligations、cash posting、snapshot invalidation、导入导出、审计日志 |
| 修改 Portfolio 计算 | Portfolio calculation service | `01_CALCULATION_SPEC.md`、Holdings 字典、coverage/unavailable 语义、`calculation_version` 和重建路径 |
| 修改 taxonomy 或 Research solve | Portfolio taxonomy/research services | effective date、PIT 输入、target 完整性、Risk/Risk Budget eligibility、历史模拟披露 |
| 修改数据库结构 | 对应 Alembic chain | 单库八 schema 依赖顺序、升级数据、恢复路径、migration-head 和 PostgreSQL integration tests |
| 修改部署脚本 | `infra/launchd` 或 `infra/systemd` | 停写、备份、迁移、回滚、原服务集合恢复、loopback 网络边界、shell tests |

复杂文件不因行数大就机械拆分。Portfolio 的账务和计算内核有大量相互约束的语义；只有存在明确职责边界、
重复实现或可验证缺陷时才重构。新 abstraction 必须有当前消费者，不为假设中的未来兼容层预留。

## 4. 数据与计算时钟

项目中的“日期”不是一个字段。修改或排查前先确认使用的是哪一个时钟。

### 4.1 市场数据

- `as_of_date`：价格、NAV 或 FX 代表的市场/净值日期。
- source observation 与摄取时间：证明系统何时获得该事实，不能反向改写 `as_of_date`。
- expected frequency、market calendar、release lag：决定 freshness；不能用固定自然日阈值代替。
- canonical projection/materialization version：决定已有证据是否需要重新投影或重算。

基金只有 `official_nav`（单位净值）和 `total_return_nav`（分红再投资复权累计净值）两种 canonical
身份。现金分红简单累加值不是总回报序列；证据不足时保持 unavailable。

### 4.2 交易与组合

| 时钟 | 含义 |
| --- | --- |
| `trade_date` / `trade_time` | 成交、定价和同日顺序 |
| `position_effective_date` | 份额进入或离开 EOD 持仓 |
| `entitlement_date` | 收入权利确认 |
| `settlement_date` | settled/pending cash 转换 |
| snapshot `as_of_date` | EOD 组合估值和收益边界 |

不要用一个日期同时猜测全部语义。详细合同见
[04_TRANSACTION_OPERATIONS.md](../apps/portfolio/docs/04_TRANSACTION_OPERATIONS.md)。

### 4.3 事实、派生与重建

交易、账户、Instrument Data 行情和 FX 是事实或必要配置。ledger postings、lots、daily snapshots、holding
snapshots、contribution slices 和大部分页面 payload 是可重建派生结果。源事实变更后先标记受影响组合
stale，由 durable worker 串行重建；读路径不能临时维护第二套真相。

计算 payload 或口径发生变化时：

1. 修改唯一 canonical 实现和规格；
2. 提升相应 `calculation_version`；
3. 验证旧物化结果会被识别为 stale 并可重建；
4. 检查完整、部分覆盖和 unavailable 三种输出；
5. 不增加永久旧 payload 兼容分支。

## 5. 指标和设计的唯一入口

| 问题 | Canonical 文档 |
| --- | --- |
| Portfolio NAV、TWR、IRR、回撤、波动率、Sharpe、Tracking Error、归因、HHI、drift、risk contribution、scenario、Research solve | [Portfolio 计算规格](../apps/portfolio/docs/01_CALCULATION_SPEC.md) |
| Holdings 每列和分组聚合 | [Holdings 字段标准](../apps/portfolio/docs/03_HOLDINGS_FIELD_REFERENCE.md) |
| 绩效方法和 GIPS 声明边界 | [GIPS alignment](../apps/portfolio/docs/02_GIPS_ALIGNMENT.md) |
| Watchlist 图表、滚动/日历区间、频率、缺点和共同样本 | [Watchlist 收益序列合同](../apps/watchlist/docs/RETURN_SERIES_CONTRACT.md) |
| 基金 NAV 证据、行为、projection 和下游重算 | [Fund NAV event model](./FUND_NAV_EVENT_AND_RECALCULATION_MODEL.md) |
| 表、字段、PK/FK 和写入所有权 | [Portfolio database dictionary](./PORTFOLIO_DATABASE_DICTIONARY.md) |
| 前端视觉与跨 app UI 边界 | [Frontend design baseline](./FRONTEND_DESIGN_BASELINE.md) |

实现、测试和页面文案与规格冲突时，不要悄悄修改规格来迁就错误实现；先确认 canonical 目标，再修复一方。
同一个公式只在其 canonical 文档维护，其他地方用链接和一句边界说明。

## 6. 本地开发与本机长期运行

项目固定的 Python、uv 和 Node 版本见根目录 README。首次安装或锁文件变化时：

```bash
infra/scripts/sync_python_env.sh
npm --prefix shared-data/instruments/ts ci
npm --prefix home/frontend ci
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/briefing/frontend ci
```

运行时 secret 只从仓库外目录或显式进程环境加载；各 backend 目录只保留 `.env.example` 键名模板，
不创建 `.env` 或 symlink。密码使用权限为 `0600` 的 `.pgpass`，不要写进 URL、文档、聊天或 Git。

需要八个 API/前端服务和每日刷新长期运行时，使用受控安装器：

```bash
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  infra/launchd/install_local_services.sh
```

公共 Parquet、原文文件和 Regime source writer 的恢复/停写边界见 Database Workflow。安装器负责停写、八 schema 备份、顺序迁移、派生快照重建、只读数据审计、失败恢复和健康检查。状态、日志与卸载见 [LOCAL_MACOS_SERVICE.md](./LOCAL_MACOS_SERVICE.md)；代码、secrets 和数据库从零恢复见 [NEW_MACHINE_RESTORE.md](./NEW_MACHINE_RESTORE.md)。不要在本文复制两个 runbook 的完整命令。

## 7. Linux 服务器部署

使用 [SERVER_DEPLOYMENT.md](./SERVER_DEPLOYMENT.md) 中的 user-systemd 安装器。部署完成不等于可公开访问：

- 八个原生服务默认只绑定 `127.0.0.1`；
- Home 提供真实账号与共享会话；各业务 API 自行验证身份、团队和资源范围，Portfolio 按组合授予 manager/editor/viewer 权限；
- 远程入口必须由经过审核的反向代理终止 TLS，并同时保护页面和 `/api`；
- 不要直接公开 `310x`、`810x` 或 PostgreSQL 端口，也不要把云控制台登录误当成页面访问控制；
- 内部维护使用服务器本机或受控 SSH tunnel，不能以此替代公网入口的访问控制验收。

供应商 transport 等会随部署环境变化的限制只在 Server Deployment 维护，接手时按当前配置重新核对，不在多份文档复制带日期的探测结论。

## 8. 验证与阶段验收

普通改动至少运行与修改直接相关的测试，然后运行统一门禁：

```bash
infra/scripts/verify_repository.sh all-local
```

它覆盖仓库卫生、Markdown 链接、Home、Data、Market、Portfolio、Watchlist、Briefing 六组 backend 快速测试、shared TypeScript、四个 frontend test/build
以及便携 infra tests。

涉及 migration、search path、cross-schema FK、PostgreSQL constraint 或数据库并发时，按
[DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md) 把七条 migration target 和 PostgreSQL integration URL
指向专用测试数据库，再运行 `migration-heads` 与 `postgres-integration`。`migration-heads` 会实际应用
migration，不是只读检查；不得指向真实业务库。对真实运行库只执行只读发布审计：

```bash
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  .venv/bin/python infra/scripts/audit_live_data.py --fail-on-warning --json
```

阶段完成至少同时满足：

- Git diff 只包含任务内修改，没有 secret、runtime DB、构建产物或缓存；
- 相关回归测试和 `all-local` 通过；
- 涉及 PostgreSQL 行为时专用库 integration tests 通过且没有 skip；
- 当前 migration heads 与 live audit 通过；
- 本机或服务器目标的八个 API/前端服务及 Regime 独立服务与健康检查通过；
- 文档描述当前合同；一次性 Review、发布数字和排查证据留在任务或版本历史，不进入长期文档；
- 没有具体高严重度缺陷；暂不支持的产品能力明确写成边界，不用 fallback 假装支持。

## 9. 当前明确边界

- Watchlist 主链路只覆盖公募、私募、ETF、股票和指数；cash、FX、other 没有详情工作面。
- Documents 只具备现有数据/附件能力，没有完整通用文档工作台；PDF/图片 OCR 和官方指数方法论文档摄取未实现。
- Portfolio 支持股票／ETF 的显式卖空与回补；不支持直接债券、融资、PE/VC capital call、基金份额转换或衍生品 transfer。
- FCN/Option 没有 daily fair value、Greeks、自动 barrier/行权或 covariance risk；相关持仓按明确的 carrying/liability 口径披露。
- Portfolio Research 历史曲线是 point-in-time policy simulation，不是实盘绩效；执行模型不包含拒单、部分成交、容量和 market impact。
- 共享登录会话由 Home 提供；远程访问须通过配置了 TLS 和页面/API 会话校验的受控入口。
- 远程访问和供应商 transport 的当前限制以 [SERVER_DEPLOYMENT.md](./SERVER_DEPLOYMENT.md) 为准。

新增能力时以真实业务需求和可审计事实为前提。不要把未支持对象塞进 `other`、notes 或兼容字段，
也不要用自动 fallback、重试或默认值掩盖口径缺失。
