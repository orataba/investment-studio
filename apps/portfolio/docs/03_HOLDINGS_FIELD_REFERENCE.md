# Holdings 字段计算与分组标准

> 文档状态：当前实现合同。本文逐字段定义 Portfolio Holdings 的展示、计算、分组和不可用规则。

> 权威边界：[`01_CALCULATION_SPEC.md`](./01_CALCULATION_SPEC.md) 定义 Portfolio 的领域计算合同；本文是该合同在 Holdings 表格字段上的完整映射。README 和用户手册只做摘要，发生冲突时不得用摘要覆盖这两份规格。

## 1. Holdings 回答什么问题

Holdings 是指定 `as_of_date` 的**当前资产负债快照**。一条普通非现金 `position` row 会合并该资产在当前组合各证券账户里的开放头寸；written option 使用独立 `option_obligation` row；现金按 settled cash ledger 生成账户与币种粒度的行。同币种的不同现金账户不能合并，因为各账户的历史汇率成本可能不同。

它主要回答两类问题：

- 当前还持有什么或承担什么义务：数量、市值或 carrying amount、权重、剩余账面成本、经济回本价、价格与汇兑未实现盈亏、short-option contract quantity 与 premium liability；
- 以当前这些资产和当前权重回看，标的自身及当前持仓篮子的历史市场表现和风险如何。

持仓列表不回答真实历史组合在某个区间内赚了多少。组合 TWR、期间贡献、已实现资本利得、Income、费用、税费和已经卖出的仓位由 Overview / Performance 核算。标的详情页的「区间盈亏」复用 Performance 的标的分组核算，不从当前持仓表倒推收益。Holdings 的 group 和 subtotal 也不是 Performance group。

Portfolio 和 Watchlist 各自实现自己的读路径，不导入对方服务或通过运行时调用复用计算。两边的 taxonomy 也完全独立：节点 ID、层级、assignment、版本和生命周期都没有映射或继承关系，同名节点不建立任何关联。对于同一 instrument、同一请求 `as_of_date`、同一已确认 total-return basis 和同一窗口边界，Holdings row 的 `1M Total Return` 等标的指标应与 Watchlist 对应字段数值一致；这只是共享市场事实下的数值一致性合同，不是 taxonomy 或应用耦合。

## 2. 公共计算约定

### 2.1 日期、行情与币种

- 未显式指定日期时，Holdings 使用 latest fresh complete snapshot；不能把部分资产已经更新的更晚日期当作组合 `as_of_date`。
- 行级 `Position Value (Local)` 与带 `(Local)` 的成本、盈亏字段使用标的本币；带 `(Base)` 的字段及所有可加总金额使用组合 base currency。对 event-valued asset 和 option obligation，`market_value(_base)` 只是 signed operational NAV amount，不代表 fair value。
- 普通非现金市值为 `quantity × selected valuation quote × price_scale`；再用 `as_of_date` FX 转为 base currency。FCN/长期权在没有可靠 fair value 时使用 `valuation_basis=carried_cost`，written option 使用 `valuation_basis=premium_liability` 和负的 NAV amount；两者的 `fair_value` 及 quote identity 均为空。现金市值为 settled cash amount。
- 行级 `Weight = market_value_base / portfolio NAV`，其中 NAV 包含 pending settlement，option obligation 因而使用负权重。行级 Return / Risk 只允许 `risk_eligible=true` 的 market-valued positions 参与；pending settlement、event-valued asset 和 written liability 都不能被伪造成有自身收益序列的持仓。组合聚合风险另以 total NAV 为分母，将 base-currency monetary rows 与衍生品资本按 0 return 处理。
- 缺少唯一且合法的 valuation quote、价格单位/scale 或必要 FX 时，依赖它的字段为不可用，不用图表点或 0 补齐。来源日历确认的休市可以沿用前一有效点并保留 source date；应更新的行情或 FX 缺失时，日度估值链在首个缺口停止。默认 Holdings 使用此前最后完整快照并披露实际日期，缺口补齐并重算前不能从后续报价重新建立连续估值链。
- 唯一的普通持仓初始估值例外是首次买入份额确认日：无正式价格时，按当日买入总 gross / 总 quantity 对所有账户和 lots 一致估值。`valuation_basis=transaction_price`、`quote_status=transaction-price` 与来源交易 IDs 明确披露，正式 quote identity 和日涨跌留空；费用仍进入损益。后续只有已确认休市可沿用，已有持仓加仓和下一交易日缺价不适用此例外；非现金交付与期权实物交付关联的股票腿也不适用。

### 2.2 成本、盈亏与收益分类

