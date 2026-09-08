# macOS 本地后台服务

项目提供八个用户级 `launchd` 常驻服务，在登录后自动启动四个 API 与四个前端；另有六个一次性数据任务，分别处理各市场盘后行情、盘前研究资料和晚间净值结算。所有端口只绑定到 `127.0.0.1`，不会暴露给局域网。

## 依赖

- 项目根目录的 `.venv`
- Node.js 与 npm
- 本机 PostgreSQL，监听 `127.0.0.1:5432`

若使用 Homebrew，可安装并启动 PostgreSQL：

```bash
brew install postgresql@17
brew services start postgresql@17
createuser --login --pwprompt investment_studio
createdb --owner investment_studio investment_studio
```

`createuser` 会交互读取密码，不把密码写进 shell history。也可以使用仓库的
`infra/postgres/docker-compose.yml`；该 profile 会创建同名角色与数据库。

## 安装或更新

在项目根目录运行：

```bash
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  infra/launchd/install_local_services.sh
```

`INVESTMENT_STUDIO_LOCAL_DATABASE_URL` 必须显式传入，目标数据库与登录角色必须已存在；
安装器不会猜测或创建另一套默认数据库，也不会重置角色密码。URL 不得包含密码；
先通过交互方式把认证信息配置到当前用户权限为 `0600` 的 `.pgpass`，避免 shell
history 和进程参数暴露凭据。

安装器会停止并等待旧服务退出，创建并校验 `identity / instrument_data / instrument_registry / data_ingestion / platform / portfolio / watchlist / market_data / market_text / briefing`
项目 schema 的迁移前 custom-format 备份，初始化身份表并执行业务迁移，重建四个前端，然后以原子文件替换更新各 plist 并启动 `launchd`
服务。任一迁移、构建、plist 安装或健康检查失败时，会先卸载新服务、恢复数据库备份
和旧 plist，再恢复此前加载的服务；数据库或 plist 回滚失败时所有托管服务保持停止。
校验后的迁移前备份默认保留在
`~/Library/Application Support/investment-studio/backups/`。晚间结算任务在加载后按已有运行状态判断是否补跑；市场行情和资料任务只注册日历计划，不在加载时额外执行。首次上线前先完成资料采集，再启动研究服务。所有批次共用现有非阻塞文件锁。

晚间结算时间可以在安装时覆盖，例如：

```bash
INVESTMENT_STUDIO_LOCAL_REFRESH_HOUR=22 \
INVESTMENT_STUDIO_LOCAL_REFRESH_MINUTE=30 \
INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  infra/launchd/install_local_services.sh
```

调度按以下市场时钟执行。Mac 系统时区须为 `Asia/Shanghai`：

| 任务后缀 | 通道 / 市场 | 运行时间 |
| --- | --- | --- |
| `cn-market-data-refresh` | `market / cn` | 上海 15:30 |
| `hk-market-data-refresh` | `market / hk` | 港股实际收盘后 30 分钟 |
| `us-market-data-refresh` | `market / us` | 美股实际收盘后 30 分钟 |
| `cn-hk-reference-data-refresh` | `reference / cn-hk` | 上海 08:00 |
| `us-reference-data-refresh` | `reference / us` | 纽约 08:00 |
| `market-data-refresh` | `settlement` | 上海 21:00；失败时 23:00 补跑 |

市场任务按标的配置的交易日历选择资产并跳过休市市场。盘后行情使用标的已配置的数据来源；
盘前资料任务读取共享数值库并更新登记资产投影；同市场 08:30 的研究优先读取共享原文及事件包，并按需要补充检索。
Watchlist 不依赖外部 DuckDB。港股和美股盘后任务在每小时 `:30` 检查实际交易日历，
仅在当天真实收盘后 30 分钟起的半小时内执行，覆盖半日市及休市日；两端共用
`infra/scripts/market_close_schedule.py`，不维护另一份冬夏令时或提前收盘日期表。

`launchd` 日历不支持独立时区，美股盘前资料任务在每小时 `:00`、`:30` 轻量检查纽约时间，
只在当地 08:00 起的半小时内执行实际刷新；使用 IANA `America/New_York` 自动处理夏令时。
其他时段直接退出，不修改上一次刷新结果。Mac 在日历时间睡眠时，`launchd` 会在唤醒后
合并触发；盘后港股/美股和美股盘前任务唤醒时若已错过目标半小时窗口，则等待下一个交易日，必要时显式补采。
注销或关机期间用户级任务不运行，锁屏不影响调度。

