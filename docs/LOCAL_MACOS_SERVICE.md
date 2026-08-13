# macOS 本地后台服务

项目提供六个用户级 `launchd` 常驻服务，在登录后自动启动三个 API 与三个前端；另有一个独立的一次性 LaunchAgent，在加载时及每天本地时间 `21:00` 刷新行情并触发 Watchlist 与 Portfolio 下游重算。所有端口只绑定到 `127.0.0.1`，不会暴露给局域网。

## 依赖

- 项目根目录的 `.venv`
- Node.js 与 npm
- 本机 PostgreSQL，监听 `127.0.0.1:5432`

若使用 Homebrew，可安装并启动 PostgreSQL：

```bash
brew install postgresql@17
brew services start postgresql@17
createuser --login --pwprompt portfolio_ops
createdb --owner portfolio_ops portfolio_ops
```

`createuser` 会交互读取密码，不把密码写进 shell history。也可以使用仓库的
`infra/postgres/docker-compose.yml`；该 profile 会创建同名角色与数据库。

## 安装或更新

在项目根目录运行：

```bash
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  infra/launchd/install_local_services.sh
```

`PORTFOLIO_OPS_LOCAL_DATABASE_URL` 必须显式传入，目标数据库与登录角色必须已存在；
安装器不会猜测或创建另一套默认数据库，也不会重置角色密码。URL 不得包含密码；
先通过交互方式把认证信息配置到当前用户权限为 `0600` 的 `.pgpass`，避免 shell
history 和进程参数暴露凭据。

安装器会停止并等待旧服务退出，创建并校验 `instrument_registry / platform / portfolio / watchlist`
四个项目 schema 的迁移前 custom-format 备份，执行全部 Alembic 迁移，重建三个前端，然后以原子文件替换更新各 plist 并启动 `launchd`
服务。任一迁移、构建、plist 安装或健康检查失败时，会先卸载新服务、恢复数据库备份
和旧 plist，再恢复此前加载的服务；数据库或 plist 回滚失败时所有托管服务保持停止。
校验后的迁移前备份默认保留在
`~/Library/Application Support/portfolio-operations-workbench/backups/`。定时任务在
加载后会先执行一次，并继续保留每日计划；非阻塞文件锁拒绝与其他刷新重叠。

默认调度时间可以在安装时覆盖，例如：

```bash
PORTFOLIO_OPS_LOCAL_REFRESH_HOUR=22 \
PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE=30 \
PORTFOLIO_OPS_LOCAL_DATABASE_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  infra/launchd/install_local_services.sh
```

`StartCalendarInterval` 使用 macOS 当前系统时区。任务每天 `21:00` 正常运行；
若该批次没有完整成功，则 `23:00` 自动补跑一次。`21:00` 已成功时，`23:00`
只检查原子运行状态并退出，不会重复扫描或重算。电脑在计划时间处于睡眠状态时，
`launchd` 会在下次唤醒后补跑，并把睡眠期间错过的多个触发合并。
用户已注销或电脑关机时，用户级 LaunchAgent 没有加载；重新登录会由
`RunAtLoad` 按同一补跑状态判断是否需要执行，并继续等待后续计划。锁屏但未注销不影响调度。

定时任务依次按 Tushare、邮件通道增量刷新，再协调方法版本落后的私募基金投影；失败条目会先重试两次。成功
写入后，Portfolio 快照刷新会在请求内同步完成，FX 变化会刷新所有组合；Watchlist
请求负责可靠地持久化或复用重算 job，后台 worker 随后异步完成实际物化。
任务通过 `fcntl` 非阻塞锁避免同一个 scheduled 脚本从 launchd 或终端重叠运行，
进程退出或崩溃时内核会自动释放锁。单项刷新失败、下游请求失败、审计失败或任务异常都会
留下非零退出状态、原子写入的刷新摘要及 `var/market-data-refresh-run-state.json`
运行状态，`KeepAlive=false` 因而不会形成无限重启循环；`23:00` 最多补跑一次，
之后等待下一天计划。若 PostgreSQL 正在启动或短暂不可用，任务默认
等待最多 300 秒再退出；可用 `PORTFOLIO_OPS_LOCAL_REFRESH_DATABASE_WAIT_SECONDS`
和 `PORTFOLIO_OPS_LOCAL_REFRESH_DATABASE_RETRY_INTERVAL_SECONDS` 调整等待时间与间隔。

Platform API 与定时 runner 都固定使用 `platform, instrument_registry, public`
search-path 顺序。邮箱目录游标、附件解析和重试状态写入私有 `platform` schema，
canonical 单位净值/复权累计净值才写入 `instrument_registry`；安装或恢复的备份会同时
覆盖两者，避免只恢复行情结果却丢失 ingestion checkpoint 后重复全量扫描。

Platform API 和定时任务都会读取当前
`~/.config/orataba/secrets/portfolio-operations-workbench/platform.env`，不再读取仓库
内的 runtime `.env`。秘密目录必须由当前用户拥有且权限为 `0700`，文件必须由
当前用户拥有且权限为 `0600`。安全加载器只接受
`PORTFOLIO_OPS_PLATFORM_*` 赋值，把值作为纯文本导入，不会执行 `$()`、反引号
等 shell 语法。安装器不会把 DataHub API key、邮件密码等秘密复制进 plist；数据库
连接只写入确实需要它的三个 API 与定时刷新 plist，三个纯 web job 不携带数据库
凭据。为了避免 Pydantic 再从第二来源补入配置，安装器、三个 API runner 和定时
runner 都会拒绝任一 backend 目录中存在
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
`Umask=077` 将新日志限制为当前用户可读写。每次安装或更新在服务停止后会压缩超过
100 MiB 的 `.log` 文件，仅保留末尾 10 MiB；阈值可分别通过
`PORTFOLIO_OPS_LOCAL_LOG_MAX_BYTES` 与 `PORTFOLIO_OPS_LOCAL_LOG_RETAIN_BYTES`
覆盖。卸载只移除服务，不删除数据库、研究输出、上传文档或日志。

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

执行 `infra/postgres/restore_project_dump.sh` 时，恢复脚本会临时卸载当前已
加载的六个常驻 job 和定时刷新 job，完成安全备份和数据库恢复/迁移后再加载。
恢复定时 job 时会由 `RunAtLoad` 启动一次刷新并重新注册日历计划。恢复失败会先自动回滚
数据库，再恢复这些服务；回滚本身失败时服务保持停止，避免在半恢复数据库上
继续写入。incoming dump 必须具有通过校验的 SHA-256 文件，不能跳过；停服后若
仍有客户端连接无法终止，恢复会在备份或 schema 删除前硬失败。恢复脚本与安装器
共用同一个 project-schema backup/rollback 原语。