- `Book Cost (Local)` 是当前开放头寸的 remaining accounting cost。FIFO 为开放 lots 剩余成本之和；moving average 为滚动平均成本 bucket 的剩余成本。普通分红不修改 book cost；明确的资本返还会冲减 book cost。
- FIFO 默认按原始 `acquisition_date` 及开仓事实时间/sequence 消耗批次；转入、历史开仓导入和拆股保留原取得顺序，不以接收或导入时间排到新批次之后。轻量头寸与完整 lot 读模型使用同一规则。
- `Book Cost (Base, Current FX)` 用 `as_of_date` 汇率换算本币 book cost；`Book Cost (Base, Trade FX)` 则把每个仍在持有的成本来源按其 acquisition date 汇率换算后求和。moving-average 合并或部分卖出不能抹掉这些成本来源。
- `Book Avg Cost = remaining cost basis / remaining quantity`。它不是某笔成交价，也不把显示舍入后的价格反写到账本。
- `Net Invested (Local)` 是当前连续持仓周期的净投入：买入成本和费用为正，已卖部分的净回款、已实现分红/票息和资本返还为负。`Break-even Price = Net Invested / current quantity`，因此它可以低于 book average cost，甚至在累计回款超过投入后为负。完全平仓后再次买入会开始新的持仓周期。
- 普通证券的未实现盈亏拆为：`Price P&L (Local) = market value local - book cost local`；`Price P&L (Base) = Price P&L (Local) × current FX`；`FX P&L (Base) = Book Cost @ Current FX - Book Cost @ Trade FX`；`Total Unrealized P&L (Base) = Price P&L (Base) + FX P&L (Base) = market value base - Book Cost @ Trade FX`。
- `Price Return (Local) = Price P&L (Local) / Book Cost (Local)`；`Total Unrealized Return (Base) = Total Unrealized P&L (Base) / Book Cost (Base, Trade FX)`。这两个百分比都是当前开放成本口径，不是 TWR。
- Event-valued position 的 carrying basis 不等于 fair value；option obligation 也没有 long-position Book Cost / Book Avg Cost / fair-value Unrealized P&L。相关证券盈亏字段留空，但外币 carrying amount 另行展示 `Historical Carrying Basis (Base)` 与 `Carrying FX Translation (Base)`；这是账面 carrying value 的汇率折算，不得命名为或冒充 fair-value P&L。
- Securities group/subtotal 的 `Total Unrealized Return (Base)` 使用组内 base-currency 总未实现盈亏除以历史汇率成本重算。它不包含 Cash & Settlement，也不冒充组合级 TWR。
- 普通 `dividend / coupon` 在 entitlement date 进入已实现 `Income`，不冲减 book cost，也不进入未实现盈亏；但会降低当前持仓周期的 `Net Invested` 与 `Break-even Price`。
- `dividend_reinvestment` 在权益日确认已实现 `Income`，新份额尚未生效时以 reinvested gross amount 在原证券账户形成应收；份额生效日才转为有成本的新 lot。新 lot 的取得日为份额生效日，历史基准币成本使用权益日收入确认 FX；不得提前显示份额、建立零成本份额或重复记现金。
- 只有明确的 `return_of_capital` 才冲减开放成本；按剩余股数逐批分摊，每批成本最低为零，超过该批剩余成本的部分确认已实现收益，不制造负的多头成本。
- FIFO 处置可用 `lot_selections` 指定开仓或接收批次，未指定按 FIFO；移动平均账户不选择单批成本。有持仓历史后不能直接更改账户成本法。实物行权的批次选择针对期权多头，股票交付成本另按证券账户计算。
- 股票/ETF 空头的 remaining quantity、book cost 及市场价值带负号进入净资产；`short_sell` 按实际事实开空，`buy_to_cover` 释放空头净账面负债并确认买回损益。期初空头保留原开仓日和剩余净账面负债，不重复增加现金；普通基金不支持卖空。
- FCN 实物交付以确认价值建立或覆盖接收证券 lot，可追溯兑付源记录；不伪造现金买入。现金账户的 collateral 用途不计为自由交易现金，margin/financing 负余额作为融资负债；可记录实际融资与抵押，不代表券商购买力计算。

### 2.3 标的收益、风险与窗口

- `1W / 1M / 3M / 6M / MTD / YTD / 1Y Total Return`、drawdown 和 volatility 只使用 Registry 已明确确认的单一 total-return series。基金使用 `total_return_nav`；其他 basis 只有在 Registry 明确声明 total-return semantics 时才可用。这些字段描述 instrument 自身的本币 total return，包含分红等复权，但不混入组合基准币种汇率变动。
- 滚动窗口从**请求的** `as_of_date` 回看；终点是 `as_of_date` 或之前最新点，锚点仍按请求日期计算，不能因为终点 stale 就整体向前平移窗口。1M/3M/6M/1Y 按自然月/年回看，1W 按 7 个日历日回看。
- Volatility 和用于分组 Drawdown 的 return path 还必须通过日频尾部新鲜度检查，最多落后 `5` 个自然日。整组成员即使共同停在同一个旧日期，也不能因为“彼此对齐”就发布已经陈旧的风险值。
- MTD / YTD 使用严格早于月初 / 年初的最近有效点作为锚点；期间内第一点不能冒充完整自然期间。
- `Chart *` 是有界采样的展示路径，可以是明确标注的 price-return 或 total-return path；它不是 Return、Volatility 或 Drawdown 的替代输入。
- 缺少完整覆盖、唯一 series identity、合法 return semantics、必要锚点、共同 return currency 或最小样本时显示 `—`，不把缺失解释为 0。只有 base-currency cash 的经济收益和风险天然为 0。
- `carried_cost` 和 `premium_liability` 行的 instrument return、chart、drawdown、Vol 与 Forward RC 均为 `N/A`；即使 payload 错误携带数值零也不得当作真实市场收益或风险展示。只有 base-currency cash / pending monetary row 可以按明确的 monetary 口径显示风险 `0`。

