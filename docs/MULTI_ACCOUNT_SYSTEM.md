# 多账号、团队研究与组合权限

系统统一使用 Home 账号服务。当前部署只有一个默认团队 `default`；没有组织树或多租户管理界面。

云端采用真实账号和逐组合权限；显式启用的本机模式免登录、具有全部业务权限。下表描述云端账号边界；本机模式仍保留人员署名、历史归属和 AI 任务范围。

## 已实现的边界

| 范围 | 读取 | 修改与归属 |
| --- | --- | --- |
| Home | 本人账号；团队成员目录 | 管理员邀请、停用、重置；拥有者交接；本人密码和二步验证 |
| Watchlist 正式研究 | 团队共享 | 成员维护主题；观点保留真实作者，其他人可另记自己的观点与复盘 |
| 原始研究对话与附件 | 对话本人；涉及组合时还须有该组合权限 | 本人对话；明确保存为正式研究后才进入团队积累 |
| Portfolio | 有逐组合授权的用户 | manager 管成员与业务；editor 改业务；viewer 只读、导出、个人展示设置 |
| Regime | 已发布状态和历史可匿名读 | 研究审计页面和接口须登录；没有新增在线修改模型入口 |
| Briefing | 已完成的正式发布版可匿名读 | 预览、草稿、输入、留存原文须登录；正式生成须管理员或发布服务 |

组合没有授权时，不出现在普通列表、资产合计和研究助手候选中。团队管理员不自动获得组合权限。成员与权限管理统一放在各组合的“设置”中，组合列表及顶部不显示独立权限入口。后端保留团队拥有者恢复管理权限的能力，要求填写原因并留下审计记录。

个人显示列、排序等设置不修改其他成员的视图。业务计算和交易确认仍由原来的 Portfolio 流程负责。

## 身份与 AI 任务

- 会话是数据库保存的随机凭证；浏览器只持有 HttpOnly cookie。登出、全部登出、改密码、重置或停用后，后续请求重新校验当前身份。管理员操作与成员权限变更按团队串行；等待中的操作在执行前重新校验会话和角色，已降权或停用的管理员不能继续邀请或重置成员。
- 业务接口自行验证身份，不接受浏览器提供的 `X-User-ID` 等身份声明。云端账号模式下，即使直连 loopback 接口也须持有效凭证。本机免登录只在显式 local 模式、实际连接地址及 Host/Origin 都属于 loopback 时生效。
- 浏览器写入请求检查 Origin；后台 Bearer 请求独立验证身份和范围。
- AI 只收到短期运行凭证，并绑定具体研究 run、组合截图批次或简报。它不能把凭证自行扩展成另一个任务，也拿不到用户主会话或服务主凭证。
- 需要跨应用读取时，后端使用独立服务身份代为委托；主体仍是原来的用户，目标应用仍检查当前资源权限。撤销父级凭证会使下级任务失效。
- 对话属于组合时，附件、历史输入、工具读取和最终结果继续继承组合权限。组合分析不能直接把持仓信息写入公开的标的研究。
- 后台研究、数据维护和发布使用服务账号。没有服务身份时明确失败，不伪装成投资经理，也不匿名重试。
- 本机无凭证请求使用已经初始化的 `default` 团队拥有者，返回 `local_unrestricted=true`，不自动建账号或永久会话。显式传入的其他账号、服务或任务凭证不会获得本机权限。本机来源的短期任务先检查资源及工具范围，再在该范围内使用本机权限；云端拒绝本机来源的凭证。

## 启用顺序

以下操作分别在本机和云端执行，数据库、账号凭证与私有业务数据保持各自独立。代码完成不代表运行库已迁移。

1. 备份项目数据库及外部配置，停止旧服务的写入。备份/恢复脚本已包含 `identity` schema；TOTP 加密密钥是外部文件，须单独保留。
2. 在外部 `home.env` 配置 `INVESTMENT_STUDIO_HOME_DATABASE_URL`。它必须与同部署其他模块指向同一个 PostgreSQL 数据库。同步依赖后执行现有 `infra/scripts/migrate_all.sh`；它创建身份库和业务权限表，不创建用户、不替任何组合授权。
3. 按 [Home 账号初始化说明](../home/README.md) 创建首位真实拥有者。使用已经确认的真实账号及显示名；显式导入运维按用户授权生成的私有 scrypt 密码散列，或交互设置新密码。记录命令返回的真实 `user_id`，凭据不记录在文档中。
4. 核对旧研究作者归属后，先运行下方预览。只有明确属于这位经理的历史临时身份才映射到其账号；未知作者继续保留未知。旧私聊的认领另用独立选项，不自动合并到所有团队成员。
5. 显式将已确认的旧观点、私聊归属认领到相应账号，并将旧组合 manager 权限授予经过确认的管理者。配好服务凭证和下述各应用的身份地址；本机另开启 local 模式。新建组合自动由创建者管理。
6. 按已确认的角色建立团队成员与只读成员。新成员默认不预授现有组合权限，之后由组合管理者手工授予。邀请链接仅创建并呈现给管理员，没有自动发邮件或发消息。
7. 使用第二个测试成员验证只读/不可见组合、私聊和旧链接，以及停用后的再次读取。完成后再切换公开站点配置。

