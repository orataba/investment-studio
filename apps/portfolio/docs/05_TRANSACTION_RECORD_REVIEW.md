# Transaction Record 全面复核与收口

最后更新：2026-08-22
状态：实现、生产迁移、部署与在线验收均已收口。

## 1. 结论

交易系统以四个直接业务入口表达事实：`Security`、`FCN`、`Option`、
`Cash & Operations`。页面录入、JSON API、CSV 与 Excel 共用同一个后端 command contract，
最终都写入同一 transaction ledger；Holdings、Accounts、Performance、Risk 和 Inspector
只从这些 canonical facts 派生，不维护第二套交易真相。

本轮已消除以下会阻断生产发布的问题：

- Portfolio 有显式、不可空的 `inception_date`，任何交易不得早于成立日，
  `opening_balance` 的 trade/settlement date 必须同时等于成立日；
- 资产 opening position 可用零成本，现金 opening balance 和普通运行期交易仍必须为正金额；
- Cash Fee/Tax 不再要求虚构资产 entitlement；Option writer 可以录入合约关联费用；
- Option 独立 Fee/Tax 是合约级现金费用，不改写 long lot 或 writer obligation；
- FCN 事件严格落在 issue/maturity 合约窗口内；
- FCN/Option Inspector 同时返回 long lots 或 writer obligations 的正确上下文；
- Transfer 仍以原子双腿记账，并拥有可导出、可回导、可跨批次查重的 command-level
  `source_system + external_reference`；
- CSV 与 Excel 使用同一 parser、preview、validation 和 atomic import 路径；
- 负 settled cash 在 Holdings 暴露为 critical exception，不被静默解释为融资；
- 数据审计的 Portfolio migration head 与当前 schema head 一致，并检查成立日交易边界。

## 2. 当前支持范围

### 2.1 Security

Security 是 Registry 中可复用市场资产的 long-only 交易模型，覆盖：

- 股票、ETF、公募基金、净值型 pooled 私募基金；
- Buy / Sell（基金页面文案为 Subscription / Redemption）；
- Dividend、Dividend Reinvestment、Return of Capital；
- 资产关联 Fee / Tax；
- inception opening position；
- 同 Portfolio、同币种、同资产类别账户之间的 position transfer。

卖出和转出都校验有效时点的可用数量。普通证券 lot 按账户的 FIFO 或 moving-average
成本法重放；转仓不形成损益，并把来源成本切片带到目标账户。

Return of Capital 使用 `trade_date` 表示 entitlement/record date，使用
`settlement_date` 表示现金到账日。这样成本调减在 entitlement 时点发生，现金则按结算日入账，
不再引入第二个含义重复的 entitlement 字段。

### 2.2 FCN

FCN 是 Portfolio-local immutable derivative contract，覆盖：

- 新合约 Entry、inception Opening Balance、Early Exit、Coupon；
- Maturity、Knock-in Close、Knock-out Close；
- 合约关联 Fee / Tax；
- 多标的条款、notional、coupon、issue/final observation/maturity、issuer/counterparty。

FCN 不注册成共享 Registry security。若发生实物交付，FCN close 与收到证券的普通 Security
Buy 是两个独立事实；系统不伪造技术配对。FCN 在事件间按 remaining transaction basis
carried，不宣称日常公允价值或市场风险覆盖。

### 2.3 Option

Option 同样是 Portfolio-local immutable contract，覆盖 Call/Put、long/short：

- Buy to Open、Sell to Close；
- Sell to Open、Buy to Close；
- long/writer Expiry（零现金）；
- long/writer Cash Settlement（正的结算金额，方向由持仓侧决定）；
- inception long Opening Balance；
- 合约关联 Fee / Tax。

Long option 使用 position lots；written option 使用独立 obligation subledger，不伪造成负的
long lot。writer premium 建立 premium-basis liability，平仓或结算时释放并确认结果。独立
Option Fee/Tax 只作为合约级现金和 Performance expense 记录一次，不改变 long lot 成本，
也不追溯修改 writer obligation。

实物行权或指派统一规范化为 Option cash settlement 加独立 Security trade。当前合约不预设
settlement mode，也不自动检查 covered writer。