### 2.4 分组的六种类型

| 代码 | 含义 |
| --- | --- |
| `none` | 只在 instrument row 展示；group 和 subtotal 留空。`Instrument` 列仍用作组名或 subtotal 标签，不代表该字段被聚合。 |
| `sum` | 对当前 rows 的绝对量求和；金额先统一为 base currency。 |
| `recomputed_ratio` | 用组级分子和分母重新计算，禁止平均成员百分比。 |
| `current_weight_return` | 用 `as_of_date` base-market-value 权重合成当前成员自身 total return。 |
| `current_basket_path` | 在共同 period 上先生成当前权重篮子 return path，再计算 volatility / drawdown。 |
| `portfolio_risk_contribution` | 成员都相对于同一个完整全组合 forward-risk variance；分组只加总这些贡献。 |

Securities group / subtotal 的当前权重 return 与 current-basket path 使用该组当前 base value 作为分母。普通证券必须提供共同可解释的 base-currency total-return series；只有本币收益而没有 FX overlay 时，不得与其他币种直接拼接。组合级 NAV、分类构成和 `Portfolio Total` 统一由 Overview 的 `Asset Mix` 承载，Holdings 不再重复计算或展示第二套总计。

### 2.5 跨页面事件估值一致性

- Overview 的 Top Holdings 对 event-valued row 显示 operational amount 和权重，但 sparkline、Unrealized P&L、1W、MTD 与 YTD 均为 `N/A`。
- Security detail 的 event-valued hero 使用 `Carrying value`，Quote、Quote Date 和 Unrealized P&L 为 `N/A`；页面不请求或展示 instrument price chart，account slice 与 open lot 也不得从 carrying basis 生成零 unrealized P&L。
- Accounts position API 必须显式返回 `carrying_value`、`fair_value`、`fair_value_coverage_status`、`valuation_basis` 和 `coverage_status`。Accounts UI 按这些字段判断事件估值，相关 Unrealized P/L 为 `N/A`，不得按 instrument type 猜测或按 `market_value - cost_basis` 得出零。

### 2.6 按内容显示的四表 contract

Holdings 使用同一份 `workspace.as_of_date`、canonical NAV 和 read-model rows，按业务语义定义四个独立表面。每类只在对应 rows 非空时挂载标题、数量、视图控件和表格；全部为空时只显示一个统一 Holdings 空状态。组合 headline 已在页面顶部提供 NAV 等总览，分类 signed NAV 与 `Portfolio Total` 则统一放在 Overview 的 `Asset Mix`，Holdings 不重复第二套总计。

Securities、FCN 和 Options 使用同一套 `View`、`Columns` 控件；Securities 另外提供 `Group By`。Cash & Settlement 的字段固定且很少，不提供没有实际价值的视图和列配置。instrument 数量紧邻表标题，不混入按钮区。

| 表面 | 行范围 | 字段合同 |
| --- | --- | --- |
| `Securities` | `holding_category=securities` 的股票、基金、ETF 等 Registry instrument | 独立视图与字段；字段覆盖当前开放头寸、行情、成本、未实现盈亏、标的 return/risk 与 Forward RC。唯一可排序并可使用 `Group By` 的表。 |
| `FCN` | `holding_category=derivatives` 且 `contract_type=fcn` | 独立视图与字段；显式展示账户、币种、名义本金、各 underlying 的 spot、initial 以及 strike/KI/KO 的实际价格和百分比条款、是否可交付、coupon、issue/final-observation/maturity、issuer/counterparty、remaining basis、signed NAV amount、historical carrying basis、carrying FX translation、组合权重与估值状态。`Delivery buffer = spot / strike price - 1`；只有所有可交付 underlying 的行情与 strike 都完整时，才选取其中最小值作为接票监控，任一缺失则整个指标不可用。它表示现价相对接票价的距离，不等于从现价跌至接票价的百分比，也不确认已经发生交付。缺少行情或必要条款时显示不可用，不推断历史 barrier event。 |
| `Options` | `holding_category=derivatives` 且 `contract_type=option` | 独立视图与字段；显式展示 side/type/underlying、expiry、strike、open contracts、multiplier、underlying equivalent、basis type、remaining basis、signed NAV amount、historical carrying basis、carrying FX translation、strike notional、组合权重、lifecycle 与估值状态。风险视图还展示 underlying spot、moneyness、intrinsic value、portfolio-level backing 与简洁 risk state。 |
| `Cash & Settlement` | `holding_category=cash_and_settlement` | 固定字段；settled cash 与 pending monetary row 都按账户与币种分别展示本币余额、base value、FX cost basis、未实现汇兑损益和权重；pending row 另外展示结算边界与关联资产。 |