晚间 `settlement` 更新 FMP 目录、Tushare 基金净值、邮件净值、FX 和私募基金投影，
不重复扫描全市场股票行情与参考资料。条目失败沿用两次重试；21:00 完整成功时，23:00
仅检查运行状态并退出。行情或净值成功写入后，Portfolio 同步刷新快照，Watchlist 持久化
重算 job 后由后台 worker 完成物化；reference 批次不触发无关的价格重算或组合审计。

单项或下游失败留下非零退出状态和原子摘要。行情/结算任务还执行只读数据审计；
资料任务以采集结果判定成败，其运行状态的 `audit_exit_code` 为 null。
每个任务使用独立的 `<任务后缀>-summary.json` 和 `<任务后缀>-run-state.json`，
避免晨间结果覆盖晚间补跑状态。`KeepAlive=false`，市场任务不增加额外重试循环。

数据维护 CLI 与定时 runner 固定使用 `data_ingestion, instrument_data, public`
search-path 顺序。邮箱目录游标、附件解析和重试状态写入私有 `data_ingestion` schema，
canonical 单位净值/复权累计净值才写入 `instrument_data`；安装或恢复的备份会同时
覆盖两者，避免只恢复行情结果却丢失 ingestion checkpoint 后重复全量扫描。

数据维护 CLI 和定时任务读取
`~/.config/orataba/secrets/investment-studio/data.env` 与 `market.env`；Briefing 使用 `briefing.env` 和 `market.env`。主页读取同目录的 `home.env`，仅拥有项目 PostgreSQL 中的 `identity` schema，不读取业务数据。各模块不读取仓库
内的 runtime `.env`。秘密目录必须由当前用户拥有且权限为 `0700`，文件必须由
当前用户拥有且权限为 `0600`。安全加载器只接受
对应模块和统一身份配置前缀的赋值，把值作为纯文本导入，不会执行 `$()`、反引号
等 shell 语法。安装器不会把 DataHub API key、邮件密码等秘密复制进 plist；数据库
连接供 Home、Watchlist、Portfolio、Briefing API 与需要数据访问的定时任务使用；四个纯 web job 不携带数据库
配置。数据库 URL 不含密码，认证使用私密 `.pgpass`。为了避免 Pydantic 再从第二来源补入配置，安装器、四个 API runner 和定时
runner 都会拒绝任一 backend 目录中存在
`.env` 文件或软链接；先把其中的值迁移到外部秘密目录并删除该文件后再安装。

入口地址：

- Investment Studio：`http://127.0.0.1:5172`
- Watchlist：`http://127.0.0.1:5173`
- Portfolio：`http://127.0.0.1:5174`
- 日报／周报：`http://127.0.0.1:5175`，本地只生成预览
- Regime：`http://127.0.0.1:3011`，由 `apps/regime/deploy/launchd/install.sh` 独立安装和管理

本机采用显式免登录模式：Home 的外部 `home.env` 设置 `INVESTMENT_STUDIO_HOME_ENVIRONMENT=local` 与 `INVESTMENT_STUDIO_HOME_AUTH_MODE=local`；各业务应用（含独立 Regime）的外部运行配置设置 `INVESTMENT_STUDIO_AUTH_MODE=local`，并指向本机 Home 身份服务。先迁移 `identity` 并显式初始化真实拥有者，再启用业务；启动程序不会创建默认用户。

仅从 loopback 连接和地址访问时，无凭证请求才以当前团队拥有者获得本机全部业务权限，页面显示“本机全权限”，不要求登录或退出。新操作仍记录真实人员，旧署名保留。显式传入的其他账号或短期任务凭证仍按原范围处理；模型不能借本机权限跨出任务或工具范围。后台定时任务继续使用独立服务凭证。云端必须使用账号模式，本机配置不能原样复制到云端。

## 公开数据复制与简报

