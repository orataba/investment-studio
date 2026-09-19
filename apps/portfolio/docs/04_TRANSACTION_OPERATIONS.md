# Transaction Operations Contract

本文定义 Portfolio `Transactions` 工作台、API 与派生账本之间的操作边界。计算公式仍以
[01_CALCULATION_SPEC.md](./01_CALCULATION_SPEC.md) 为准；本文只说明如何安全地创建、修改、
删除和核对交易事实。

交易录入的证券搜索同时覆盖已登记资产和股票/ETF 全市场目录。直接交易、FCN/Option 合约底层证券和 FCN 实物交付证券（手工录入及材料复核）使用同一目录登记流程，无需先录入一笔交易。搜索只读；选中目录证券后，由共享数据维护入口按需建立资产身份、配置行情来源并补齐行情，重复选择复用同一资产。候选及建档后的规范记录都需满足账户币种和交易类型；建档失败保留录入内容并显示错误。选择证券与保存交易是两个独立动作，注册不会自动入账。股票/ETF 优先使用共享行情；缺少历史或已完成交易日的报价时，由现有采集器补拉并保留原始响应及观测时间，再投影到资产行情。失败保留明确错误，可重新选择补齐，不能用空行情报告准备完成。

## 1. 一条记录代表什么

`transaction_record` 保存已经确认的经济事实，不直接保存 Holdings、cash balance、
ledger posting 或 position lot。后四者均由交易事实、账户事实和行情事实确定性派生。

- `account_id` 是交易所属账户；
- 证券交易的现金腿由 `settlement_cash_account_id` 指向结算现金账户；
- 交易列表筛选现金账户时，同时包含该账户的直接现金活动、证券/衍生品结算，以及换汇入账；内部划转只展示该账户自己的腿，不把配对腿重复计入。
- 内部转仓必须通过 internal-transfer command 一次生成成对事实，不能单独修改其中一腿；
- 期权实物行权或指派必须通过 option-outcome command 一次生成期权关闭腿和股票交割腿，并保存严格一对一的 `option_delivery_link`；
- note 只保存操作来源或必要说明，不承担日期、金额、份额等结构化事实。

Transactions 页面主表用于定位交易；完整 note、派生 postings、position lots 与 change
history 只在右侧 inspector 展示，避免主表把审计说明和经济字段混在一起。

Transactions 录入界面使用 `Security / FCN / Option / Cash & Operations` 四个一级入口，
但不会把界面入口另存成一套账务分类：Security 映射到 `asset_domain=security`，FCN 与
Option 映射到 `asset_domain=derivative` 并分别使用 `contract_type=fcn/option`，现金及运营
事实映射到 `asset_domain=cash`。账户、证券或合约、可用动作按该入口依次收敛，避免把
证券交易录入衍生品账户，或把不同合约生命周期混入同一个动作列表。

新建衍生品合约时，合约条款与本次交易事实分开填写。新 Option 只能买入开仓、卖出开仓
或建立期初余额；新 FCN 只能记录进入或期初余额。平仓、利息和生命周期结果必须引用已经
存在的 Portfolio contract。Option 条款必须用 `underlying_instrument_id` 引用
Registry 股票，不能靠合约名称、ticker 文本或 note 推断。页面、AI 草稿、JSON、CSV 和 Excel
共用这条合同。后续交易只引用 contract ID；补充或纠错使用审计修订入口，保存前后版本、确认人和依据，不通过交易覆盖条款或替换产品身份。

## 2. 日期和时间

| 字段 | 业务含义 | 影响 |
| --- | --- | --- |
| `trade_date` | 下单、成交或定价事实日 | 交易顺序、价格与可用份额校验 |
| `trade_time` | 已知的真实成交时刻 | 同日交易排序 |
| `position_effective_date` | 份额进入或离开 EOD 持仓的日期 | position、lot、持仓估值 |
| `settlement_date` | 现金实际结算日期 | settled / pending cash posting |
| `entitlement_date` | 收入权利确认日；关联费用可指定历史权益日 | dividend / coupon / reinvestment income 与显式历史权益校验 |

