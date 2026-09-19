# Investment Studio — Portfolio

Portfolio 承载组合、账户、交易、账本、持仓、绩效、风险、taxonomy 和研究。源交易与账户是事实；ledger postings、lots、daily snapshots、contribution slices 和页面 payload 是可重建派生结果。

## 职责与边界

当前交易主路径支持：

- Registry 中的股票、ETF、公募和私募基金；股票/ETF 另支持明确记录的卖空、买回与期初空头；
- Portfolio-local FCN 与 Option 合约及其已确认生命周期事件；
- Cash、费用、税、利息、换汇和账户内转移；
- Preview/Commit、CSV/Excel 和截图助手共用的交易 command contract。

Portfolio 不直接写 Registry market facts，也不复用 Watchlist 的名单、taxonomy 或研究模型。交易录入的股票/ETF 搜索，以及新建 FCN、Option 合约的挂钩标的选择（含截图复核），同时读取全市场目录；用户选择未登记证券时，组合编辑权限保护的入口调用共享数据 CLI 按需建档及补齐行情，再返回规范资产记录。搜索不写数据，选择证券不创建合约或交易；删除挂钩行、修改搜索、切换草稿后，未完成的登记结果不会覆盖其他行。已确认合约的挂钩身份仍不可在条款修订中替换。基金卖空、直接债券、券商授信与购买力计算、PE/VC capital call、基金份额转换、空头证券及衍生品 transfer，以及 FCN/Option 的 daily fair value、Greeks、FCN 自动 barrier 判定和期权自动行权不在当前支持范围。实际融资借还和抵押释放可通过明确现金用途的账户与内部划转记录。

关键运行约束：

- FCN 和 Option 是 Portfolio-local immutable contracts；只有 underlying/deliverable 证券引用 Registry；
- Portfolio 的 reporting/base currency 在组合 Settings 管理；切换后交易事实币种不变，所有旧口径派生快照失效并全量重算；
- 组合编辑者可在组合列表或组合内设置中重命名；名称去除首尾空格后为 1 至 200 个字符，组合 ID、授权、交易与历史快照不变。仅修改名称不会触发金融重算，列表、选择器和缓存中的名称同步更新；
- 经人工确认的期权实物行权或指派由一个原子命令生成零现金期权关闭和按行权价成交的股票腿，并保留严格一对一关联；FCN 实物交付在同一兑付记录保存实际证券、确认价值、现金尾差及必要的汇率，不能用虚构现金兑付及独立买股替代；
- 已有期权空头使用 `opening_written` 导入期初账面负债，不重复记录历史权利金现金；FCN 敲入观察使用 `knock_in_observation`，不提前清仓；
- 账户出现资产交易后不允许切换成本法并重述历史。Option 的交割方式、行权风格以及 FCN 观察条款按已确认合约保存；未知条款明确保留为未确认；
- 交易的 trade、position-effective、entitlement、settlement 和 snapshot 时钟不能互相替代；
- 所有写路径先保存 canonical facts，再标记最早受影响日期并重建派生读模型；
- daily snapshot 重算的外部入口只写 durable calculation state 并返回 `202`；单线程 worker 合并 generation、保留最早 `dirty_from`，发布前复核 source generation；
- 依赖快照的金融 GET 不在请求内重算；未就绪时返回 `503 portfolio_calculation_pending` 与 `Retry-After`，前端合并相同请求并有界等待。交易事实明细不受此等待或估值截止日限制；显式 Research 计算及离线刷新仍可调用现有计算内核；
- Performance 区间计算读取每日核算发布的边界批次、估值、汇率及区间内事件，保留批次原始取得顺序和内部转仓来源；切换日期无需重载证券/汇率完整历史或重建账本。分组风险读取历史有效性摘要与所需窗口的报价日期，保留原报价优先级及缺口判定。新读模型与每日快照共用计算版本和来源失效机制，不依赖已访问过的日期缓存；
- Holdings 的分析结果和 summary 共用的风险频率按快照来源版本缓存；日期、风险政策、分析分类版本参与缓存键。计算中来源发生变化的结果不缓存，金融响应发布前再次核对来源；实时任务和衍生品观察在缓存外读取。展示图表裁剪不裁剪收益率、回撤或波动率计算历史；
- 组合 summary、Overview 和默认 Holdings 共享同一 fresh-complete snapshot 选择规则；来源日历确认的休市可沿用前一有效点，预期行情或必要 FX 缺失则在首个缺口停止，补齐并重算前不从后续日期重新起算；
- Taxonomy/TargetSet 是 planning truth，Research 消费它们，不建立第二套目标体系；
- Research 先成功提交结果，再清理请求时间更早且已结束的运行及其产物；仍在运行的任务和较新请求不被旧任务删除。发布失败会回滚本次修改并保留上一份有效结果；
- Portfolio taxonomy 与 Watchlist taxonomy 的节点、assignment 和版本完全独立，同名不代表关联；
- 条件不足的收益、风险和研究结果明确 unavailable，不用旧算法、等权或不完整样本兜底。Research 的当前性校验同时覆盖分类目标、历史交易行版本、账户及合约、本位币和风险设置、研究标的及 FX 的共享来源版本；计算过程中这些输入变更会终止本次发布并保留上一份有效结果。来源水位不提供按日期的局部修订信息，因此固定分析日的研究在相关来源更新后也需重算确认。