`market.env` 明确 `ROLE=replica`；本地不排入全市场来源采集。使用
`infra/scripts/install_market_pipeline.py --scheduler launchd --role replica --env-root /absolute/external/config`
写出每小时与登录后补齐数据的任务，再通过 `launchctl` 加载。资讯源与云数值发行目录必须显式配置。
Mac 离线后的遗漏按数据包回执补齐；首次运行前应导入规范数值、原始资讯并核对覆盖。
`briefing.env` 设置 `EDITION_ROLE=preview`，正式报告的定时器仅在云端启用。
PostgreSQL 备份还需要配套公开数据目录，详见 [Market Data Pipeline](MARKET_DATA_PIPELINE.md)。

## 状态、日志与卸载

本地 Watchlist 研究工具默认回调 `8000`，并读取 Portfolio `8001` 与 Regime `3011` 的只读证据；Portfolio 标的风险默认连接 Watchlist `8000`。覆盖地址时使用各应用 `.env.example` 列出的命名空间配置。Watchlist 的本地 `watchlist.env` 可选，但开发命令直接启动 API 时仍需显式提供数据库 URL；常驻服务由安装器提供。个人 Portfolio Research 由外部 `portfolio.env` 的 `INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED` 控制。

行情/结算任务摘要的 `status=succeeded` 只表示数据刷新阶段成功；最终结果还包括只读审计。应同时查看对应 `*-run-state.json` 的 `status`、`refresh_exit_code` 和 `audit_exit_code`，以及 launchd 最近退出码。升级迁移后必须同步当前源码再执行审计，不能手工把失败记录改成成功。

```bash
infra/launchd/status_local_services.sh
infra/launchd/uninstall_local_services.sh
```

日志位于 `~/Library/Logs/investment-studio/`，LaunchAgent 通过
`Umask=077` 将新日志限制为当前用户可读写。每次安装或更新在服务停止后会压缩超过
100 MiB 的 `.log` 文件，仅保留末尾 10 MiB；阈值可分别通过
`INVESTMENT_STUDIO_LOCAL_LOG_MAX_BYTES` 与 `INVESTMENT_STUDIO_LOCAL_LOG_RETAIN_BYTES`
覆盖。卸载只移除服务，不删除数据库、研究输出、上传文档或日志。

晚间结算对应文件如下；其余任务将 `market-data-refresh` 替换为上表任务后缀：

- 标准日志：`~/Library/Logs/investment-studio/market-data-refresh.log`
- 错误日志：`~/Library/Logs/investment-studio/market-data-refresh.error.log`
- 最近一次摘要与运行状态：`~/.local/state/investment-studio/market-data-refresh-{summary,run-state}.json`

`status_local_services.sh` 会把定时任务的实际 plist 时间、运行次数、最近退出码及
摘要状态一起显示。需要立即手工执行晚间结算时，可以运行：

```bash
launchctl kickstart "gui/$UID/com.orataba.investment-studio.market-data-refresh"
```

不要使用 `kickstart -k`；`-k` 会先终止正在进行的刷新。从其他终端直接运行同一个
`refresh_market_data_scheduled.py` 时，其默认锁路径与 launchd 一致，也会拒绝重叠
执行。单资产 CLI 修正属于另一条显式数据写入操作；运行全量定时批次时不要同时修改同一资产。

执行 `infra/postgres/restore_project_dump.sh` 时，恢复脚本会临时卸载当前已
加载的八个 API/前端 job、六个行情/结算定时 job 及公共数据同步 job，完成安全备份和数据库恢复/迁移后再加载。
晚间任务按 `RunAtLoad` 判断补跑，市场行情/资料任务重新注册日历计划。恢复失败会先自动回滚
数据库，再恢复这些服务；回滚本身失败时服务保持停止，避免在半恢复数据库上
继续写入。incoming dump 必须具有通过校验的 SHA-256 文件，不能跳过；停服后若
仍有客户端连接无法终止，恢复会在备份或 schema 删除前硬失败。恢复脚本与安装器
共用同一个 project-schema backup/rollback 原语。


多账号启用、历史作者认领、组合管理者指派和服务凭证配置见 [多账号体系](MULTI_ACCOUNT_SYSTEM.md)。本机免登录也须先迁移身份库、建立真实拥有者并配置后台服务身份；云端会话和权限独立配置。