未知成交时间必须留空。后端会使用配置的默认时点完成确定性排序，同时写入
`trade_time_is_estimated = true`；默认时点不是精确成交事实。开盘前或盘中成交且应进入
当日 EOD 持仓时，`position_effective_date = trade_date`。按当日收盘 NAV 申购、下一交易日
确认份额时，保留真实 `trade_date`，并把确认日写入 `position_effective_date`。

期权实物结果两腿共用 `trade_time`。先买股后交割须保存各自已知实际时刻，不能依录入先后或改造时间表示交易顺序；未确认的时间仍留空。

后端使用 `transaction_sequence` 作为同日同时间、同创建时间事实的确定性顺序；现金入出和内部转账同样使用该顺序重放，不读取页面正序或倒序作为账本先后。

初次买入的份额确认日没有正式行情时，可按当日确认买入总金额除以总份额形成成交基准估值，并标明交易来源；费用税费照常计入损益，不能揉进该估值价格。正式行情存在时优先使用，下一交易日仍需正式价；已有持仓加仓不能用当次成交价替旧持仓补价，也不要把这项组合初始估值写入共享行情。FCN 非现金交付和期权实物交付关联的股票腿不适用此例外，交付价值与行权价不能代替正式行情。

## 3. 金额、份额和价格

所有输入均使用非负 magnitude，方向由所选资产的交易动作决定。

- 基金申购 / 赎回：确认金额与确认份额固定，unit price 由二者和 Registry price-scale
  contract 反算并按 source precision 保存。页面展示的 NAV 仅作参考，不自动写入成交价格。
- ETF / 股票：数量与实际 execution price 为常用锚点，gross amount 由 price-scale contract
  计算；若成交回单直接给出金额，可用金额反算价格。
- FCN：合约进入、期间利息收入和关闭分别记录；关闭结果可选正常到期、敲入或敲出。末期票息及合约费用可作为关闭记录的绑定现金明细，和收股、现金尾差一起保存、修改或删除；已单独录入的票息不要重复添加。
- FCN 的 notional 是每张合约名义本金，quantity 是合约数量；成交金额可以因折价、溢价或二级交易而不同于名义本金。敲入观察单独使用 `knock_in_observation`，金额为零、不填数量，不关闭持仓；note 保存发行人确认依据。最终现金兑付、票息和实际结算日分别记录。
- 期权：数量单位为合约张数，gross amount 使用本地合约 multiplier；普通开平仓与现金结果各自记录。经人工确认的实物行权/指派由专用命令把期权零现金关闭和按 strike 的股票买卖原子落账，费用与税费只进入股票腿。
- fees、taxes 与 fee category 必须分开保存，不能揉进 gross amount。

## 4. 现金和待确认资产

现金结算与份额确认可以发生在不同日期。系统用 settlement subledger 表示
`pending_subscription`、`settlement_receivable` 或 `settlement_payable`，而不是在证券账户
提前伪造份额，也不是把在途金额当作 settled cash。确认日到达后 bridge 消失并转为真实
position；组合 NAV 在等待期间仍必须对平。

红利再投资若权益日早于新份额生效日，权益日先确认收入和原证券账户的应收，新份额到 `position_effective_date` 才进入持仓并以再投资金额形成成本。它不经过现金账户，不填写一笔虚构现金收付，也不能在份额确认时再确认一次收入；确认前应核对「原持仓市值 + 再投资应收」，而不是只核对原持仓市值。

外币 monetary value 第一次进入账本时按 `monetary_recognition_date` 建立 historical base
basis。pending balance 转成 settled cash 只改变状态，不重置 basis；同币种内部现金转账也继承
来源账户 basis。换汇的目标币种 basis 使用实际 source consideration，不使用行情反推成交金额。

卖出或赎回交易的 Fact 面板同时展示组合基准币口径的 realized position P&L，并拆成 price 与
position FX。historical cost 来自实际释放的 lot origins；随后形成的 receivable / cash 使用独立
monetary basis，不能把结算后的 FX 再算进 position realized P&L。完整例子见
[05_MULTI_CURRENCY_ACCOUNTING_EXAMPLE.md](./05_MULTI_CURRENCY_ACCOUNTING_EXAMPLE.md)。

