# macOS 本地后台服务

项目提供八个用户级 `launchd` 常驻服务，在登录后自动启动四个 API 与四个前端；另有五个一次性数据任务，分别处理各市场盘后行情、Tushare 盘前资料和晚间净值结算。公共数据同步另行安装。所有端口只绑定到 `127.0.0.1`，不会暴露给局域网。

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

### 仅更新界面

不涉及 API、依赖或数据库的界面修改，无需运行下述完整安装器。先从已验证的提交，使用仓库锁定的 Node
版本和本地应用地址，将受影响前端构建到工作区外的独立目录（`--manifest --outDir <目录>`）；共享 UI
修改需构建 Home、Watchlist、Portfolio、Briefing 四个前端。保留构建提交、文件校验值与原 Web job 加载状态。

将上一版 manifest 引用的 `assets/` 文件复制到新包中，不覆盖新文件、不改新 manifest，保证已打开页面仍能
加载旧分块。旧包没有 manifest 时，首次保留其整个 `assets/` 目录；后续按 manifest 保留一代，避免无限累积。
只卸载原先已加载的 Web job，将旧 `dist` 整体移至持久回滚目录、完整新包移入对应 `dist`，再用原 plist
恢复原 Web job 集合。校验 HTML、资源及受影响交互后记录结果；失败时恢复旧 `dist` 和原加载状态。
此路径不改 plist、不重启 API、不迁移数据库，也不触发行情同步或 Regime 重建。

### 完整安装或更新

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
项目 schema 的迁移前 custom-format 备份，初始化身份表并执行业务迁移；刷新本次发布所需的股票/ETF 搜索目录后，按共享登记目录同步四个系统 Watchlist 的成员及行物化，保留用户名单、视图和研究，补齐已有 chart 的紧凑列表投影，再刷新失效的 Portfolio 快照、预计算最新可靠日期的持仓与风险页面结果并执行只读审计。安装不全量重采已登记证券的行情；行情和参考事实由公共数据同步及既有定时维护流程更新。目录或物化失败同样触发回滚，不依赖浏览器访问补齐目录。随后重建四个前端，以原子文件替换更新各 plist 并启动 `launchd`
服务。首次尝试 `bootstrap` 新服务前，迁移、构建或 plist 安装失败会恢复数据库备份
和旧 plist，再恢复此前加载的服务；恢复失败则保留私有恢复目录，不重新启动服务。
`RunAtLoad` 可能在健康检查前就产生新记录，因此安装器会在首次 `bootstrap` 前记录
`writers-may-have-resumed`。此后部分启动或健康检查失败，只卸载托管服务并保留新数据库
和新 plist，不自动回放旧备份。恢复目录中的 `phase` 记为 `forward-repair-required`，
并保留原服务状态、旧 plist 和备份路径；继续保持维护状态，基于现有数据修复后再恢复运行。
如某个服务无法卸载，安装器会明确报告，须先人工停止该服务。
校验后的迁移前备份默认保留在
`~/Library/Application Support/investment-studio/backups/`。晚间结算任务在加载后按已有运行状态判断是否补跑；市场行情和资料任务只注册日历计划，不在加载时额外执行。首次上线前先完成资料采集，再启动研究服务。所有批次共用独占写锁，定时刷新在下述有界窗口内等待已有批次完成。

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
| `market-data-refresh` | `settlement` | 上海 21:00；失败时 23:00 补跑 |

市场任务按标的配置的交易日历选择资产并跳过休市市场。盘后行情使用标的已配置的数据来源；
盘前资料任务仅采集 Tushare 参考资料；FMP 登记资产投影由公共数据到达流水线统一更新并保存失败状态；同市场 08:30 的研究优先读取共享原文及事件包，并按需要补充检索。
Watchlist 不依赖外部 DuckDB。港股和美股盘后任务在每小时 `:30` 检查实际交易日历，
仅在当天真实收盘后 30 分钟起的半小时内执行，覆盖半日市及休市日；两端共用
`infra/scripts/market_close_schedule.py`，不维护另一份冬夏令时或提前收盘日期表。

Mac 在日历时间睡眠时，`launchd` 会在唤醒后合并触发；盘后港股/美股任务若已错过目标半小时窗口，则等待下一个交易日，必要时显式补采。
安装器会停止并删除旧 `us-reference-data-refresh` plist；仅在首次启动新服务前的失败中
恢复旧定义和运行状态，跨过启动边界后的失败保留新定义并按上述方式修复。
定时刷新共用独占写锁，默认最多等待 900 秒；runner 可通过进程环境变量 `INVESTMENT_STUDIO_LOCAL_REFRESH_LOCK_WAIT_SECONDS` 覆盖。等待写入日志，超时明确返回 75，不启动并行写入或伪报成功。
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