每个资产表的 subtotal 只用于阅读该资产表；资产 row/subtotal 的 `Portfolio Weight` 始终是 signed base value / canonical total NAV，资产 Forward RC 始终相对于同一个全组合 forward-risk variance。不得因视觉拆表把 Securities、FCN、Options 或 Cash 各自归一成 100%。

表格行本身不承担导航。只有资产、合约或现金/结算行的名称是详情入口；其他单元格保持普通表格行为，并允许按住鼠标左右拖动横向滚动。这样查看宽表时不会因为选中字段或拖动而误入详情页。

`holding_category` 是系统 read-model 字段，不是用户可选的 Group By 维度，也不写入交易事实。`Group By` 只在 Securities 表内部按 taxonomy、instrument type、currency 等字段建立分组；FCN、Options 与 Cash & Settlement 不参与该分组。菜单直接列出当前活动分类，并保留一级和末级分组。Taxonomy / Taxonomy Leaf 读取明确选中的分类及当前 active instrument assignment，不跟随 Holdings `as_of_date` 回放；未选择分类显示 `—`，已选分类但无 assignment 为 `Unassigned`。视图保存具体 taxonomy ID；分类失效后不静默改用其他分类，不存在全局默认分类。covered call 等策略继续由独立股票行和 short Call 行表达，不额外制造策略持仓类型。

`required_underlying_quantity = open_contract_quantity × contract_multiplier`，`strike_notional = strike × required underlying`。Strike 与 strike notional 使用 Registry underlying 的报价币种（`strike_currency`），不能沿用可能不同的合约权利金币种；base amount 只有在 as-of FX 可用时才发布。written Call 的 backing 将组合内同一 underlying 的现有股票数量与该标的全部 open written Call 所需数量汇总比较；written Put 将行权价币种的 settled cash 与该币种全部 open written Put 的 strike notional 汇总比较。结果只是一项组合层即时风险提示，不把股票或现金分配给具体合约，也不代表券商保证金或质押状态；不同币种的 premium/strike 没有明确兑换条款时不生成净 payoff，也不自动实物交割。covered call 等策略仍由独立股票和期权事实表达，不新增策略持仓类型。

Pending row 的 identity 固定为：

```text
pending:{kind}:{posting_account}:{economic_instrument}:{currency}:{settlement_date}:{pending_until_date}
```

它防止相同账户/资产/币种但不同结算边界的余额发生主键冲突。普通现金结算使用结算现金账户，红利再投资应收使用原证券账户；后者展示为 `settlement_receivable`，不会增加可用现金。`pending_status` 目前包括 `awaiting_settlement`、`settled_awaiting_position` 和 `overdue`；本币 signed amount 与 base amount 必须同时保留，base FX 不可用时本币金额仍可展示但 base aggregation 为 unavailable。

Settled cash 的 instrument identity 为 `cash:{currency}`，行 identity 为 `cash:{currency}:{account_id}`。每个账户使用移动平均的 historical base-currency monetary basis：价值第一次进入账本时按 `monetary_recognition_date` 建立；pending receivable / payable / subscription bridge 转成 settled cash 时原样继承，不在 settlement date 重置。现金减少时按当时平均 basis 释放；组合内部同币种现金划转也继承来源账户 basis。`monetary unrealized FX P&L = current base value - historical monetary basis`。历史或当前 FX 不完整时对应行的汇兑损益为不可用，不以当前汇率回填历史成本。

同一现金生效日按交易时刻、创建时间及 `transaction_sequence` 重放；即使同批记录创建时间相同，先出后入与先入后出的历史汇率成本也必须分别按真实顺序核算，不能按倒序展示列表计算。

`FX Cost Basis` 是货币资产或负债的历史基准币成本，不是证券 `Book Cost`。一个 pending row 若合并了同一结算边界下多笔不同 recognition date 的交易，只展示加权 FX basis；单一 recognition date 仅在组成交易一致时返回。

Workspace API 的 `operational_summary` 保留 open short-option contract count、expiry buckets、option obligation strike notional 和 settlement receivable/payable/net；`operational_alerts` 返回 severity、code、message 和真实 `related_line_ids`。Holdings 的 `Operational Status` 面板集中展示需要处理的提醒，并链接到相关行。到期已到、负 settled cash 和逾期结算是 critical，七日内到期与 settlement FX unavailable 是 warning；到期检查同时覆盖 long 和 written option。动态 workspace、materialized snapshot 和 instrument detail projection 对当前字段必须保持 parity。组合任一页面打开时，系统检查已过期但仍有 open long/writer quantity 的期权；同一批未决事项在浏览器会话内自动提示一次，也可从页头入口重新打开。用户确认作废、现金结算或实物行权/指派后直接入账，不引入额外审查流程。