当 withdrawal、证券结算、费用、税费或换汇等交易减少非基准币 monetary exposure 时，Fact
面板另列 `Realized cash FX`：按释放数量取得移动平均 historical monetary basis，并与该交易
结算日的 base-currency fair value 比较。它与 realized position price / FX P&L 分开，避免把
持仓处置与之后现金持有期的汇率变化重复计算。同币种内部现金转账只转移 basis，不确认 cash FX；
剩余外币现金继续在 Holdings 显示 unrealized FX P&L。

## 5. 创建、修改和删除控制

- 单笔创建、内部转仓、期权结果及文件批量导入均强制要求非空 `Idempotency-Key`。页面为每次操作生成 key，网络重试复用同 key；同 key 只能重放同一 operation 与同一 payload；
- 页面在请求完成前锁定提交按钮，防止双击生成重复事实；
- update 必须携带当前 `expected_row_version`，缺失返回 validation error，版本过期返回 conflict；
- delete 必须携带完整 delete scope 的 row-version map；成对转仓与 option physical-delivery pair 都必须整组删除；已关联的交割腿不能单独修改；
- create / update / delete 都追加 change log。Transactions inspector 的 History 标签显示版本、
  时间和变更字段，不能用直接改库替代正常修订流程。
- 账户已有资产交易（包括 FCN / Option）后不能切换成本法来静默重算旧 lots 和已实现损益；空账户可选 FIFO 或移动平均。需要不同成本法时建立新账户，历史事实保持不变。

## 6. 操作后核对

每次录入或修订至少核对：

1. activity type、账户、证券与币种；
2. trade、position-effective、settlement、entitlement 日期是否各自表达真实事件；
3. 基金确认金额与份额是否保持原始精度，反算价格是否未被参考 NAV 覆盖；
4. Net Cash Effect、postings、position lot 与 realized price / FX split 是否一致；
5. History 是否新增正确版本；
6. 受影响的 Holdings、Performance 与 snapshots 是否已被标记并刷新。

## 7. CSV 输入与输出

Transactions 工作台只使用一套标准 CSV 字段：

| 入口 | 范围 | 结果 |
| --- | --- | --- |
| `Export All Transactions` | 当前 Portfolio 的全部交易 | 已填充的标准 CSV，可直接交给 `Import CSV` |
| `Blank CSV Template` | 只有同一套标准字段 | 适合程序或熟悉字段的用户批量录入 |
| `Blank Excel Template` | `Transactions` 上传页 + 填写说明 + 字段指南 + 全量交易示例 + 枚举列表 | 提供给需要手工填写的协作者；只有 `Transactions` 页会被上传 |
| `Import CSV` | 上述导出文件或填好的模板 | 先整批预览，再原子写入 |

CSV 表达的是交易命令，不是数据库行，因此不包含 transaction ID、row version、change
history、解析后的时间戳或内部配对 ID。导入时仍会按目标 Portfolio 的账户、证券、合约、币种
和持仓历史重新校验。导入必须先预览；
有任意行错误或批次错误时不允许提交，确认后整批写入，不能只写入其中的有效行。

### Excel 手工填写模板

提供给其他人手工录入时，优先使用 `Blank Excel Template`。模板的 `Transactions` 工作表仍
保留标准 CSV 的 46 个字段（包含交付、绑定现金明细、批次引用及补充条款），第一行字段名不可修改，默认没有示例数据，因此不会把示例误导入。
模板另外提供以下工作表：

- `Instructions`：填写流程、普通交易与内部转移的必填规则、金额方向和上传限制；
- `Field Guide`：每个字段的中文含义、必填条件和跨字段约束；
- `Examples`：现金、证券、基金、期权、FCN、费用税费、换汇、内部转移和期初余额的可复制示例；
- `Lists`：资产类型、各资产可用交易动作、费用类别、币种和期权类型的代码及说明。

Excel 会对通用枚举、日期、时间和非负数提供下拉或输入限制，并用颜色提示普通交易的条件必填
列。账户、证券和衍生品合约不会预置在模板中；填写人必须使用提供方已经设置好的精确 ID。
Excel 端限制只用于减少手工错误，账户归属、币种、资产类别、持仓历史和交易动作组合仍以
Import 预览的后端校验为准。

模板覆盖范围如下。公募基金和私募基金使用相同的证券交易命令，基金类别由 Registry 中的证券
资料决定，不在交易行重复声明。