## 公开数据与简报

`market.env` 明确 `ROLE=replica`；本地不排入全市场来源采集。Studio 完整数据使用下文的每周
云端快照同步，市场数值、原始资讯与业务数据在同一次切换中发布。完整同步启用后，停用旧的
`market-sync` 小时复制计划。安装器发现外部 `cloud-sync.json` 时自动使用完整同步定义。
`briefing.env` 设置 `EDITION_ROLE=preview`，正式报告的定时器仅在云端启用。
PostgreSQL 备份还需要配套公开数据目录，详见 [Market Data Pipeline](MARKET_DATA_PIPELINE.md)。

## 状态、日志与卸载

本地 Watchlist 研究工具默认回调 `8000`，并读取 Portfolio `8001` 与 Regime `3011` 的只读证据；Portfolio 标的风险默认连接 Watchlist `8000`。覆盖地址时使用各应用 `.env.example` 列出的命名空间配置。Watchlist 的本地 `watchlist.env` 可选，但开发命令直接启动 API 时仍需显式提供数据库 URL；常驻服务由安装器提供。Portfolio Research 在本地与云端默认启用并沿用逐组合权限；外部 `portfolio.env` 的 `INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED=false` 可显式关闭。升级时需清理此前为禁用入口而设置的旧值。

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