详情页按资产语义拆分，不能强行共用普通证券模板：

- Security detail 展示标的自身交易、开放 lots、已实现/未实现损益和 distributions，并单列与该 underlying 关联的期权活动及期权损益；实物交割的期权腿和股票腿通过 `option_delivery_link` 可相互核对。
- Option detail 展示合约条款、underlying 的真实 price-level chart 与 strike 线、到期 payoff、intrinsic value、moneyness、剩余 long premium 或 writer liability、到期与 backing 风险。没有可靠期权 fair value、波动率或 Greeks 时不估造 time value、daily P&L 或 Greeks。
- FCN detail 按 underlying 展示真实 price-level chart 及 initial/strike/KI/KO 参考线与距离，并集中提示期限和价格区域风险。它只呈现当前可验证状态，不把当前价格穿越某条线解释成历史上已经发生的 knock-in/knock-out。
- Cash detail 锁定到一个具体账户与币种，展示本币余额、base value、historical monetary basis、未实现 FX P&L、可交易状态和该行关联的 ledger facts；Settlement detail 展示 signed receivable/payable、recognition/settlement 边界、pending 状态和 economic instrument。两类 monetary detail 都不请求证券价格图或 position lots。

详情页的信息层次与口径：

- 总览首屏保留仓位与资产特有风险，估值来源和核算说明放在可展开区域。股票、ETF 和基金图表展示标的自身序列；横轴按实际日期间隔排列，最大回撤只针对所选图表内的观测值，不代表持仓收益或整个持有期风险。
- 「区间盈亏」读取 `/performance/calculation/groups?axis=instrument`，按 `group_key` 精确选择本标的，以组合本位币列示已实现价格损益、未实现价格损益变动、分红／票息、汇兑损益及费用税费。使用返回的实际起止边界；收盘至收盘的区间不包含期初日活动。当前未实现余额与区间未实现变动不得混用。关联期权单独核算；同一合约的买方和卖方区间结果合并展示并明确标注。
- FCN 优先展示已收净票息、挂钩标的与条款线距离、所选标的走势。已收票息仅计截至日已结算的确认交易；缺失票息率、初始价格和观察条款不推算。距离定义为 `spot / contractual level - 1`，不解释为触发概率。敲入观察不等同于已核实股票接票；最终实物兑付通过 asset_deliveries 保存证券交付事实，并关联接收证券的成本与历史。
- 期权内在价值分别按每股与当前合约数量展示，不代表期权市价；到期图只表示期权本身，排除正股和其他对冲。条款中的 Decimal JSON 字符串需先转为数值，再做行权线、盈亏平衡价与损益运算。买方采用剩余成本，卖方采用剩余税费前权利金；开仓费用与期末费用口径明确说明。
- 批次页支持开放／已关闭筛选；卖方期权展示义务批次与平仓记录。已退出持仓仍使用批次原币，不能在当前 holding 缺失时误标为组合本位币。拆股后的持有起点保留 acquisition date，成本延续可在事件和批次中追溯。
- 「收入与事件」展示已确认的分红、票息、合约结果和已应用份额变更，按权益／经济日期筛选，并单列结算日期。来源公告的预计权益与已入账收入分开；分配核对状态明确为当前状态，不声称是历史快照。

CSV/XLSX 只为非空的 `Securities`、`FCN`、`Options`、`Cash & Settlement` 输出各自独立的标题与表头，不再把异质字段压进带 `Category` 的统一 schema，也不重复导出组合总计。Securities、FCN 和 Options 跟随各自当前视图的可见字段；Cash & Settlement 导出固定字段并包含 FX cost basis 与未实现 FX P&L。Securities 额外跟随当前筛选、排序和可选 Group。价格路径、收益、未实现盈亏和回撤的不适用值导出为 `N/A`；衍生品的 Vol / Forward RC 同样导出 `N/A`，只有明确 modeled-zero 的 monetary risk 输出数值 `0`。

## 3. Securities 可配置字段与聚合字典

下表必须与前端 `HOLDINGS_GROUP_AGGREGATION_KIND` 一一对应。表内的机器可读标记由测试使用，增删字段时必须同时更新实现、本文和 contract tests。

