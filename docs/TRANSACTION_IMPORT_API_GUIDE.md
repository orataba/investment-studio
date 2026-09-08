# Portfolio 标准交易记录 JSON 接口对接说明

本文档面向“交易截图转标准化交易记录”系统的开发者。`transaction-imports` 接口只接收已经标准化、准备进入人工确认的交易事实，不接收截图文件、OCR 原文、委托单或数据库行。内部截图助手使用下文单独的证据采集接口；截图不会绕过 Preview/Commit 直接进入账本。

接口的 OpenAPI 定义由当前服务自动生成：

- Swagger UI：`GET /docs`
- OpenAPI JSON：`GET /openapi.json`

本文档与 OpenAPI 是对接依据；Portfolio 数据库表不是外部写入合同。

## 1. 接口边界

上游系统负责：

- 识别截图中的成交、现金收付或衍生品事件；
- 区分已成交、部分成交、未成交、已撤单和待确认状态；
- 完成人工核对，形成一条一条的标准化候选事实；
- 为每条事实提供稳定且不可重复使用的 `external_reference`。

Portfolio 负责：

- 校验 Portfolio、账户、币种、证券和本地衍生品合约；
- 校验交易动作、日期、金额、持仓和合约生命周期；
- 防止批次重放和来源事实重复；
- 原子写入源交易，并刷新由交易派生的账本、持仓和组合快照。

上游不得直接写入 Portfolio 数据库，也不得写入 holdings、lots、ledger postings、performance、risk 或 snapshots。它们都是 Portfolio 根据源交易派生的结果。

## 2. 接口概览

所有路径中的 `{portfolio_id}` 都必须替换为目标组合 ID。

| 用途 | 方法与路径 |
|---|---|
| 查询组合 | `GET /api/portfolios` |
| 查询账户 | `GET /api/portfolios/{portfolio_id}/accounts` |
| 查询可用证券 | `GET /api/portfolios/{portfolio_id}/instruments` |
| 查询已有 FCN/Option 合约 | `GET /api/portfolios/{portfolio_id}/derivative-contracts` |
| 保存截图证据 | `POST /api/portfolios/{portfolio_id}/transaction-captures` |
| 查询截图证据 | `GET /api/portfolios/{portfolio_id}/transaction-captures` |
| 建立多截图分析批次 | `POST /api/portfolios/{portfolio_id}/transaction-capture-batches` |
| 查询分析批次 | `GET /api/portfolios/{portfolio_id}/transaction-capture-batches` |
| 启动受限 Agent 分析 | `POST /api/portfolios/{portfolio_id}/transaction-capture-batches/{batch_id}/analysis-runs` |
| 获取 Agent 任务上下文 | `GET /api/portfolios/{portfolio_id}/transaction-capture-batches/{batch_id}/agent-context` |
| 保存分析修订 | `POST /api/portfolios/{portfolio_id}/transaction-capture-batches/{batch_id}/analysis-revisions` |
| 预览 JSON 批次 | `POST /api/portfolios/{portfolio_id}/transaction-imports/preview` |
| 确认并原子入账 | `POST /api/portfolios/{portfolio_id}/transaction-imports/commit` |
| 查询已入账交易 | `GET /api/portfolios/{portfolio_id}/transactions` |
| 查询到期未确认期权 | `GET /api/portfolios/{portfolio_id}/options/unresolved-actions` |
| 确认期权到期/结算/实物结果 | `POST /api/portfolios/{portfolio_id}/options/outcomes` |
| 查询期权实物交割关系 | `GET /api/portfolios/{portfolio_id}/options/delivery-links` |
| 查询 written-option obligations | `GET /api/portfolios/{portfolio_id}/options/obligations` |

请求使用：

```http
Content-Type: application/json
```

单批最少 1 条、最多 5,000 条。截图识别批次通常应远小于此上限。

应用接口本身不新增第二套身份系统。部署时只能通过项目现有的受控内网或已鉴权反向代理开放，不得把 Portfolio 后端端口直接暴露到公网。

### 2.1 内部截图助手的第一阶段边界

截图助手使用“证据批次 → Agent 分析 → Preview → 人工确认 → Commit”边界：

