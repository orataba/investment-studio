# Investment Studio Home

这是账号与导航主页，提供 Watchlist、Portfolio、Regime、Briefing 四个业务 App 的入口。云端使用真实账号登录；显式启用的本机模式免登录并具有全部业务权限。

- `frontend/`：登录、账号设置、团队成员管理和主页导航。
- `backend/home_api/`：账号、团队、可撤销会话与服务身份；入口地址和主页自身健康状态。
- 只拥有 PostgreSQL 的 `identity` schema，不读取业务账本、研究记录或持仓，不持有 FMP/DataHub/邮箱密钥。
- 私密配置来自仓库外 `home.env`，前缀为 `INVESTMENT_STUDIO_HOME_`。
- `apps.json` 是唯一导航清单。增删卡片不需要修改 React；云端地址通过 `INVESTMENT_STUDIO_HOME_APP_URLS` JSON 对象按 app_id 覆盖。

所有数据接入、解析、导入、人工修正和定时更新都属于独立的 [Data](../shared-data/README.md)，不经由主页执行。公共数值与文本由 [Market Data Pipeline](../docs/MARKET_DATA_PIPELINE.md) 管理；Regime 的模型代码与运行数据库由子模块独立维护。

本地服务与部署见 [Local Service](../docs/LOCAL_MACOS_SERVICE.md) 和 [Server Deployment](../docs/SERVER_DEPLOYMENT.md)。

## 账号初始化与切换

这是显式迁移，不在启动时创建账号，也不再读取旧的单账号配置作为登录兜底。身份数据库未配置时受保护请求不可用。首版只有 `default` 团队，研究署名使用永久用户 ID。

1. 在私密 Home 配置中设置 `INVESTMENT_STUDIO_HOME_DATABASE_URL`，指向该实例的 PostgreSQL。
2. 执行 `PYTHONPATH=home/backend .venv/bin/python -m home_api.cli migrate` 应用身份库版本迁移；已有账号原地保留。
3. 执行 `PYTHONPATH=home/backend .venv/bin/python -m home_api.cli bootstrap --username 原用户名 --display-name 显示名 --password-hash-file /private/旧密码哈希文件`。也可省略最后一个参数，交互设置至少 12 位的新密码。该操作只在身份库为空时允许。
4. 记录输出的 `user_id`、`team_id`，由业务迁移明确分配历史作者、旧私聊和组合管理者。Home 不猜测历史作者，也不会把全部组合授给新成员。
5. 配置各业务应用身份解析地址与专属后台凭证，完成组合、助手、附件的隔离验证后再开放真实成员访问。旧签名 cookie 不再有效。

本次确认的初始化归属为：拥有者账号及显示名均为 `shaw`，旧组合由他独占 manager 权限，已确认的旧观点与私聊显式认领到他的永久用户 ID。`leo`、`aaron`、`huangwei` 为团队成员，`yungu` 为团队只读成员；四人均不预授现有组合权限，之后由组合管理者手工授予。该说明是初始化要求，不代表运行库已执行。

## 本机免登录

先显式初始化真实团队拥有者，再在本机 `home.env` 设置 `INVESTMENT_STUDIO_HOME_ENVIRONMENT=local`、`INVESTMENT_STUDIO_HOME_AUTH_MODE=local`；各业务应用设置 `INVESTMENT_STUDIO_AUTH_MODE=local`。Home 和业务入口必须绑定 loopback，并使用 `127.0.0.1` 或 `localhost` 访问。仅设置模式而没有身份数据库或拥有者，不会自动创建账号。

无凭证的本机请求由 Home 解析为 `default` 团队当前拥有者，新操作仍保留真实署名，页面显示“本机全权限”并隐藏登录、退出流程。请求检查实际连接地址、Host 和 Origin；云端不能启用该模式，也不接受本机身份。显式传入的会话或运行凭证仍按该凭证解析，不会自动提升为本机身份。