四个 API 的日志包含结构化 `http_request` 与后台操作开始/结束记录，可按请求编号关联总耗时、SQL 耗时和失败代码位置；浏览器耗时及代码异常记录写入 `home-api` 日志。记录不包含表单、响应正文、密码或研究内容。排查某次操作时先按发生时间和请求编号筛选，再检查对应计算或采集任务；字段合同见 [请求与任务诊断](SERVER_DEPLOYMENT.md#request-and-job-diagnostics)。本机没有 Nginx 时不产生该文档中的 Nginx 访问日志。

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
加载的八个 API/前端 job、五个行情/结算定时 job（以及尚未退役的旧资料任务）及公共数据同步 job，完成安全备份和数据库恢复/迁移后再加载。
晚间任务按 `RunAtLoad` 判断补跑，市场行情/资料任务重新注册日历计划。恢复失败会先自动回滚
数据库，再恢复这些服务；回滚本身失败时服务保持停止，避免在半恢复数据库上
继续写入。incoming dump 必须具有通过校验的 SHA-256 文件，不能跳过；停服后若
仍有客户端连接无法终止，恢复会在备份或 schema 删除前硬失败。恢复脚本与安装器
共用同一个 project-schema backup/rollback 原语。


多账号启用、历史作者认领、组合管理者指派和服务凭证配置见 [多账号体系](MULTI_ACCOUNT_SYSTEM.md)。本机免登录也须先迁移身份库、建立真实拥有者并配置后台服务身份；云端会话和权限独立配置。

## 每周从云端同步

云端是 Studio 业务数据的权威来源。本机每周接收一次云端快照；两端平时分别运行，
本机操作不会回传，下次同步会覆盖本机独有的列表、研究和交易。同步前的本机数据库与文件
保留在外部恢复目录中。Regime 自有模型数据库及运行目录不在覆盖范围内。

文件传输要求 [Homebrew rsync 3](https://formulae.brew.sh/formula/rsync)，先运行 `brew install rsync`。
同步脚本优先从 `/opt/homebrew/bin`、`/usr/local/bin` 定位程序，再检查版本；不使用 macOS
自带的旧版 rsync/openrsync。依赖不满足时，在下载数据库或克隆文件前直接报错，不切换传输实现或自动重试。
云端还须提供支持 `--rsyncable` 的 GNU gzip；开始数据库导出前检查该能力。本机使用 Python 解压，无须另装 gzip。

`bin/investment-studio cloud-sync --config /private/cloud-sync.json` 的流程为：

1. 云端以 PostgreSQL 一致性快照导出八个 schema，云端服务继续运行。custom dump 关闭内层压缩，
   流式送入 `gzip -n --rsyncable -1`；云端只保存压缩包，管道成功后才原子发布为不可变临时文件。
   云端暂存目录由服务用户的 `$HOME/.local/state/investment-studio/cloud-sync` 下 `mktemp` 私有创建，
   使用该用户实际主目录所在的数据盘，不占用可能较小的 `/tmp` 内存盘。
   本机用最近成功同步的 `cloud.pgdump.gz` 独立克隆作增量基线，rsync 复用相同数据块；
   没有旧压缩包时完整传输。旧包仅帮助减少传输字节，本次云端包的 SHA-256 校验通过后才解压与恢复。
   云端临时包在传输成功或失败后清理，本机保留已校验的压缩包及散列供后续同步使用。
2. 后台下载市场文件、研究产物、上传材料和保留的私募确认函，在独立本地数据库恢复；本机页面继续使用原库。
   市场目录先以 macOS/APFS 写时复制克隆为增量基线，各副本保持独立文件身份，云端已删除的文件也从暂存副本移除。
   完整保留数值 Parquet 历史、`numeric/raw` 原始响应和文本原件；仅排除 `numeric/outbox/`，
   其中的索引和 ZIP 是从已保留的批次及原件重新生成的数值传输缓存，本机完整快照和 Regime 均不读取它们。
   已有暂存副本中的此缓存也移除；云端原目录不变。数据库所引用的实际文件仍必须全部通过下一步校验。
3. 核对迁移版本、外键约束、市场文件大小、原文内容散列及研究产物引用。版本与当前代码
   不一致或缺文件时停止，不发布半份快照。
4. 最后短暂停止本机 Studio 服务，切换数据库和文件，再恢复原服务集合。存在独立活动写入
   时不抢占该事务；本次同步失败，等待下一次排程。云端在此期间不停机。
5. 首次启动新服务前持久化 `writers-may-have-resumed`，随后验证本机免登录全权限和各 API 健康。
   该边界之前失败恢复原数据库、文件和服务；之后部分启动或健康检查失败，只停止托管服务，
   保留新数据库和文件，记录 `forward-repair-required`，不能自动回滚而丢弃新写入。成功后记录同步时间，
   保留最近两次成功同步前的本机数据供恢复；更旧的已成功批次在新快照验收后清理。

人员 ID、历史署名和组合归属随云端数据同步；云端浏览器会话、一次性链接、服务令牌和 AI
委托不复制。本机保留自己的后台服务凭据和数据读取角色，继续使用显式 `local` 免登录模式。
云端仍使用真实账号及逐组合权限。复制时正在运行的任务不能继承云端进程；本机将它们明确
恢复为待重算或中断状态，不冒充已经完成。

配置为仓库外的 `0600` JSON 文件。数据库密码仍来自 `.pgpass`，`admin_url` 指向同一台本机
PostgreSQL 的维护账户，用于建立临时库与切换库名，不能指向云端。SSH 使用已配置的主机别名。
示例中的目录必须与本机和云端实际运行配置一致：

```json
{
  "ssh_host": "studio-cloud-market",
  "remote_user": "investment-studio",
  "remote_port": 55433,
  "remote_database_user": "investment_studio",
  "remote_database": "investment_studio",
  "database_url": "postgresql://investment_studio@127.0.0.1:5432/investment_studio",
  "admin_url": "postgresql://LOCAL_DATABASE_ADMIN@127.0.0.1:5432/postgres",
  "state_root": "/absolute/local/state/cloud-sync",
  "local_identity_url": "http://127.0.0.1:8002/api/auth/session",
  "health_urls": ["http://127.0.0.1:8000/api/health", "http://127.0.0.1:8001/api/health"],
  "files": {
    "market": {"remote": "/var/lib/investment-studio/market-data", "local": "/absolute/local/market-data"},
    "documents": {"remote": "/home/investment-studio/.local/share/investment-studio/watchlist-documents", "local": "/absolute/local/watchlist-documents"},
    "research": {"remote": "/home/investment-studio/.local/share/investment-studio/portfolio-research-outputs", "local": "/absolute/local/portfolio-research-outputs"},
    "evidence": {"remote": "/home/investment-studio/.local/share/investment-studio/private-evidence", "local": "/absolute/local/private-evidence"}
  }
}
```

首次手动同步成功后，增加 `--install-schedule` 安装每周日 09:00 的 LaunchAgent；登录、唤醒及每日
检查只补跑本周日 09:00 之后尚未完成的同步，不重复覆盖。首次手动同步不会把每周计划永久推迟到手动执行的星期。日志和 `last-success.json` 位于配置的 `state_root`；
每批 `cutover.json` 持久化切换阶段、原库名称与各文件位置，作为唯一恢复记录。
若进程在切换中断或恢复不完整，下次同步会在下载前停止。遇到 `writers-may-have-resumed`
或 `forward-repair-required` 时，先确认所有托管写入已停止，保留新数据库和文件并向前修复；
旧快照仅保留为取证与经核对后的恢复依据，不能直接覆盖已经发生的新事务。此前的失败可按
`cutover.json` 恢复整套数据库、文件和原服务。验收后将阶段标记为 `rolled_back`（恢复原数据）或 `published`（完成新快照），
再允许后续同步。失败批次不自动清理，排查后人工清理其暂存库及文件。恢复前停止本机写入，将记录的数据库与文件一起恢复，
再检查服务健康。不要只恢复数据库而遗漏文件。旧的单独公共市场复制计划应停用，避免与完整同步
重叠；本机的业务服务和独立 Regime 调度继续运行。