- 上传 PNG、JPEG 或 WebP，单张不超过 12 MB；
- 原图按 Portfolio 和内容指纹去重保存，可通过 capture image 接口回看；
- 一次分析可包含 1–10 张截图；同一张证据可被不同用途的批次复用；
- Agent 必须把整批截图一起理解，并逐张分类为成交、委托、持仓快照、现金快照、账户摘要、混合或未知；
- Agent 输出字段级证据、重复关系和待确认问题，可选地提出 `transaction_import`，不依赖固定券商模板；
- 持仓或现金快照可以只形成初始化/对账候选，不会被机械地反推成历史交易；
- 模型或人工产生的分析每次保存为一条不可变修订，并记录 harness、模型、会话和证据引用；
- `analysis-runs` 异步返回，批次通过 `analysis_run_status` 暴露 `queued`、`running`、`succeeded` 或 `failed`；同一批次不能同时启动两次；
- 包含 `transaction_import` 的修订会自动运行现有 Portfolio Preview，并保留当时的 digest 和 Preview 结果；
- 批次查询会从正式账本反查来源引用，并通过 `ledger_status` 返回 `no_proposal`、`unrecorded`、`partially_recorded` 或 `recorded`；`recorded_transaction_ids` 返回已经写入的交易 ID，因此刷新页面后仍能阻止同一截图提案被再次入账；
- 保存截图或分析都不会创建交易。正式入账仍只能使用第 3 节的人工确认和 Commit 流程。

模型供应商、提示词和图像解析运行时不进入账本合同。DeepSeek Harness、Codex 或其他 MCP host 只负责读取证据、生成分析、运行 Preview 和保存修订；提供给 Agent 的工具集中没有 Commit。
内部受限 MCP 运行时会在进程启动时绑定当前 `portfolio_id` 和 `batch_id`，模型工具参数不能改选组合或批次；完整 Registry 也不会进入模型上下文，标的只通过紧凑搜索结果返回。

## 3. 标准流程

1. 查询目标 Portfolio 的账户、证券和已有衍生品合约。
2. 把截图识别结果转换为本文档定义的业务命令。
3. 调用 Preview；检查 `error_count == 0`。
4. 向用户展示识别值和 Portfolio 返回的标准化结果，完成最终确认。
5. 将完全相同的 `source_system` 和 `records` 连同 Preview 返回的 `preview_digest` 发送给 Commit。
6. Commit 使用一个新的 `Idempotency-Key`；超时重试时复用同一个键和完全相同的请求体。
7. 保存 Commit 返回的 `transaction_id`，用于后续对账。

Preview 不写数据库。Commit 是全有或全无的原子操作；只要一条记录或整批历史校验失败，整批都不会写入。

Preview 调用示例：

```bash
curl --fail-with-body \
  -X POST "${PORTFOLIO_API_BASE_URL}/api/portfolios/${PORTFOLIO_ID}/transaction-imports/preview" \
  -H 'Content-Type: application/json' \
  --data-binary @transaction_import_api_preview.json
```

Commit 使用相同的 `source_system` 和 `records`，只增加 `preview_digest`，并增加请求头：

```http
Idempotency-Key: screenshot-batch-20260821-001
```

## 4. Preview 请求

```json
{
  "source_system": "trade_screenshot_parser",
  "records": [
    {
      "external_reference": "screenshot-20260821-001#1",
      "asset_type": "security",
      "transaction_action": "buy",
      "trade_date": "2026-08-21",
      "settlement_date": "2026-08-25",
      "account_id": "broker-us-core",
      "settlement_cash_account_id": "cash-usd-main",
      "instrument_id": "equity-us-example",
      "quantity": "100",
      "price": "12.34",
      "gross_amount": "1234.00",
      "fees": "2.50",
      "fee_category": "transaction_cost",
      "taxes": "0",
      "currency": "USD"
    }
  ]
}
```

完整的跨资产示例见 [transaction_import_api_preview.json](examples/transaction_import_api_preview.json)。

### 批次字段

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `source_system` | string | 是 | 上游系统的稳定名称，1–100 字符；不要随版本或机器改变 |
| `records` | array | 是 | 1–5,000 条交易命令，按真实经济发生顺序排列 |

### 每条记录的通用字段