<!-- holdings-column-contract:start -->
| 字段 key | UI 标签 | instrument row 口径 | group / subtotal | 类型 |
| --- | --- | --- | --- | --- |
| `instrument` | Instrument | canonical instrument name；现金为对应币种现金名称 | 仅作为组名或 subtotal 标签 | `none` |
| `ticker` | Ticker | Registry primary identifier | 留空 | `none` |
| `instrument_type` | Instrument Type | canonical instrument type | 留空；可作为 Group By 维度 | `none` |
| `taxonomy_top` | Taxonomy | Securities 使用明确选中分类的顶层节点；未选分类为 —，已选但无当前 assignment 为 Unassigned；不按 Holdings `as_of_date` 回放 | 留空；可作为 Securities 的 Group By 维度 | `none` |
| `taxonomy_leaf` | Taxonomy Leaf | Securities 使用明确选中分类的当前叶节点；未选分类为 —，已选但无当前 assignment 为 Unassigned | 留空；可作为 Securities 的 Group By 维度 | `none` |
| `currency` | Currency | instrument currency；现金为该现金币种 | 留空；可作为 Group By 维度 | `none` |
| `holding_date` | Holding Since | 当前开放头寸里最早的 holding start date；不包含已经平掉的旧头寸 | 留空，不拼接不同成员起点 | `none` |
| `quantity` | Quantity | 当前开放 quantity；short-option obligation 为负的 open contract count；现金行是 settled cash amount | 留空，不跨不同份额单位求和 | `none` |
| `cost_method` | Cost Method | 账户成本法；跨账户不一致时为 Mixed | 留空 | `none` |
| `avg_cost_book` | Book Avg Cost | market/event position 的 `remaining cost basis / remaining quantity`；option obligation 和 cash 不适用 | 留空，不平均成员成本 | `none` |
| `last_price` | Quote | `as_of_date` 选中的 valuation quote；event-valued asset、option obligation 和 cash 不适用 | 留空 | `none` |
| `quote_date` | Quote Date | 上述 valuation quote 的实际日期；event-valued row 必须为空 | 留空 | `none` |
| `quote_basis` | Quote Basis | 实际 market quote basis，例如 `official_nav` 或 `close`；event-valued row 无 quote，因此为空 | 留空 | `none` |
| `quote_provider` | Provider | 选中 valuation series 的 provider | 留空 | `none` |
| `quote_status` | Quote Status | valuation selection / freshness 状态 | 留空 | `none` |
| `current_fx_rate` | Current FX to Base | `as_of_date` 的 instrument currency 到 portfolio base currency 汇率 | 留空，不平均汇率 | `none` |
| `market_value` | Position Value (Local) | market-valued position 的本币 fair value | 留空，不跨币种加总 | `none` |
| `market_value_base` | Position Value (Base) | 上述 signed NAV amount 按 `as_of_date` FX 转为组合 base currency | 直接加总 | `sum` |
| `cost_basis` | Book Cost (Local) | position 的本币 remaining book cost | 留空，不跨币种加总 | `none` |
| `cost_basis_base` | Book Cost (Base, Current FX) | 本币 remaining book cost 按 `as_of_date` FX 换算 | 直接加总 | `sum` |
| `cost_basis_historical_base` | Book Cost (Base, Trade FX) | 每个剩余成本来源按 acquisition-date FX 换算后的 base cost | 覆盖完整时直接加总 | `sum` |
| `cost_basis_fx_rate` | Weighted Cost FX | `Book Cost (Base, Trade FX) / Book Cost (Local)` | 留空，不平均汇率 | `none` |
| `net_invested` | Net Invested (Local) | 当前连续持仓周期的买入净投入减已卖回款、已实现收入与资本返还 | 留空，不跨币种加总 | `none` |
| `break_even_price` | Break-even Price | `Net Invested (Local) / current quantity` | 留空，不平均价格 | `none` |
| `weight` | Weight | `market_value_base / portfolio NAV`；written liability 为负权重 | 加总当前 row 权重 | `sum` |
| `accounts` | Accounts | 当前持有该 instrument 的 distinct account 数 | 留空，账户集合可能重叠 | `none` |
| `open_lots` | Open Lots | 当前开放 lot 数；moving average synthetic lot 按一个开放 lot 展示 | 加总开放 lot 数 | `sum` |
| `unrealized_price_pnl` | Price P&L (Local) | `market value local - Book Cost (Local)` | 留空，不跨币种加总 | `none` |
| `unrealized_price_pnl_base` | Price P&L (Base) | 本币价格未实现损益按 current FX 换算 | 覆盖完整时直接加总 | `sum` |
| `unrealized_fx_pnl_base` | FX P&L (Base) | `Book Cost @ Current FX - Book Cost @ Trade FX` | 覆盖完整时直接加总 | `sum` |
| `unrealized_pnl_base` | Total Unrealized P&L (Base) | `market value base - Book Cost @ Trade FX`，并严格等于 base price P&L 加 FX P&L | 覆盖完整时直接加总 | `sum` |
| `unrealized_return` | Price Return (Local) | `Price P&L (Local) / Book Cost (Local)` | 留空，不平均成员收益 | `none` |
| `unrealized_return_base` | Total Unrealized Return (Base) | `Total Unrealized P&L (Base) / Book Cost (Base, Trade FX)` | 以组级总盈亏和历史汇率成本重算 | `recomputed_ratio` |
| `instrument_return_1w` | 1W Total Return | instrument 自身本币 total return，锚点为请求 `as_of_date - 7` 日或此前最近点；含复权收入、不含 FX | 当前 base-market-value 权重合成，要求完整覆盖与兼容币种 | `current_weight_return` |
| `instrument_return_1m` | 1M Total Return | instrument 自身本币 total return，按请求 `as_of_date` 回看 1 个自然月；不含 FX | 当前 base-market-value 权重合成，要求完整覆盖与兼容币种 | `current_weight_return` |
| `instrument_return_3m` | 3M Total Return | instrument 自身本币 total return，按请求 `as_of_date` 回看 3 个自然月；不含 FX | 当前 base-market-value 权重合成，要求完整覆盖与兼容币种 | `current_weight_return` |
| `instrument_return_6m` | 6M Total Return | instrument 自身本币 total return，按请求 `as_of_date` 回看 6 个自然月；不含 FX | 当前 base-market-value 权重合成，要求完整覆盖与兼容币种 | `current_weight_return` |
| `instrument_return_mtd` | MTD Total Return | instrument 自身本币 total return；锚点严格早于月初；不含 FX | 当前 base-market-value 权重合成，要求完整覆盖与兼容币种 | `current_weight_return` |
| `instrument_return_ytd` | YTD Total Return | instrument 自身本币 total return；锚点严格早于年初；不含 FX | 当前 base-market-value 权重合成，要求完整覆盖与兼容币种 | `current_weight_return` |
| `instrument_return_1y` | 1Y Total Return | instrument 自身本币 total return，按请求 `as_of_date` 回看 12 个自然月；不含 FX | 当前 base-market-value 权重合成，要求完整覆盖与兼容币种 | `current_weight_return` |
| `instrument_current_drawdown` | Current DD | confirmed total-return series 的最新点相对历史峰值 | 用共同 period 和当前权重篮子 path 重算 | `current_basket_path` |
| `instrument_max_drawdown` | Max DD | confirmed total-return series 可用历史上的最大回撤 | 用共同 period 和当前权重篮子 path 重算 | `current_basket_path` |
| `instrument_holding_max_drawdown` | Held Max DD | 从当前最早开放持仓日起的最大回撤；不含已经平掉的旧持有期 | 留空，不为不同持有起点创造伪共同区间 | `none` |
| `instrument_volatility_1m` | 1M Vol | 普通证券为 confirmed total-return series 的 trailing sample volatility；衍生品为 N/A，modeled-zero monetary row 为 0 | 从风险 scope 排除衍生品后，用 1M 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `instrument_volatility_3m` | 3M Vol | 同上，3M 窗口 | 从风险 scope 排除衍生品后，用 3M 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `instrument_volatility_6m` | 6M Vol | 同上，6M 窗口 | 从风险 scope 排除衍生品后，用 6M 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `instrument_volatility_1y` | 1Y Vol | 同上，1Y 窗口 | 从风险 scope 排除衍生品后，用 1Y 共同 period 当前篮子 path 重算；modeled-zero monetary rows 作为 0-return 成员 | `current_basket_path` |
| `forward_risk_share` | Forward RC | eligible 普通证券相对于同一 total-portfolio variance 的 contribution share；衍生品为 N/A，modeled-zero monetary row 为 0；缺少可用估值的普通证券为 N/A | eligible 风险模型完整时加总，衍生品不进入合计；纯 modeled-zero monetary group 的行级贡献为 0；未覆盖敞口仍保留在风险覆盖摘要 | `portfolio_risk_contribution` |
| `price_chart_1m` | Chart 1M | 1M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_3m` | Chart 3M | 3M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_6m` | Chart 6M | 6M 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `price_chart_1y` | Chart 1Y | 1Y 有界采样展示路径，保留真实 basis / semantics | 留空 | `none` |
| `coverage` | Coverage | valuation、NAV、FX、trend series 等覆盖诊断 | 留空；可作为 Group By 维度 | `none` |
<!-- holdings-column-contract:end -->