旧研究归属预览与应用：

```sh
.venv/bin/python infra/scripts/claim_research_identity.py --user-id REAL_USER_ID
.venv/bin/python infra/scripts/claim_research_identity.py --user-id REAL_USER_ID --apply
```

若原来全部未认领私聊确实属于这位经理，在预览与应用时分别增加 `--claim-unassigned-conversations`。默认来源身份为 `local-investor`，可通过 `--from-user-id` 指定经过确认的其他临时身份。这个命令不会推测未知作者，也不改变原始文本和当时的研究结论。

## 外部运行配置

在各应用自己的外部 env 文件中配置下列变量，不能把运行凭证写进仓库、前端配置或 AI 提示词。

```dotenv
INVESTMENT_STUDIO_AUTH_URL=http://127.0.0.1:8002/api/auth
INVESTMENT_STUDIO_AUTH_MODE=local
INVESTMENT_STUDIO_AUTH_COOKIE_NAME=studio_session
INVESTMENT_STUDIO_AUTH_ALLOWED_ORIGINS=["http://127.0.0.1:5172","http://127.0.0.1:5173","http://127.0.0.1:5174","http://127.0.0.1:5175"]
INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE=/absolute/private/path/APP-service-token
```

上面是本地 HTTP 示例。Home 必须对应设置：

```dotenv
INVESTMENT_STUDIO_HOME_ENVIRONMENT=local
INVESTMENT_STUDIO_HOME_AUTH_MODE=local
INVESTMENT_STUDIO_HOME_AUTH_COOKIE_NAME=studio_session
INVESTMENT_STUDIO_HOME_AUTH_COOKIE_SECURE=false
```

以上 local 模式要求入口只绑定 loopback，身份库已存在真实拥有者。界面显示“本机全权限”，无须登录或退出；无凭证请求不设置浏览器会话。本机研究与组合可以完整访问，但正式观点的历史署名不因此改写，组合资料仍不能直接自动发布成团队研究。

云端明确使用 `INVESTMENT_STUDIO_AUTH_MODE=account`、Home 的 `INVESTMENT_STUDIO_HOME_AUTH_MODE=account` 与生产环境。使用实际 Home API 端口（现有 systemd 示例为 `8102`）、HTTPS 页面 Origin、`__Secure-yungu_session`、Secure cookie，以及 `.yunguyungu.com` cookie domain。同一部署各模块必须使用相同 cookie 名称；需要测试本地账号会话时，`127.0.0.1` 与 `localhost` 不能混用来共享 cookie。

服务凭证通过 Home CLI 的 `create-service` 写入新的 0600 文件，不能手写一个任意 token。建议按当前职责分别创建：

| 服务用途 / env 文件 | audiences | scopes |
| --- | --- | --- |
| Watchlist 后台研究 / `watchlist.env` | watchlist、portfolio、identity | watchlist:research、portfolio:read、identity:delegate |
| Portfolio 快照维护与后端委托 / `portfolio.env` | portfolio、identity | portfolio:maintain、identity:delegate |
| 市场数据刷新 / `data.env` | portfolio、watchlist | portfolio:maintain、watchlist:maintenance |
| Briefing 发布 / `briefing.env` | briefing | briefing:publish |

`portfolio:read` 是对后台服务的明确授权：可读取本团队全部组合来安排自动风险研究，不能改账本、成员或共享设置，也不包含维护权限。普通用户不使用这个全量服务授权；AI子任务仍会按具体组合收窄。

`portfolio:maintain` 是部署实例的后台估值维护授权，只允许没有绑定单个资源的服务凭证调用全量或按标的重算；用户会话和 AI 子任务不能借此扩大操作范围。它不提供交易、成员管理或账本修改权限。

`identity:delegate` 仅供应用后端在受控调用中使用；模型进程的环境白名单不会包含这个服务主凭证。凭证有到期时间，使用 Home CLI 轮换或撤销。

Regime 使用独立运行环境时，将 `packages/identity` 安装进其 Python 环境，或在其外部 runtime 配置的 `PYTHONPATH` 中加入该绝对目录，并配置 `INVESTMENT_STUDIO_AUTH_URL` 与 cookie 名。缺少账号服务时，公开信号仍可读，审计请求返回不可用，不绕过校验。

## 迁移与运行注意事项

- 迁移默认不授旧组合、不认领旧私聊；历史归属须经确认后通过初始化步骤应用到相应真实账号。新成员仍无现有组合权限，不从本机全权限推导云端授权。
- 新研究迁移保留观点版本、时间、原作者信息和既有输入。正式研究不会按用户复制成多套孤立资料。
- Watchlist 的新身份迁移不提供静默降级。确需回退时使用停写后的完整备份，并一并处理会话和外部 TOTP 密钥。
- 已交付给某人的内容无法撤回；撤权控制的是此后的读取、工具调用和结果发布。
- 首次启用必须完成上述账号和历史数据归属步骤；实际执行与运行验证状态另行记录，配置说明不代表已完成启用。