| 字段 | 类型 | 必填 | 含义与规则 |
|---|---|---:|---|
| `external_reference` | string | 是 | 上游事实唯一标识，1–200 字符；与 `source_system` 组合后在一个 Portfolio 内唯一 |
| `asset_type` | enum | 是 | `security`、`fcn`、`option`、`cash` |
| `transaction_action` | enum | 是 | 必须使用第 5 节中该资产允许的动作 |
| `trade_date` | date | 是 | `YYYY-MM-DD`；成交日或经济事实发生日 |
| `trade_time` | string | 否 | `HH:MM`，使用后端配置的交易时区；省略时系统使用默认时间并标记为估算；导出时估算时间留空，不能变成已知成交时间 |
| `settlement_date` | date | 条件 | `YYYY-MM-DD`，不得早于 `trade_date`；省略时等于 `trade_date` |
| `position_effective_date` | date | 条件 | 头寸开始发生变化的日期，不得早于 `trade_date`；仅头寸变化动作使用 |
| `entitlement_date` | date | 条件 | 权益归属日，不得晚于 `trade_date`；仅 dividend、dividend reinvestment、coupon、资产 fee/tax 使用 |
| `acquisition_date` | date | 条件 | 原始开仓日；资产 `opening_balance`、`opening_written`、`short_opening_balance` 使用，且不得晚于 `trade_date` |
| `account_id` | string | 是 | 目标账户 ID，必须属于 URL 中的 Portfolio，类别和币种必须匹配 |
| `counterparty_account_id` | string | 条件 | 内部转账的另一账户，或 FX conversion 的目标现金账户 |
| `settlement_cash_account_id` | string | 条件 | 证券、FCN、Option 的结算现金账户；必须是同币种 Cash 账户 |
| `instrument_id` | string | 条件 | Security 必填；必须来自 Portfolio instruments 接口 |
| `derivative_contract_id` | string | 条件 | FCN/Option 必填；一个 Portfolio 内稳定且唯一 |
| `derivative_contract` | object | 条件 | 仅首次创建 FCN/Option 合约时提供；后续交易必须省略 |
| `record_reference` | string | 否 | 本文件/批次内唯一批次引用，供后续 lot_selections 使用；不是券商业务号 |
| `lot_selections` | array | 否 | FIFO 处置的开仓记录引用及数量；期权实物行权时选择的是期权多头批次，不是股票批次 |
| `asset_deliveries` | array | 条件 | FCN 实物兑付的接收账户、证券、数量、确认价值、币种与合约折算汇率 |
| `settlement_cashflows` | array | 条件 | FCN 关闭绑定的票息、合约费用税费，各自记录现金账户、币种、确认日和结算日 |
| `option_delivery` | object | 条件 | physical_long / physical_written 必填；交付账户及股票腿的费用、费用分类、税费和 allow_stock_short |
| `quantity` | decimal string | 条件 | 证券份额、基金份额、FCN 数量或期权张数；方向由动作决定，值本身不使用负号 |
| `price` | decimal string | 条件 | 每单位成交价；Option 为每份标的单位的权利金，不是整张合约权利金 |
| `gross_amount` | decimal string | 条件 | 未扣费用和税费的成交/现金事实金额；方向由动作决定，值本身不使用负号 |
| `counter_amount` | decimal string | 条件 | FX conversion 目标账户实际收到的金额 |
| `fx_rate` | decimal string | 条件 | `counter_amount / gross_amount` |
| `fees` | decimal string | 否 | 附着于本交易的费用，省略等于 0 |
| `fee_category` | enum | 否 | 见第 7 节；省略为 `unknown` |
| `taxes` | decimal string | 否 | 附着于本交易的税费，省略等于 0 |
| `currency` | enum | 是 | 只允许 `USD`、`HKD`、`CNY`、`EUR`、`GBP`、`CHF`，必须与账户和资产一致 |
| `note` | string | 否 | 人工可读备注；不得依赖备注表达必须参与计算的结构化事实 |

JSON 中金额、价格、数量和比例字段必须发送十进制字符串，不要发送经过二进制浮点运算的结果。例如发送 `"1234.50"`，不要在客户端用浮点数计算后发送 `1234.499999999`。

未知字段会被拒绝。不要发送 OCR 文本、置信度、截图 Base64、订单状态、内部交易类型、数据库 ID、row version、ledger posting 或 holdings 字段。

## 5. 资产和动作

### Security

股票、ETF、公募基金和私募基金都使用 `asset_type = security`，通过 Registry 的 `instrument_id` 区分具体类型。