本机 AI 仍使用真实短期、绑定资源的委托，保留本机身份的来源，但不能越过运行、组合或工具范围。它的父来源是当前真实拥有者，不创建永久登录会话；拥有者停用、交接或切回账号模式后，相关委托失效。后台定时服务继续使用独立服务凭证。

账号模式下，`/account` 支持显示名、改密码、退出全部设备、二步验证、团队成员邀请、停用/恢复、角色更改、密码重置和拥有者交接。团队成员目录可由成员和绑定的任务读取，不包含凭据。团队管理员不自动获得组合权限。拥有者交接不改变原作者和组合授权。

邀请和重置链接 24 小时内一次有效，凭证放 URL fragment，服务器不接收链接中的明文凭证；实际设置密码时才通过 HTTPS POST 提交。创建和重置链接在页面临时展示，人工交给对应成员。停用、退出和密码变更会撤销服务端会话，相关任务委托随之失效；恢复账号不会恢复已撤销的会话。连续 5 次失败会暂停该账号登录 15 分钟。

修改密码、通过链接设置密码、停用账号和运维恢复也会撤销尚未使用的旧邀请或重置链接；恢复账号不会使旧链接重新生效。并发凭证操作在同一用户锁内重新验证有效性。

## 二步验证与恢复

二步验证使用 PyOTP，数据库中的验证器密钥使用 `cryptography.Fernet` 加密。先执行 `... -m home_api.cli create-mfa-key --output /private/identity-mfa-key`，再设置 `INVESTMENT_STUDIO_HOME_AUTH_TOTP_KEY_FILE`。私钥文件权限必须是 `0600`，需要与身份库一起按私密备份规则保存。外网部署建议设置 `INVESTMENT_STUDIO_HOME_AUTH_REQUIRE_ADMIN_TOTP=true`：未完成设置的管理员只能进入 Home 账号设置，不能访问业务应用或管理成员。

验证码不接受重复使用；启用或移除二步验证会退出所有设备。密码重置不会移除二步验证。验证器丢失时由本机运维核实身份后执行 `... -m home_api.cli recover-user --user-id UUID --reset-mfa`；必要时另加 `--reset-password` 交互设置密码，恢复动作会留痕并撤销会话，不会自动恢复被停用的账号。

## 服务与运行凭证

`... -m home_api.cli create-service --service-id watchlist-backend --display-name 研究后台 --audience identity --audience watchlist --scope identity:delegate --scope watchlist:research --token-file /private/watchlist-identity-token` 创建有期限的服务凭证，默认 90 天，最长 365 天。凭证仅写新建 `0600` 文件，命令输出其 ID 和文件路径。按实际后台职责分别授权 audience / scope，模型不持有该服务凭证。使用 `revoke-service --credential-id UUID` 撤销，子任务凭证随之失效。

`POST /api/auth/introspect` 接收 Bearer 凭证与 `{audience}`，返回当前身份和范围，不返回 token。`POST /api/auth/delegations` 给浏览器会话或服务身份生成短期、绑定资源的运行凭证；任务不能自行再委托。后台跨服务使用独立 `X-Studio-Service-Token`（需 `identity` audience 与 `identity:delegate` scope）代原用户进行有范围的再委托。每次解析校验原始账号和会话、上级委托是否仍有效，业务应用继续按当前组合权限作最终判断。

云端保持 `AUTH_MODE=account` 和生产环境，cookie 为服务端可撤销的随机会话，使用 Secure / HttpOnly / SameSite=strict。开发 HTTP 若需要显式账号会话，须使用 `environment=local`、`auth_cookie_secure=false` 及不带 `__Secure-` 前缀的 cookie 名，并在各应用使用相同 cookie 配置。本机免登录本身不设置 cookie；浏览器写请求仍检查 Origin。`frontend_url`、`cors_origins` 与 `app_urls` 中的完整 origin 应仅列可信工作台地址。