### 2.4 Cash & Operations

覆盖 Deposit、Withdrawal、Interest、FX Conversion、Cash Fee、Cash Tax、Cash Opening
Balance 和 same-currency Internal Transfer。FX conversion 原子生成源币现金流出和目标币现金
流入，要求 `counter_amount = gross_amount × fx_rate`。

Cash Fee/Tax 不关联 instrument、derivative contract 或 entitlement date。系统允许交易事实
导致负 settled cash，以免伪造不存在的资金；但 Holdings 必须把该状态显示为 critical
operational exception。融资只有在系统拥有明确融资事实模型时才能成立，当前不能由负现金推断。

## 3. 日期、金额与身份契约

- Portfolio 创建时必须提供 base currency 与非未来 inception date；
- 所有 transaction trade date 必须大于或等于 inception date；
- opening balance 的 trade date 和 settlement date 必须等于 inception date；
- opening asset 可保留早于成立日的 acquisition date；
- 除 long/writer option expiry 和 asset opening balance 外，gross amount 必须为正；
- income/expense entitlement 未显式填写时以 trade date 校验当日资格；
- 每个外部来源事实建议携带 `source_system + external_reference`，该组合在 Portfolio 内唯一；
- Transfer 的来源身份只持久化在 `transfer_out` 腿，`transfer_in` 不复制该唯一键；
- Idempotency-Key 保护同一次 request replay，source identity 保护跨 request/batch 的同源重复。

## 4. 文件 Import / Export 契约

CSV 和 Excel 都使用同一组 canonical columns。Excel 只是带说明、字段指南、示例与下拉的手工
录入外壳，不拥有另一套业务规则。

- Export All 始终导出当前 Portfolio 全部交易，不受页面过滤器影响；
- persisted transfer pair 导出时折叠成一条 command，导入时重新原子生成双腿；
- 首笔 FCN/Option 行可携带 immutable contract definition，后续行只引用 contract ID；
- preview 会在任何写入前完成账户、币种、Registry identity、contract、日期、金额、
  source identity 和整段 position history 校验；
- import 必须使用未变化的 preview digest，并整批成功或整批失败；
- CSV/Excel 不导出 transaction ID、row version、change log 或内部 transfer group ID。

因此“页面录入”和“文件录入”的差异只在交互形式，不在会计或验证语义。

## 5. 与其他组合模块的配合

- Accounts / Ledger：settled cash、pending settlement、position posting、writer liability
  都由 transaction facts 重放；
- Holdings：普通证券使用 market/fund NAV 估值；FCN/long option 使用 carried cost；writer
  option 使用负的 premium liability；
- Performance：所有真实现金、费用、税、coupon、realized result 都只确认一次；external flow
  使用 settlement clock，funded performance start 与法律/导入 inception 分开；
- Risk：只让正式 policy 标为 eligible 且有完整 total-return coverage 的普通市场资产进入
  covariance；衍生品 carrying/liability 和 base cash 作为 0-return capital 留在总 NAV 分母，
  衍生品行明确显示 excluded，而不是伪报 volatility；
- Snapshot：任何交易 create/update/delete 都按最早受影响日期标记 derived snapshots stale；
- Inspector / History：展示 canonical record、postings、lots/obligations、row version 和不可变
  change log。

## 6. 明确不支持的边界

下列内容不是隐藏的“半支持”状态；页面、API 与文件模板都不应把它们包装成现有能力：

- 普通 Security short selling、借券、margin financing；
- 直接债券交易；
- PE/VC/有限合伙的 capital call、commitment、distribution 与估值模型；
- 基金份额转换、share-class conversion；
- FCN 或 Option 合约的直接 transfer；
- multi-leg derivative strategy/group relation；
- 自动 barrier monitoring、自动行权/指派、自动 covered-position test；
- FCN/Option 的 daily fair-value、Greeks 或 covariance risk。

不得把这些对象塞入 `other`、普通 Security、note 或兼容字段来绕过模型。如果业务需要，应先
定义独立、可审计的事实与下游口径，再新增能力。

## 7. 生产发布验收

最终发布证据（2026-08-20）：