| `transaction_action` | 含义 |
|---|---|
| `buy` | 买入；公募/私募基金表示申购确认 |
| `sell` | 卖出；公募/私募基金表示赎回确认 |
| `short_sell` | 股票/ETF 实际卖空；先处置已有多头，差额开空 |
| `buy_to_cover` | 股票/ETF 买回已有空头，不超出待买回数量 |
| `short_opening_balance` | inception date 已存在的股票/ETF 空头，正数量及剩余净账面负债，不重复动现金 |
| `dividend` | 现金分红 |
| `dividend_reinvestment` | 分红再投资 |
| `return_of_capital` | 资本返还 |
| `fee` | 与该证券相关的独立费用 |
| `tax` | 与该证券相关的独立税费 |
| `transfer_out` / `transfer_in` | Portfolio 内账户之间转移持仓，只提交一个方向 |
| `opening_balance` | Portfolio inception date 的期初持仓 |

`buy`、`sell` 需要 `instrument_id`、`quantity`、`price`、`gross_amount`。普通证券满足：

```text
gross_amount = quantity × price
```

允许 `price` 使用由精确 `gross_amount / quantity` 四舍五入到四位小数的展示值；`gross_amount` 始终是权威金额。

Security 持仓转账需要 `instrument_id`、`quantity` 和另一 Security 账户的 `counterparty_account_id`。`gross_amount` 可省略并由 Portfolio 按源账户成本推导；若提供，必须与交易日可转移持仓的成本一致。

### FCN

| `transaction_action` | 含义 |
|---|---|
| `entry` | 进入/买入 FCN |
| `early_exit` | 到期前退出/卖出 FCN |
| `coupon` | 票息收入 |
| `knock_in_close` | 敲入结果关闭 |
| `knock_in_observation` | 已确认敲入观察，零金额、不填数量、不关闭合约；note 保存依据 |
| `knock_out_close` | 敲出结果关闭 |
| `maturity_close` | 正常到期关闭 |
| `fee` / `tax` | 合约相关独立费用或税费 |
| `opening_balance` | inception date 的已有 FCN 多头 |

所有 FCN 动作都需要 `derivative_contract_id`。首次 `entry` 或 `opening_balance` 还要提供完整 `derivative_contract`；后续动作只发送 ID。

Portfolio 记录经确认的 FCN 事件，不自动判断障碍是否触发。最终实物兑付在 `knock_in_close` / `maturity_close` 的 `asset_deliveries` 中记录收到的证券；`gross_amount` 仅填实际现金尾差。系统建立证券成本及来源关联，不额外录一条现金买入，也不虚构现金兑付。

### Option

| `transaction_action` | 含义 |
|---|---|
| `buy_to_open` | 买入开仓，多头增加 |
| `sell_to_close` | 卖出平仓，多头减少 |
| `sell_to_open` | 卖出开仓，空头义务增加 |
| `buy_to_close` | 买入平仓，空头义务减少 |
| `expire_long` | 多头到期作废，零现金 |
| `cash_settle_long` | 多头现金结算 |
| `expire_written` | 空头到期作废，零现金 |
| `cash_settle_written` | 空头现金结算 |
| `physical_long` / `physical_written` | 多头实物行权 / 空头实物指派，option_delivery 必填，一行原子生成两腿 |
| `opening_written` | inception date 的已有期权空头，gross_amount 为剩余账面权利金负债，不重复收取现金 |
| `fee` / `tax` | 合约相关独立费用或税费 |
| `opening_balance` | inception date 的已有期权多头 |

所有 Option 动作都需要 `derivative_contract_id`。首次 `buy_to_open`、`sell_to_open`、`opening_balance` 或 `opening_written` 还要提供完整 `derivative_contract`。

权利金交易满足：

```text
gross_amount = contract quantity × price × contract_multiplier
```

`expire_long` 和 `expire_written` 的 `gross_amount` 必须为 `"0"`，不得发送结算现金账户、费用或税费。现金结算需要正数 `gross_amount` 和结算现金账户。

标准 Preview/Commit 使用 `physical_long` / `physical_written` 加 `option_delivery` 表达实物结果，一行原子生成期权与股票两腿；也可使用下面的专用 outcome command。不得提交内部事件名 `option_long_exercise` / `option_writer_assignment`，也不能用“现金结算 + 独立股票买卖”模拟实物交割。

#### 期权结果命令

```http
POST /api/portfolios/{portfolio_id}/options/outcomes
Idempotency-Key: option-outcome-<stable-operation-id>
Content-Type: application/json
```