## 文档

| 主题 | 权威文档 |
| --- | --- |
| NAV、TWR、IRR、风险、归因、drift、scenario 和 Research solve | [Calculation Spec](./docs/01_CALCULATION_SPEC.md) |
| GIPS-informed 方法及声明边界 | [GIPS Alignment](./docs/02_GIPS_ALIGNMENT.md) |
| Holdings 每个字段和分组聚合 | [Holdings Field Reference](./docs/03_HOLDINGS_FIELD_REFERENCE.md) |
| 交易创建、修改、删除、CSV/Excel 与核对 | [Transaction Operations](./docs/04_TRANSACTION_OPERATIONS.md) |
| Portfolio 文档维护入口 | [Portfolio Documentation](./docs/README.md) |
| 外部 JSON Preview/Commit 对接 | [Transaction Import API Guide](../../docs/TRANSACTION_IMPORT_API_GUIDE.md) |
| 截图助手 Harness 和权限边界 | [Portfolio Copilot Harness](../../docs/PORTFOLIO_COPILOT_HARNESS.md) |
| 数据表与字段 | [Portfolio Database Dictionary](../../docs/PORTFOLIO_DATABASE_DICTIONARY.md) |
| 仓库架构、数据库和部署 | [Repository Documentation](../../docs/README.md) |

README 不维护公式或字段摘要；发生冲突时以上 canonical 合同和当前实现必须在同一改动中校正。

## 代码定位

```text
apps/portfolio/
  backend/portfolio_app/api/       HTTP routes 与 contracts
  backend/portfolio_app/services/  交易、账本、计算、风险和研究
  backend/portfolio_app/db/        portfolio schema models/session
  backend/alembic/                 portfolio migrations
  backend/scripts/                 受控导入、截图 Harness 和运维入口
  frontend/src/                    Portfolio 工作台
```

## 本地开发

先按根目录 README 安装依赖，并在仓库外准备 `portfolio.env`。从仓库根目录启动后端：

```bash
PROJECT_ROOT="$PWD"
RUNTIME_ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio"
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$PROJECT_ROOT"
investment_studio_load_env_file \
  "$(investment_studio_runtime_env_file portfolio "$RUNTIME_ENV_ROOT")" \
  INVESTMENT_STUDIO_PORTFOLIO_
: "${INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL:?portfolio.env must set the canonical database URL}"
PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/shared-data/instruments/python" \
  "$PROJECT_ROOT/.venv/bin/python" -m uvicorn portfolio_app.main:app \
  --host 127.0.0.1 --port 8001 --reload
```

启动前端：

```bash
npm --prefix apps/portfolio/frontend run dev -- --host 127.0.0.1 --port 5174
```

前端默认把 `/api` 代理到 `http://127.0.0.1:8001`。真实数据库迁移必须使用根目录统一入口；不要单独升级 Portfolio Alembic chain。重建空 schema 后不会自动生成 demo portfolio，需通过 UI/API 显式创建或使用受控导入脚本。

Performance 手动编辑起止日期后，点击“应用区间”或按 Enter 才提交完整区间；预设区间、最新日期和重置仍立即应用。可靠日期边界继续由后端返回的有效区间决定。前端仅对带 `portfolio_calculation_pending` 的 GET 按 `Retry-After` 重试，同一路径的在途请求共用等待，最多等待 120 秒后提示稍后刷新；计算失败、普通 `503` 和写请求不自动重试。

## 验证