| 范围 | 示例覆盖 |
| --- | --- |
| 现金与运营 | 入金、出金、利息、换汇、独立费用/税费、现金期初余额、现金内部转移 |
| 股票、ETF、基金 | 买入、卖出/赎回、分红、红利再投资、返还资本、期初持仓、独立费用/税费、证券转仓；股票/ETF 另支持卖空、买回和期初空头，基金不支持卖空 |
| FCN | 新合约进入、多标的条款、期初持仓、提前退出、票息、费用、税费、到期、敲入观察/结果、敲出；实物收股通过原子多腿记录，可含碎股现金及显式 FX |
| Option | Call/Put、长短仓开平仓、长短仓期初、费用税费、到期、现金结算及原子实物行权/指派；文件保留股票交付关系 |
| 多币种 | USD、HKD、CNY、EUR、GBP、CHF；证券、持仓账户、合约、结算现金账户和交易币种必须满足同币种规则 |

系统目前支持的交易币种为 `USD`、`HKD`、`CNY`、`EUR`、`GBP` 和 `CHF`。普通资产交易的 `currency` 必须与持仓账户、
证券或衍生品合约及结算现金账户一致；证券转仓要求证券与转入、转出账户币种一致，现金内部转移
只能在同币种现金账户间进行。换汇行的 `currency` 是转出现金账户币种，
`counter_amount = gross_amount × fx_rate`，目标币种由 `counterparty_account_id` 对应账户确定。

FCN 最终结算使用一张结算单：`asset_deliveries` 保存实际收股数量、本币确认总价值与合约折算汇率，`gross_amount` 只填合约币种现金尾差，`settlement_cashflows` 保存末期票息及合约费用税费。CSV/Excel 对应 `asset_deliveries_json` 和 `settlement_cashflows_json`。不要另造本金现金兑付、换汇或股票买入；只有回单确认账户实际发生独立换汇时才另录真实换汇。

每条收股明细分别填写经济生效日对应的公允确认价值、实际证券到账日、股票取得费用税费及同证券币种的扣费现金账户。取得费用计入股票成本并只扣一次现金，不再次冲减 FCN 处置损益。`fx_rate_to_contract` 为每单位证券币种折合的合约币种；`quantity_fx_rate` 为按条款计算股数时使用的反向汇率，和碎股数量、碎股计价依据一起仅作回单证据。合同接票价、确认公允价值及随后真实股票成交价分别保留，不能相互代填。

收股自经济生效日起进入估值和市场风险，实际到账前在详情标记待交付；现金尾差、股票取得费用和其他现金明细按各自结算日进入现金。实际证券到账日未录入时显示未知。到账前 FIFO 账户如有其他已到账股票，卖出需指定该批次；平均成本账户需等同标的待交付股票到账后再处置，以免错误消耗未到账来源。修改或删除结算不能破坏后续真实卖出及账户历史。

FCN 与所收股票详情均展示整笔投资追踪：FCN 处置损益、票息及合约费用，加上来源股票后续已实现、未实现和现金股息；与原有同标的股票混仓时按真实来源批次分配。股票取得费用已包含在其成本中。缺少所需行情或汇率，以及尚不能完整追溯的红利再投资、返还本金或跨证券公司行动，会明确显示不完整与 N/A。已关闭 FCN 可从股票详情或原结算记录回看。

Option 的实物行权/指派由同一个 outcome command 原子生成股票腿；CSV/Excel 用 physical_long / physical_written + option_delivery_json，导出保留关系，不再导入股票腿。`opening_balance`、`opening_written`、`short_opening_balance` 的交易和结算日必须等于组合 inception；原开仓日填 acquisition_date，剩余负债或成本不重复影响期初现金。

CSV/Excel 的 `derivative_additional_terms_json` 保留经确认的结算方式、行权风格、FCN 观察/付息日期及条款依据，导出后可原样回导；基础条款仍使用专用列。条款缺失时界面显示未确认，不自动补成美式、现金交割或实物交割。截图提取不能仅凭“购/沽”、日期或“卖空”猜测期权/权证身份或开平仓方向。