```json
{
  "derivative_contract_id": "option-abbv-20261218-c220",
  "side": "long",
  "outcome": "physical",
  "quantity": "1",
  "event_date": "2026-12-18",
  "trade_time": "16:00",
  "settlement_date": "2026-12-18",
  "stock_account_id": "security-usd",
  "settlement_cash_account_id": "cash-usd",
  "fees": "1.25",
  "fee_category": "transaction_cost",
  "taxes": "0"
}
```

`outcome` 允许 `expired`、`cash_settled`、`physical`，必须遵循已确认的合约交割条款。到期作废的 `event_date` 必须等于 expiry，不发送金额或费用；现金结算发送实际正数 `cash_settlement_amount`。实物结果的数量和执行价按 multiplier 与 strike 计算：long Call / written Put 买入股票，long Put / written Call 交付股票。期权零现金关闭，费用税费只进股票腿，两腿与 `option_delivery_link` 原子写入。权利金币种可以不同于股票币种；明确的 `strike_currency` 必须等于标的报价币种，股票腿在该币种现金账户结算。没有合约 FX 转换模型时，不接受 strike 与报价币种不同的产品，也不允许用虚构现金交割规避。

交付前实际买入股票后再交割，或券商确认指派先形成股票空头、之后实际买回，都可以记录。后一种在 outcome / `option_delivery` 中显式发送 `allow_stock_short=true`；系统按已有多头数量先处置多头，差额形成空头。后续使用 `buy_to_cover`，不能改写成交时间。该字段不是融资融券许可或券商保证金测算。

CSV/Excel 对应 `physical_long` / `physical_written`，通过 `option_delivery_json` 记录股票账户、现金账户和交付费用，一行生成原子双腿。不要再导入同笔股票交付。标准导出保留这一关系。所有新建交易、内部转仓、期权结果及批量提交都必须携带非空 `Idempotency-Key`；重试复用同 key，修改 payload 使用新 key。

实物结果两腿共用 `trade_time`，先买股后交付按各自已知真实时间记录。专用 outcome 的 `fees`、`fee_category`、`taxes` 进入股票腿；文件/JSON 批次将这些字段放在 `option_delivery` 内，外层费用税费为零、分类为 unknown。`lot_selections` 属于期权多头关闭腿，数量单位是合约张数，留空沿用账户成本法。未知时间仍留空并标记为估算，不允许改造时间来绕过可用仓位。

### FCN 实物兑付、指定批次和账户用途

- FCN `knock_in_close` / `maturity_close` 可带 `asset_deliveries`（CSV/Excel 列 `asset_deliveries_json`）。每腿填写接收账户、Registry 标的、quantity、该腿原币总 fair_value、currency 和 fx_rate_to_contract。汇率含义为一单位证券币种折合多少合约币种；同币种必须为 1。`gross_amount` 只记录实际现金尾差，可为零。合约处置损益 = 确认收股折算价值 + 实际现金 − 合约处置费用 − 所处置合约账面成本；收到证券以本币确认价值加取得费用税费建 lot，不制造本金现金买卖。
- 每腿可填 `delivery_date`（实际到账）、`fees`、`taxes`、`fee_category`、`settlement_cash_account_id` 和 `fee_settlement_date`。非零取得费用要求同证券币种 Cash 账户；费用仅扣现金一次，并资本化到股票成本。经济生效日取母记录 `position_effective_date` 或 `trade_date`；实际到账日、扣费日不能早于经济生效日，扣费日默认经济生效日。未填实际到账日保持未知。`quantity_fx_rate` 为证券币种/合约币种的数量换算条款 FX；`fractional_quantity` 和 `fractional_reference_price` 只保留碎股回单依据，不生成换汇或自动推算现金尾差。
- FCN `knock_in_close` / `maturity_close` / `knock_out_close` 可带 `settlement_cashflows`（文件列 `settlement_cashflows_json`）：每项为 `{kind, cash_account_id, currency, amount, recognition_date?, settlement_date?, fee_category?, note?}`，kind 为 `coupon` / `fee` / `tax`，amount 为正数。票息使用合约币种，费用可使用其他币种；现金账户须匹配币种。确认日默认经济生效日，结算日默认确认日且不能更早。必须提供母记录 note 作为结算依据，已录独立票息不得重复添加。所有明细随母记录原子写入、修改及删除。
- `GET /api/portfolios/{portfolio_id}/positions/{position_reference_id}/fcn-lifecycles?as_of_date=YYYY-MM-DD` 从 FCN 合约或接票证券查看来源收益及交付状态；缺少必要行情/FX 或来源不完整时收益为 null，不能解释为零。
- `short_sell` / `buy_to_cover` / `short_opening_balance` 只支持股票及 ETF。期初空头填正数量和剩余净账面负债，不重复收取现金。
- FIFO 账户卖出、买回、兑付可传 `lot_selections=[{opening_transaction_id, quantity}]`，数量之和必须等于处置量；缺少可用批次拒绝整笔。移动平均账户不能选择单批成本。文件列 `lot_selections_json` 可引用本文件唯一 `record_reference`，导入和组合复制会重绑到新记录，不沿用旧组合 ID。
- Cash 账户 `cash_purpose` 为 operating、margin、collateral 或 financing。融资借还、抵押释放用内部现金划转；抵押账户可保存 `collateral_reference` 确认依据。借款本金不是收益或外部入金。实际利息、借券费和空头股息补偿分别用 fee_category financing_interest、borrow_fee、payment_in_lieu。
- 已有合约补充条款使用 `PATCH /api/portfolios/{portfolio_id}/derivative-contracts/{contract_id}`，提交 expected_row_version、完整 terms、reason 和 reviewed_by。保留前后版本并重放历史；不能把合约身份替换为另一个产品，不能使已存在的交割事实与条款冲突。