表内所有“可作为 Group By 维度”的说明都只适用于 Securities 区段；FCN、Options 与 Cash & Settlement 始终保留为独立一级区段。

## 4. 分组计算细则

### 4.1 当前权重收益

对 group 中有当前市值的成员：

`group_return = Σ(current_market_value_base_i / group_market_value_base × instrument_total_return_i)`

覆盖不足时整个 group 留空，不能剔除缺失成员后重新归一。该结果表示“如果当前篮子在该历史窗口一直保持当前权重”，不是实际组合 TWR。

当前 Securities group scope 中只要存在非零 event-valued asset，价格/汇兑未实现盈亏、未实现收益和 instrument return 均失败关闭为 `N/A`。Operational amount 仍参与 Position Value、Weight 和 NAV 对账；Forward RC 继续使用全组合风险模型的共同分母。

### 4.2 当前篮子风险

普通证券先构造共同的日频 mark-to-last 路径，再用当前权重得到 group return series，不能加权平均成员风险值。group 与 subtotal 使用各自篮子的净市值分母。只有至少一个可建模市场成员并且共同路径完整时才发布 Volatility、Current DD 与 Max DD。

成员可以在共同起点之前拥有不同长度的早期历史；共同起点取各成员首个合法 period start 中最晚者。从该点开始，每个成员必须具有完全相同、内部连续且顺序一致的 `(period_start, period_end)` identity：后一段的 start 必须等于前一段的 end。任何成员在共同区间中间或尾部缺一段，或所有成员共同缺失同一段，整个 group 指标都应显示不可用，不能静默取交集后把缺口隐藏。