Risk 提供单证券、单 FCN 和自定义分类的集中度观察及可选上限。FCN 按剩余本金分配，默认等分挂钩标的，支持自定义比例；Option 不进入该集中度分子。分类设置可管理权重/风险贡献目标和集中度规则，页面分组选择与默认生产分析分类分离。尾部风险提供当前持仓历史模拟 VaR/ES，并显示证券/FX 的实际建模覆盖；DSH 读取同一套集中度、目标与尾部风险数据。详见计算规格第 8、9、11.5 节。新增限额默认未配置，由组合编辑者显式设定。

```bash
infra/scripts/verify_repository.sh backend portfolio
infra/scripts/verify_repository.sh frontend portfolio
```

修改 Holdings 字段时运行文档合同测试；修改 migration、cross-schema FK、search path、事务或并发行为时按 [Database Workflow](../../docs/DATABASE_WORKFLOW.md) 运行 PostgreSQL 专项门禁。

## 统一账号与逐组合权限

Portfolio 后端通过 `packages/identity` 向 Home 解析真实身份，不接受 `X-User-ID` 建立权限。云端账号模式按会话和逐组合授权访问；显式本机模式由 Home 返回实际拥有者的 `local_unrestricted` 身份，本机免登录且可管理全部组合（含未分配旧组合），仍保留实际操作人和账本校验。该标记在云端无效，短期工具委托仍先受绑定资源和工具范围限制，不能借本机权限入账。配置 `INVESTMENT_STUDIO_AUTH_URL`、统一会话 cookie 名称及允许的浏览器 Origin。跨应用用户请求传递原凭证，组合访问由 Portfolio 当前授权决定。

每个组合的 `portfolio_access_state` 明确团队归属，`portfolio_membership` 采用 `manager / editor / viewer`。组合列表在读取净值摘要前先按授权筛选。管理者在组合的 Settings 内授予或移除角色，外层页面不单独展示权限入口；移除最后一位有效管理者前须先交接。团队管理员没有隐含业务访问权。排序与表格设置归个人；新创建及复制组合仅将创建者设为管理者。

迁移 `20260908_0061` 保留账本和原交易历史，旧组合不会自动授给任何新账号。首次授权由部署时明确指定的拥有者认领。拥有者恢复授权 API 保留：目录只包含 ID、名称和管理状态，不含净值或持仓，目录读取和授权均留审计；组合外层页面不展示恢复入口。

所有组合 API、工作区、文件下载、研究报告、截图及批量请求经过同一服务端授权。文件和受保护响应带 `Cache-Control: private, no-store`。研究助手保持人工入账边界：截图 MCP 使用 Home 生成的单次任务委托，只能读取绑定批次材料和本组合事实、执行 Preview、提交复核稿，不能 Commit，也不能凭用户填入的作者名或 batch ID 建立身份；撤权后下一次工具调用即失效。

页面研究助手传递当前 `holding_id / as_of_date / account_id`，其中持仓引用可为本地 FCN、Option 或现金头寸，不能当作共享证券 ID。历史风险上下文通过 `risk-context?as_of_date=...` 读取当日持仓；账户或单持仓作为焦点附在全组合上下文上，不重定义组合净值分母。保存的当前目标和重算模型仍按其非 PIT 边界披露。

组合只读成员可以读取风险并发起组合分析；共享标的风险事项的写入另按团队研究权限控制。`session.can_write_team_research` 决定共享跟进/规则的编辑入口，不能由组合编辑权限代替；团队只读账号不显示共享研究写控件。

估值后台线程使用独立服务身份，须具备 `portfolio:maintain`，每轮执行前向 Home 重新解析。首版是单团队部署，`portfolio:maintain` 明确代表部署实例级估值维护，队列及全量重算面向实例全部组合；无 `resource_scope` 的维护服务或本机全权限主体可调用全量重算，普通账号会话和绑定组合的委托不能全量重算。该服务的 HTTP 权限不允许修改交易、组合成员或共享业务配置。维护服务凭证通过 `INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE`（仅本人可读）或部署环境注入。浏览器、截图模型进程不能取得该主凭证。

`GET /api/portfolios/{id}/access` 返回当前用户的 `user_id / team_id / role / can_read / can_edit / can_manage`。未登录返回 401，无组合访问返回 404，权限不足的写操作返回 403。研究中的跨服务调用也必须使用相同用户或绑定的委托，内部地址不构成授权。

后台组合风险研究可被明确授予服务级 `portfolio:read`：允许读取本团队全部已归属组合，但不允许重算维护、修改交易或授权。此全团队只读权限仅适用于显式配置的服务身份；普通用户始终按逐组合授权，带组合范围的服务委托仍只读该组合。