同一个 Idempotency-Key 只能重放完全相同的 outcome payload。`GET .../options/unresolved-actions` 返回已过期但仍有 open long/written quantity 的事项；`delivery-links` 可按 `underlying_instrument_id` 过滤，`obligations` 可按 `as_of_date`、`derivative_contract_id` 或 `underlying_instrument_id` 过滤。

### Cash

| `transaction_action` | 含义 |
|---|---|
| `deposit` | 外部资金流入 |
| `withdrawal` | 外部资金流出 |
| `interest` | 现金利息收入 |
| `fx_conversion` | 两个不同币种 Cash 账户之间换汇 |
| `fee` / `tax` | 现金账户独立费用或税费 |
| `transfer_out` / `transfer_in` | 同币种 Cash 账户内部转账，只提交一个方向 |
| `opening_balance` | inception date 的期初现金 |

Cash 动作不得发送 `instrument_id`、`derivative_contract_id` 或 `settlement_cash_account_id`。

## 6. 衍生品合约对象

衍生品合约属于一个 Portfolio 和一个 FCN/Option 账户。后续交易只引用原合约 ID，不能通过交易请求覆盖条款。存量条款补充或纠错使用第 5 节的审计修订接口，保留前后版本、确认人和依据；不得替换产品身份或使已记录交割与条款冲突。

### Option 合约

```json
{
  "derivative_contract_id": "option-abbv-20261218-c220",
  "contract_name": "ABBV Dec 220 Call",
  "contract_type": "option",
  "external_reference": "broker-contract-001",
  "terms": {
    "underlying_instrument_id": "equity-us-abbv",
    "option_type": "call",
    "expiry_date": "2026-12-18",
    "strike": "220",
    "contract_multiplier": "100"
  }
}
```

`option_type` 只允许 `call` 或 `put`。`strike` 和 `contract_multiplier` 必须大于 0。`underlying_instrument_id` 必须是 Registry Security。经确认的 `settlement_type`、`exercise_style`、`strike_currency`、行权日期及 `terms_reference` 放在 terms 中；已知条款参与结果校验，未知不猜测。

### FCN 合约

```json
{
  "derivative_contract_id": "fcn-2026-001",
  "contract_name": "Demo Bank ABBV FCN",
  "contract_type": "fcn",
  "external_reference": "broker-contract-002",
  "terms": {
    "notional": "100000",
    "annual_coupon_rate_pct": "12",
    "issue_date": "2026-05-01",
    "final_observation_date": "2026-08-28",
    "maturity_date": "2026-09-01",
    "issuer": "Demo Bank",
    "counterparty": "Demo Broker",
    "underlyings": [
      {
        "instrument_id": "equity-us-abbv",
        "initial_reference_price": "200",
        "strike_level_pct": "100",
        "knock_in_level_pct": "70",
        "knock_out_level_pct": null,
        "deliverable": true
      }
    ]
  }
}
```