### 4.3 Forward RC

所有 eligible 市场成员必须来自同一个完整 Production Risk Model 和同一 leaf covariance matrix，权重为 signed base exposure / total NAV。`risk_eligible` 由正式证券身份、持仓类型、实际 `market_quote` 估值、完整覆盖和可用价格派生；页面所选分类、assignment 和目标不控制实际持仓风险。未分类或零目标的持仓仍参与能够支持的风险计算。窗口从 holdings as-of date 按自然月回看；eligible member return 的 start/end period identity 必须完全一致，strict 与 complete-case policy 都校验尾部新鲜度。group / subtotal 只能加总成员相对于同一 total-portfolio variance 的 risk share；不能先合成 group return 再运行 shrinkage，也不能在每个 group 内另建局部分母。`abs` mode 使用精确绝对贡献，零贡献保持为零。

行级 `modeling_status` 明确区分 `eligible`（可交给风险模型验证收益窗口）、`unavailable`（缺少可用估值／身份）、`unsupported`（当前不支持的衍生品模型）和 `cash_or_settlement`；`exclusion_reason` 为系统生成的实际原因，`excluded_rows` 保留相同状态。风险覆盖 payload 不携带分类政策／配置版本，也没有独立子组合 TWR 占位字段。

Event-valued asset 和 derivative liability 不进入 covariance matrix，行级 `forward_risk_share`、contribution 与 modeled volatility 留空，状态为 `excluded`。Holdings `risk_coverage_summary` 和 Forward-risk summary 必须返回 `model_name=Market risk model`、total NAV、`modeled_net_exposure`、`modeled_gross_exposure`、`excluded_carrying_value`、`excluded_liability`、`cash_unallocated_exposure`、coverage ratio 和 `excluded_rows`。没有 eligible risky holding、total NAV 无效、存在无法建模的非本币 monetary 或估值不可用的普通证券敞口，或者 eligible member 的 return/FX/period identity/weight/variance 不完整时，整个 forward risk 失败关闭。modeled-zero monetary row 的行级贡献可以明确为 0，但它本身不能使纯现金组合得到可观测组合风险。

## 5. 不可用与排查顺序

`—` 表示缺数据、口径不适用或严格条件未满足，不等于 0。衍生品的风险字段固定为 `N/A`；明确显示的风险 `0` 只适用于 modeled-zero monetary rows。排查顺序：

1. 数量或 Holding Since：检查 Transactions 的 trade date、账户、instrument、quantity 和历史卖出/转仓；
2. 成本或未实现盈亏：检查账户 FIFO / moving average、成本来源 acquisition date、历史/当前 FX、gross amount、费用税费、分红/资本返还分类；
3. 市值或权重：检查 valuation quote、quote basis、price scale、FX 和 snapshot freshness；
4. Return / Vol / Drawdown：检查 Registry total-return semantics、窗口锚点、历史覆盖和 risk frequency；
5. group 指标：检查每个当前成员是否 100% 覆盖、return currency 是否一致；
6. Forward RC：先检查真实持仓／估值覆盖、total NAV 与未覆盖敞口和原因，再检查 eligible members、Production Risk Model、完整对齐收益矩阵、base-currency return、total-portfolio variance 和 workspace status。

## 集中度视图

Holdings 的“敞口与集中度”独立于账面持仓表：直接证券按账户绝对市值求和，FCN 按剩余名义本金并在每套分类中分配一次；期权、现金和待结算不进入分子，组合账面 NAV 仍为分母。不得使用已跨账户净额合并的 `allocation` 替代 gross 敞口。期权未具备可靠 delta，不能将权利金、账面负债或执行价名义额作为 delta 敞口。

投影行包括 `exposure_base`、`known_exposure_base`、`weight`、`lower_bound_weight`、`limit_weight`、`headroom_weight`、`status`、来源和覆盖说明。缺数据时完整金额/权重为空，保留已知下界；下界已超过上限仍可标超限，否则不能宣称限额内。`scope.enabled` 只控制对应 taxonomy 节点提醒，关闭时保留上限。单证券/FCN 上限全组合唯一，空白不限制，0 禁止正敞口，等于上限不算超限。

父节点汇总全部后代，父子行及不同分类树不可重复相加。限额从所展示估值日生效；编辑读取区分当日生效 revision 与最新写入 revision，后者用于并发校验。Overview 只显示同一投影中的超限和无法判断摘要，完整明细与 FCN 分配编辑留在 Holdings。
