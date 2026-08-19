# Transaction Operations Contract

本文定义 Portfolio `Transactions` 工作台、API 与派生账本之间的操作边界。计算公式仍以
[01_CALCULATION_SPEC.md](./01_CALCULATION_SPEC.md) 为准；本文只说明如何安全地创建、修改、
删除和核对交易事实。

## 1. 一条记录代表什么

`transaction_record` 保存已经确认的经济事实，不直接保存 Holdings、cash balance、
ledger posting 或 position lot。后四者均由交易事实、账户事实和行情事实确定性派生。

- `account_id` 是交易所属账户；
- 证券交易的现金腿由 `settlement_cash_account_id` 指向结算现金账户；
- 内部转仓必须通过 internal-transfer command 一次生成成对事实，不能单独修改其中一腿；
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
存在的 Portfolio contract。合约条款创建后作为不可变事实，后续交易只引用 contract ID。

## 2. 日期和时间

| 字段 | 业务含义 | 影响 |
| --- | --- | --- |
| `trade_date` | 下单、成交或定价事实日 | 交易顺序、价格与可用份额校验 |
| `trade_time` | 已知的真实成交时刻 | 同日交易排序 |
| `position_effective_date` | 份额进入或离开 EOD 持仓的日期 | position、lot、持仓估值 |
| `settlement_date` | 现金实际结算日期 | settled / pending cash posting |
| `entitlement_date` | 收入权利确认日 | dividend / coupon income |

未知成交时间必须留空。后端会使用配置的默认时点完成确定性排序，同时写入
`trade_time_is_estimated = true`；默认时点不是精确成交事实。开盘前或盘中成交且应进入
当日 EOD 持仓时，`position_effective_date = trade_date`。按当日收盘 NAV 申购、下一交易日
确认份额时，保留真实 `trade_date`，并把确认日写入 `position_effective_date`。

## 3. 金额、份额和价格

所有输入均使用非负 magnitude，方向由所选资产的交易动作决定。

- 基金申购 / 赎回：确认金额与确认份额固定，unit price 由二者和 Registry price-scale
  contract 反算并按 source precision 保存。页面展示的 NAV 仅作参考，不自动写入成交价格。
- ETF / 股票：数量与实际 execution price 为常用锚点，gross amount 由 price-scale contract
  计算；若成交回单直接给出金额，可用金额反算价格。
- FCN：合约进入、利息收入和关闭分别记录；关闭结果可选正常到期、敲入或敲出。
- 期权：数量单位为合约张数，gross amount 使用本地合约 multiplier；Call / Put 买卖和关闭均为独立事实。
- fees、taxes 与 fee category 必须分开保存，不能揉进 gross amount。

## 4. 现金和待确认资产

现金结算与份额确认可以发生在不同日期。系统用 settlement subledger 表示
`pending_subscription`、`settlement_receivable` 或 `settlement_payable`，而不是在证券账户
提前伪造份额，也不是把在途金额当作 settled cash。确认日到达后 bridge 消失并转为真实
position；组合 NAV 在等待期间仍必须对平。

## 5. 创建、修改和删除控制

- create 与 internal transfer 请求必须携带 request-scoped `Idempotency-Key`；同 key 只能重放
  同一 operation 与同一 payload；
- 页面在请求完成前锁定提交按钮，防止双击生成重复事实；
- update 必须携带当前 `expected_row_version`，缺失返回 validation error，版本过期返回 conflict；
- delete 必须携带完整 delete scope 的 row-version map；成对转仓必须整组删除；
- create / update / delete 都追加 change log。Transactions inspector 的 History 标签显示版本、
  时间和变更字段，不能用直接改库替代正常修订流程。

## 6. 操作后核对

每次录入或修订至少核对：

1. activity type、账户、证券与币种；
2. trade、position-effective、settlement、entitlement 日期是否各自表达真实事件；
3. 基金确认金额与份额是否保持原始精度，反算价格是否未被参考 NAV 覆盖；
4. Net Cash Effect、postings 与 position lot 方向是否一致；
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
保留标准 CSV 的 40 个字段，第一行字段名不可修改，默认没有示例数据，因此不会把示例误导入。
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
| 股票、ETF、基金 | 买入、卖出/赎回、分红、红利再投资、返还资本、期初持仓、独立费用/税费、证券转仓；另有股票、ETF、公募基金和私募基金的明确示例 |
| FCN | 新合约进入、多标的条款、期初持仓、提前退出、票息、费用、税费、正常到期、敲入、敲出和交付证券的独立记录 |
| Option | Call/Put、多头/空头开仓和平仓、期初多头持仓、费用、税费、长短仓到期作废/现金结算，以及实物行权或指派后的独立证券记录 |
| 多币种 | USD、HKD、CNY；证券、持仓账户、合约、结算现金账户和交易币种必须满足同币种规则 |

系统目前支持的交易币种为 `USD`、`HKD` 和 `CNY`。普通资产交易的 `currency` 必须与持仓账户、
证券或衍生品合约及结算现金账户一致；证券转仓要求证券与转入、转出账户币种一致，现金内部转移
只能在同币种现金账户间进行。换汇行的 `currency` 是转出现金账户币种，
`counter_amount = gross_amount × fx_rate`，目标币种由 `counterparty_account_id` 对应账户确定。

FCN 在到期前退出选择 `early_exit`。Option 实物行权/指派先选择 Option 的多头或空头现金结算
动作，再按交付日市场或参考价独立记录标的证券买入/卖出。FCN
敲入交付同理，FCN 结束行与证券 `buy` 行是两个独立事实。模板不含 Portfolio 资料，也不提供
关联或配对 ID。`opening_balance` 可用于已有 Option 多头；已有空头应按实际开仓日期、数量和
权利金补录 `sell_to_open`，不能用正数量期初余额代替。

`transfer_group_id` 只表示同一笔内部转账或转仓生成的 `transfer_out / transfer_in` 双腿，
不用于绑定 Option、FCN、股票交割或其他经济上相关的交易，也不出现在 CSV。导出时一组双腿
折叠成一行 Security 或 Cash 的 `transfer_out` 动作，`account_id` 为转出账户、
`counterparty_account_id` 为转入账户；也可按相反视角填写 `transfer_in`。回导时系统重新原子生成双腿。

批量导入证券时使用 Registry `instrument_id`；Option / FCN 使用 Portfolio-local
`derivative_contract_id`。新衍生品合约的不可变条款写在首次交易行，后续行只保留 contract
ID。建议每行填写稳定的 `source_system + external_reference`，用于识别重复来源事实。