规则：

- `notional`、`issue_date`、`maturity_date`、`issuer`、`counterparty` 和至少一个 underlying 必填；
- `maturity_date` 不得早于 `issue_date`；
- `final_observation_date` 若存在，必须位于 issue 和 maturity 之间；
- 一个合约的 `underlyings` 不得重复 `instrument_id`；
- 百分比字段以百分数表示，`70` 表示 70%，不是 0.70。

## 7. 金额、费用和币种

- 所有输入金额均为非负数，现金方向由 `transaction_action` 决定；不得用负数表达卖出、提款、费用或空头。
- `gross_amount` 不含本条交易的 `fees` 和 `taxes`，不能把券商显示的净扣款/净到账直接填入 `gross_amount`。
- `fee` 或 `tax` 独立动作使用 `gross_amount` 表示该笔费用或税款，不能同时再填写嵌套 `fees` 或 `taxes`。
- `fee_category` 允许：`unknown`、`transaction_cost`、`management_fee`、`custody_fee`、`administration_fee`、`performance_fee`、`financing_interest`、`borrow_fee`、`payment_in_lieu`、`other`。无法从截图证明分类时使用 `unknown`，不要猜测。
- 普通交易的持仓账户、证券或衍生品合约、结算现金账户及交易币种一致。期权权利金币种可不同于股票币种，但实物股票腿必须使用明确 strike_currency 对应的股票报价及账户币种；FCN 跨币种交付腿使用显式 fx_rate_to_contract，不从行情猜测转换条款。
- FX conversion 中，`currency` 是源现金账户币种；`counterparty_account_id` 指向目标现金账户，且 `counter_amount = gross_amount × fx_rate`。

## 8. 日期和期初边界

- 任何交易都不得早于 Portfolio `inception_date`。
- 所有 `opening_balance`、`opening_written`、`short_opening_balance` 的 `trade_date` 和 `settlement_date` 必须等于 `inception_date`。
- 运行期间新增现金使用 `deposit`，新增持仓使用真实 `buy` 或 `transfer`，不得用 opening balance 绕过外部现金流。
- Security 头寸一般从 `position_effective_date` 生效；现金从 `settlement_date` 生效。两者不是同一字段。
- Dividend、coupon 等收益可使用 `entitlement_date`；不得为了改变 Performance 归属而改写 trade 或 settlement date。
- FCN 交易和事件必须落在合约 issue date 至 maturity date 的有效窗口内；到期关闭不得早于 maturity date。

## 9. 来源身份、幂等和重试

每条交易通过以下组合去重：

```text
portfolio_id + source_system + external_reference
```

推荐的 `external_reference` 优先级：

1. 券商成交编号或流水编号；
2. 上游稳定的源文档 ID + 原始明细 ID；
3. 上游稳定的源文档 ID + 固定记录序号。

同一截图重新识别、用户重新打开确认页或网络超时后，不能生成新的来源身份。

Commit 还要求 HTTP 请求头：

```http
Idempotency-Key: screenshot-batch-20260821-001
```

规则：

- 每个逻辑批次生成一个新键；
- 同一 Commit 超时重试时，复用原键和完全相同的请求体；
- 不得用同一个键提交不同内容；
- 不得通过更换 Idempotency-Key 绕过重复 `source_system + external_reference` 校验。

## 10. Preview 和 Commit

### Preview 响应

以下只展示外层结构，`command` 和 `transaction` 的内部字段在示例中省略；实际响应会返回完整对象。

```json
{
  "portfolio_id": "investment-studio",
  "preview_digest": "64位十六进制摘要",
  "row_count": 1,
  "valid_count": 1,
  "error_count": 0,
  "warnings": [],
  "batch_errors": [],
  "rows": [
    {
      "record_index": 1,
      "external_reference": "screenshot-20260821-001#1",
      "command": {},
      "transaction": {},
      "internal_transfer": null,
      "errors": []
    }
  ]
}
```

- `rows[].command` 是接收到的业务命令；
- `rows[].transaction` 是 Portfolio 映射后的标准交易；内部转账时改为 `internal_transfer`；
- `rows[].errors` 是单条错误；
- `batch_errors` 是重复来源身份、合约冲突、整批持仓历史等错误；
- `valid_count == row_count` 仍不代表可提交，必须同时满足 `error_count == 0`；
- `warnings` 不阻止提交，但上游仍应展示给审核者。

