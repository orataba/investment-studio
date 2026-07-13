# macOS 本地后台服务

项目提供六个用户级 `launchd` 常驻服务，在登录后自动启动三个 API 与三个前端；另有一个独立的一次性 LaunchAgent，每天本地时间 `21:00` 刷新行情并触发 Watchlist 与 Portfolio 下游重算。所有端口只绑定到 `127.0.0.1`，不会暴露给局域网。

## 依赖

- 项目根目录的 `.venv`
- Node.js 与 npm
- 本机 PostgreSQL，监听 `127.0.0.1:5432`

若使用 Homebrew，可安装并启动 PostgreSQL：

```bash
brew install postgresql@17
brew services start postgresql@17
```

## 安装或更新

在项目根目录运行：

```bash
CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
  infra/launchd/install_local_services.sh
```

`CONFIRM_RELEASE` 必须与 URL 解析出的 `database@host:port` 完全一致；日期必须是本次
Portfolio/Watchlist 全量重建采用的显式估值日期。安装器会校验并初始化本地数据库，
停止六个常驻 job 和定时刷新 job，再调用安全发布编排器创建已校验的发布前备份、
执行全部 Alembic 迁移、重建 Portfolio/Watchlist、通过零失败零告警审计并创建已校验的
发布后备份。随后才重建三个前端、更新并启动 `launchd` 服务。服务停止后的任一步失败
都会保持全部托管服务停止，供人工检查。安全数据库发布本身若在变更后失败，会先尝试
恢复发布前备份，且回滚成功也不会自动恢复服务；若数据库发布已经通过、随后前端构建或
LaunchAgent 更新/启动失败，则保留已审计的新数据库并继续停服。定时任务只在完整成功后
加载，且不会在安装时立即执行。

默认调度时间可以在安装时覆盖，例如：

```bash
CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
PORTFOLIO_OPS_LOCAL_REFRESH_HOUR=22 \
PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE=30 \
  infra/launchd/install_local_services.sh
```

`StartCalendarInterval` 使用 macOS 当前系统时区。电脑在 `21:00` 处于睡眠状态时，
`launchd` 会在下次唤醒后补跑一次，并把睡眠期间错过的多个触发合并成一次。
用户已注销或电脑关机时，用户级 LaunchAgent 没有加载；重新登录不会追补这类
触发，之后会在下一个 `21:00` 正常执行。锁屏但未注销不影响调度。

定时任务按 Tushare、邮件两个已配置通道增量刷新，失败条目会先重试两次。成功
写入后，Portfolio 快照刷新会在请求内同步完成，FX 变化会刷新所有组合；Watchlist
请求负责可靠地持久化或复用重算 job，后台 worker 随后异步完成实际物化。
任务通过 `fcntl` 非阻塞锁避免同一个 scheduled 脚本从 launchd 或终端重叠运行，
进程退出或崩溃时内核会自动释放锁。单项刷新失败、下游请求失败或任务异常都会
留下非零退出状态和原子写入的运行摘要，`KeepAlive=false` 因而不会形成无限重启
循环，下一个日历触发仍会正常运行。

Platform API 和定时任务都会读取当前
`~/.config/orataba/secrets/portfolio-operations-workbench/platform.env`，不再读取仓库
内的 runtime `.env`。秘密目录必须由当前用户拥有且权限为 `0700`，文件必须由
当前用户拥有且权限为 `0600`。安全加载器只接受
`PORTFOLIO_OPS_PLATFORM_*` 赋值，把值作为纯文本导入，不会执行 `$()`、反引号
等 shell 语法。安装器不会把 Tushare token、邮件密码等秘密复制进 plist；plist
只保存环境文件目录和本地数据库连接覆盖值。为了避免 Pydantic 再从第二来源补入
配置，安装器、三个 API runner 和定时 runner 都会拒绝任一 backend 目录中存在
`.env` 文件或软链接；先把其中的值迁移到外部秘密目录并删除该文件后再安装。

入口地址：

- Platform：`http://127.0.0.1:5172`
- Watchlist：`http://127.0.0.1:5173`
- Portfolio：`http://127.0.0.1:5174`

## 状态、日志与卸载

```bash
infra/launchd/status_local_services.sh
infra/launchd/uninstall_local_services.sh
```

日志位于 `~/Library/Logs/portfolio-operations-workbench/`，LaunchAgent 通过
`Umask=077` 将新日志限制为当前用户可读写。卸载只移除服务，不删除数据库、
研究输出、上传文档或日志。

定时刷新对应文件为：

- 标准日志：`~/Library/Logs/portfolio-operations-workbench/market-data-refresh.log`
- 错误日志：`~/Library/Logs/portfolio-operations-workbench/market-data-refresh.error.log`
- 最近一次运行摘要：`var/market-data-refresh-summary.json`

`status_local_services.sh` 会把定时任务的实际 plist 时间、运行次数、最近退出码及
摘要状态一起显示。需要立即手工执行同一任务时，可以运行：

```bash
launchctl kickstart "gui/$UID/com.orataba.portfolio-ops.market-data-refresh"
```

不要使用 `kickstart -k`；`-k` 会先终止正在进行的刷新。从其他终端直接运行同一个
`refresh_market_data_scheduled.py` 时，其默认锁路径与 launchd 一致，也会拒绝重叠
执行。Database Dashboard 的 UI 批量刷新是另一条入口，不受这个 scheduled-script
锁约束。

恢复项目 dump 时也必须显式声明目标和重建日期；该脚本不接受数据库 URL，而是使用
下列 `PORTFOLIO_OPS_DB_*` 参数：

```bash
PORTFOLIO_OPS_DB_HOST=127.0.0.1 \
PORTFOLIO_OPS_DB_PORT=5432 \
PORTFOLIO_OPS_DB_NAME=portfolio_ops \
PORTFOLIO_OPS_DB_USER=portfolio_ops \
CONFIRM_RESTORE=portfolio_ops \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=YYYY-MM-DD \
  infra/postgres/restore_project_dump.sh
```

恢复脚本会临时卸载当前已加载的六个常驻 job 和定时刷新 job，校验 checksum、archive
和实际数据库身份，创建恢复前安全备份，只恢复三个项目 schema，再执行全部迁移、
Portfolio/Watchlist 全量重建和零失败零告警审计。完整成功后才重新加载原先运行的 job；
恢复定时 job 时只重新注册日历计划，不会立即触发刷新。恢复、迁移、重建或审计失败会
先自动回滚数据库；回滚成功后恢复先前服务，回滚本身失败时服务保持停止并打印恢复
备份路径，避免在半恢复数据库上继续写入。