裸卖 Call 后先实际买股再交割，或券商先确认股票空头再买回，都按实际先后记录。后一种明确勾选券商已形成证券空头；股票负仓采用 FIFO / 移动平均，买回按真实成交价和费用核算。普通卖出不会悄悄转成卖空。融资借还和抵押释放使用独立现金用途账户间的内部划转；已抵押现金不列为自由现金，融资负数计入净资产负债，实际利息、借券费和股息补偿记费用。本系统不是券商授信、购买力或自动强平引擎；强平只录实际成交，不模拟没有发生的交易。

指定批次处置在 FIFO 账户填开仓记录及数量，移动平均不能选择个别批次成本。导出使用 record_reference 重绑批次；更新和删除会重放全历史，不能破坏之后的卖出/交割。已存合约修改通过审计修订入口保存前后条款、确认人和依据，而不是在交易提交时覆盖条款。

未指定批次时，FIFO 按原始 acquisition date 和开仓事实顺序消耗成本。历史开仓导入、内部转仓和拆股保留原取得日期及顺序，不按导入日期或转入新账户的日期重新排队；缺少逐批来源的聚合期初持仓仍只能解释为一个 synthetic opening lot。

合约修订不能替换资产身份或使已记录交割失效。没有明确转换模型的 quanto、行权币种与标的报价币种不同的合约，以及调整后包含非标准篮子的期权，不按普通期权处理。兼并换股、分拆、权利发行、证券空头的跨账户转移及衍生品跨账户转移仍需专用事件语义，不能通过伪造买卖代替。指定开仓记录并非税务 lot-ID 系统；具体可重复情景见 [交易情景测试](../backend/tests/test_transaction_repair_scenarios.py)。

实物行权的 `lot_selections` 选择期权多头批次，数量单位为合约张数；股票腿按证券账户成本法计算。专用结果接口中的 fees、fee_category、taxes 进入股票腿；文件和 AI 草稿将它们放在 option_delivery 内，外层费用税费为零、分类为 unknown。导出保留两腿的共同时间、股票腿费用分类和期权指定批次。AI 复核可补齐交付账户，编辑 FCN 的账户、标的、数量、价值、币种、汇率及批次；不会将缺失事实自动补成一笔已确认成交。

页头的简洁期权结果入口默认使用账户成本法；需要指定多头开仓批次时，使用交易页的实物行权表单。估算时间在 CSV/Excel 导出中留空，回导后仍标记为估算；只有已知实际时间才导出具体时刻。

`transfer_group_id` 只表示同一笔内部转账或转仓生成的 `transfer_out / transfer_in` 双腿，
不用于绑定 Option、FCN、股票交割或其他经济上相关的交易，也不出现在 CSV。期权交割关系只由专用命令写入 `option_delivery_link`。导出时一组双腿
折叠成一行 Security 或 Cash 的 `transfer_out` 动作，`account_id` 为转出账户、
`counterparty_account_id` 为转入账户。手工文件也只填写一个方向；回导时系统重新原子生成双腿。Transfer 可填写 `source_system + external_reference`；系统把这组 command-level 来源身份保存在 `transfer_out` 腿，导出折叠后仍可原样回导并跨批次识别重复。

Cash Fee / Tax 不关联资产，因此不填写 entitlement date；Security、FCN、Option 关联费用可填写 entitlement date。未填时按费用实际发生前的交易顺序校验证券多空头、FCN/Option 多头或 written-option obligation，因此当日先开仓后收费可以记录，开仓前的费用不能借用稍后才形成的持仓；显式历史 entitlement date 仍按该日 BOD 权益校验。Option 的独立 Fee / Tax 是合约级现金费用：进入现金、NAV 与 Performance，但不改写 long lot cost basis 或 writer obligation。Return of Capital 用 `trade_date` 表示 entitlement/record date，用 `settlement_date` 表示实际到账日，不再重复填写 `entitlement_date`。非 margin/financing 账户的负 settled cash 会提示需核对；已明确标记的保证金或融资账户负余额按融资负债显示，不误报成缺失入金。账户用途必须依据实际安排填写，不能仅为消除提示改为融资。

批量导入证券时使用 Registry `instrument_id`；Option / FCN 使用 Portfolio-local
`derivative_contract_id`。新衍生品合约条款写在首次交易行，后续行只保留 contract
ID。建议每行填写稳定的 `source_system + external_reference`，用于识别重复来源事实。