### Commit 请求

Commit 请求体必须是原 Preview 请求体增加 `preview_digest`：

```json
{
  "source_system": "trade_screenshot_parser",
  "records": [
    {
      "external_reference": "screenshot-20260821-001#1",
      "asset_type": "cash",
      "transaction_action": "deposit",
      "trade_date": "2026-08-21",
      "account_id": "cash-usd-main",
      "gross_amount": "1000.00",
      "currency": "USD"
    }
  ],
  "preview_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

示例中的 `preview_digest` 必须替换成 Preview 实际返回值。任何记录、顺序或批次字段发生改变，都必须重新 Preview。

普通记录一条命令创建一条交易。一个 Cash/Security 内部转账命令会原子创建 transfer-out 和 transfer-in 两条账本事实，因此 Commit 的 `created_count` 可能大于 Preview 的 `row_count`。

## 11. HTTP 状态和处理方式

| 状态 | 含义 | 上游处理 |
|---|---|---|
| `200` | Preview 完成，或 Commit 成功/幂等重放成功 | Preview 仍必须检查 `error_count` |
| `404` | Portfolio 不存在 | 停止并检查 `portfolio_id` |
| `409` | Preview 后内容变化、幂等键冲突或来源事实并发重复 | 不要换键盲重试；重新读取状态并核对 |
| `422` | JSON 结构错误，或 Commit 时业务校验未通过 | 按字段路径、逐条错误或批次错误修正后重新 Preview |
| `5xx` | 服务或依赖异常 | 保留原请求；Commit 重试必须复用原 Idempotency-Key |

FastAPI 的请求结构错误会返回标准 `detail[]`，其中 `loc` 指向类似 `body.records.0.trade_date` 的字段路径。Portfolio 业务错误位于 Preview 的 `rows[].errors` 或 `batch_errors`。

## 12. 截图 Agent 必须执行的检查

调用 Preview 前，上游至少要确认：

- 已经读取同一批次的全部截图，而不是逐图独立转换；
- 重叠区域和重复行已建立证据关系；不能确定是否重复时保留候选并要求人工确认；
- 截图表示已成交或已经发生的现金/生命周期事实，不是委托、撤单、未成交或预测；
- 持仓、现金或账户快照没有被当作可以证明成交过程的交易流水；
- 部分成交按券商真实成交明细分别记录，不把未成交数量计入；
- 买卖方向、期权开平仓方向和 Call/Put 已确认；
- 数量、单价、成交总额、费用、税费和净扣款没有混用；
- 交易日、头寸生效日、结算日和权益归属日没有互相替代；
- 证券已解析为唯一 `instrument_id`，不能只发送 ticker 或名称；
- 账户已解析为唯一 `account_id`，不能只发送券商名称；
- 新 FCN/Option 的条款完整；已有合约只引用原 `derivative_contract_id`；
- 期权实物结果没有拆成普通导入记录，而是使用同一组合的专用 outcome command；
- 每条 `external_reference` 稳定且在批次内不重复；
- 人工审核未通过的记录没有进入 Commit 请求。

原始截图、可见文字、框选坐标、字段状态和重复判断保存在截图证据链中。正式 `transaction-imports` 合同仍只接收审核后的结构化业务事实，识别元数据不会进入账本事实。

## 13. 上线前联调验收

建议双方共同完成以下最小验收集：

- Cash deposit Preview、Commit 和同键重试；
- 股票、ETF、公募基金、私募基金各一笔买入/卖出；
- 一笔带费用和税费的交易，核对 gross 与 net cash effect；
- Option 多头开平仓、空头开平仓、零现金到期、现金结算，以及四种 Call/Put × long/written 实物方向的成对落账与幂等重试；
- FCN entry、coupon、正常/敲入/敲出关闭；
- Cash 内部转账和 Security 持仓转账各一笔，只提交一个方向；
- 相同来源事实重复提交，确认被拒绝；
- Preview 后修改任一字段，确认旧 digest 不能 Commit；
- 一条错误记录混入批次，确认整批不落账；
- Commit 成功后，通过 Transactions、Holdings、Performance 和 Ledger 核对派生结果。

联调通过后，上游仍应先保持人工确认，不应基于未经验证的 OCR 置信度阈值直接自动入账。