- Portfolio backend 621 项测试通过，其中 PostgreSQL 集成测试使用真实临时数据库运行；
- Portfolio frontend 58 个测试文件、257 项测试及 production build 通过；infra 17 项测试通过；
- 生产四个业务 schema 的隔离副本先从 0053 完整升级到 0054，full audit 为
  42 checks、0 failed、0 warnings，演练临时数据库随后删除；
- 正式生产数据库已升级到 `20260820_0054`。最新一次受控安装前备份为
  `portfolio-ops-pre-launchd-install-20260820T083133Z-30203.pgdump`；
- 生产发布刷新 23 个当前相关市场标的，0 失败；刷新后 5 个 Portfolio snapshot 重建，
  1 个确认已是 current；live audit 再次为 42 checks、0 failed、0 warnings；
- 生产页面已验收 Portfolio 创建表单、四个交易入口、Import 文件选择、Export、Transactions、
  Holdings、Performance 与 Risk。验收未写入测试交易；
- Holdings 显示 `No operational exceptions`。Risk 正常返回模型结果，并明确披露
  1 个标的 observation gap，而不是把 90.19% coverage 伪报成完整；
- Performance 区间计算不再按请求重复回放每日账本：此前 60 秒超时的同一 31 日接口现在
  实测 HTTP 200、2.58 秒。页面从打开到 11 个 Taxonomy groups、19 个 instrument/cash rows
  和 Portfolio Total 完整可用实测 8.41 秒；访问日志确认只发出一次最终 Taxonomy 分组请求；
- 生产实现提交为 `cd8e094`、`ea51064`、`aaf44d5`，均已推送至远端 `main`。

## 8. 2026-08-22 多市场收口验收

本轮把中国内地、香港、美国和 FMP 当前实际支持的欧洲上市证券统一到同一套
Registry、Watchlist、Portfolio 与市场数据契约：

- 上市股票和 ETF 必须持久化交易所身份；Registry 迁移补齐 114 只存量 ETF 的
  `exchange_code`，并修正深市 ETF 的交易日历归属；
- FMP 欧洲范围限定为伦敦、Xetra、巴黎、阿姆斯特丹、米兰和瑞士六个已验证市场，
  资产首次物化时以 profile 校验交易所、证券类型和报价币种；伦敦 `GBp/GBX` 行情统一换算为
  GBP，不能把上市地点当作报价币种；
- 系统币种扩展为 USD、HKD、CNY、EUR、GBP、CHF；新增 USD/EUR、USD/GBP、USD/CHF
  正式汇率序列，并继续从 USD 维护对生成直接、倒数或交叉汇率；
- Watchlist 增加欧洲股票分类，ETF 搜索结果携带交易所；Portfolio 的交易、投资范围和持仓
  快照中的历史 instrument reference 由迁移一次性同步，不保留运行期兼容分支；
- 非上市欧洲基金不宣称 FMP 自动覆盖，继续走公开/私募基金的稳定身份与 NAV 导入流程。

正式生产数据库 head 已升级为 Platform `20260822_0006`、Portfolio
`20260822_0055`、Registry `20260822_0026`、Watchlist `20260822_0043`。发布刷新 26 个当前
FMP 标的，0 失败；重建 6 个 Portfolio snapshots；最终 live audit 为 43 checks、0 failed、
0 warnings。生产 FMP catalog 共 26,629 条 active instruments，其中股票 17,048、ETF
9,581。114 只存量 ETF 的交易所缺失和沪深日历错配均归零，Portfolio persisted reference
错配归零。

三个新增汇率序列已覆盖到 2026-08-21：USD/CHF 14,447 个观测、USD/EUR 12,137 个观测、
USD/GBP 12,093 个观测。后端普通套件为 Platform 334、Watchlist 154、Portfolio 629 项通过；
其中跳过的 PostgreSQL 专项分别 5、4、3 项，均已用真实临时库单独通过。三个前端分别 50、
43、261 项通过，production build、TypeScript 类型检查和 infra 17 项测试均通过。最新受控
安装前备份为 `portfolio-ops-pre-launchd-install-20260822T103707Z-67141.pgdump`，六个本地服务
最终健康检查全部通过。
