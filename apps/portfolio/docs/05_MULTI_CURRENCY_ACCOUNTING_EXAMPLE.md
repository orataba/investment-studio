# Multi-currency Accounting Example

本文用一条可复算的 USD 资产生命周期说明 CNY 基准组合的历史汇率成本、头寸 realized FX、
pending settlement FX 与 cash FX 如何连续衔接。公式权威口径仍见
[01_CALCULATION_SPEC.md](./01_CALCULATION_SPEC.md)。

## 事实

组合基准币为 CNY，现金账户和证券均为 USD；忽略费用与税费。

| 日期 | 事实 | USD/CNY |
| --- | --- | ---: |
| 01-01 | 期初现金 USD 1,000 | 7.0 |
| 01-02 | 买入 100 股，支付 USD 500，01-04 结算 | 7.2 |
| 01-05 | 卖出 40 股，净应收 USD 240，01-07 结算 | 7.7 |
| 01-06 | 确认分红 USD 10，01-09 结算 | 7.9 |
| 01-08 | 估值 | 8.0 |
| 01-10 | 估值 | 8.1 |

## 头寸 realized P&L

买入 lot 的本币成本为 USD 500，historical base cost 为 `500 × 7.2 = CNY 3,600`。
卖出 40% 时释放：

- 本币成本：`500 × 40% = USD 200`；
- historical base cost：`3,600 × 40% = CNY 1,440`；
- price P&L：`(240 - 200) × 7.7 = CNY 308`；
- position FX P&L：`200 × 7.7 - 1,440 = CNY 100`；
- realized position P&L：`308 + 100 = CNY 408`。

这 CNY 100 截止在 01-05 的头寸处置边界。01-05 以后 USD 240 应收款的汇率变化属于
pending / cash，不得再次进入 position realized FX。

## Pending 到 cash 的 basis 连续性

01-06 时，卖出应收的 historical monetary basis 为 `240 × 7.7 = CNY 1,848`，按当日汇率
估值为 `240 × 7.9 = CNY 1,896`，所以 pending unrealized FX 为 CNY 48。

应收在 01-07 结算后进入现金账户，仍携带 CNY 1,848 basis，不能按 01-07 现汇重置。
分红同理，从 entitlement date 建立 `10 × 7.9 = CNY 79` basis，并在结算时原样转入现金。

01-10 的 settled cash 为 USD 750，historical basis 为 CNY 5,427，当前价值为
`750 × 8.1 = CNY 6,075`，因此现金未实现汇兑损益为 `6,075 - 5,427 = CNY 648`。

## 页面之间如何对应

- Holdings：展示剩余证券的 price / FX / total unrealized，以及 cash 和 pending 的 FX cost basis、未实现 FX；
- Accounts：按现金账户展示 settled、pending 和二者合计的 monetary unrealized FX；
- Transactions：卖出/赎回展示 realized position P&L，并拆成 price 与 position FX；
- Performance：按每天真实敞口路径展示 `Total FX Attribution`，用于区间 P&L 对账；它不是期末 realized / unrealized 分类。
