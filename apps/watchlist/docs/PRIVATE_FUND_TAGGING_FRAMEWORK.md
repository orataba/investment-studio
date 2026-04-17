# 私募基金标签体系方案

状态：Proposal  
日期：2026-04-16  
适用范围：`Watchlist` 中的私募基金产品标签、筛选、对比、研究跟踪

## 1. 这份方案解决什么问题

目标不是给私募基金硬贴一个“基金类型”，而是让 `Watchlist` 能稳定回答下面几类投资问题：

1. 这只产品本质上是什么策略。
2. 它主要靠什么赚钱。
3. 它的风险和回撤控制是什么风格。
4. 它在哪类市场环境下更有优势，在哪类环境下更脆弱。
5. 它最近是否稳定、是否值得持续跟踪。

如果标签体系不能支持上面这 5 个问题，它对后续研究、筛选和投资都不够有用。

## 2. 第一性原则

设计这个体系时，最重要的是区分三类信息：

1. `结构性身份`
   变化慢，决定“这是什么产品”。
   例如：股票市场中性、量化 CTA、期货套利、多策略。
2. `行为特征`
   可以随时间变化，决定“这个产品现在像什么”。
   例如：低波 / 中波 / 高波、回撤控制强弱、和权益相关性高低。
3. `稳定性与可跟踪性`
   站在产品池角度，决定“这个产品是否容易持续理解、持续跟踪”。
   例如：风格稳定、披露清晰、团队变化小。

因此不建议只做一个平铺的标签列表。更好的做法是做成 `4+1` 维度：

1. 策略身份
2. 收益来源
3. 风险行为
4. 市场适配
5. 稳定性与可跟踪性

## 3. 推荐主维度

| 主维度 | 核心问题 | 标签性质 | 更新频率 |
| --- | --- | --- | --- |
| 策略身份 | 这是什么产品 | 结构性 | 半年 / 有重大变化时 |
| 收益来源 | 它靠什么赚钱 | 半结构性 | 季度 |
| 风险行为 | 它怎么亏钱、怎么控回撤 | 观察性 | 月度 / 季度 |
| 市场适配 | 什么环境下更占优 | 观察性 | 季度 |
| 稳定性与可跟踪性 | 风格是否稳定、是否容易持续跟踪 | 研究跟踪性 | 季度 |

## 4. 推荐字段设计

不建议一上来做太多字段。建议分两层：

- `MVP`
  先做 8-10 个最关键字段，支持 watchlist 筛选和比较。
- `V2`
  再补风格漂移、团队稳定性、容量等监控字段。

### 4.1 MVP 字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `strategy_family` | `single_select` | 一级策略归类，只允许一个主类 | `股票对冲`、`CTA 趋势`、`相对价值套利`、`宏观/事件驱动`、`多策略` |
| `strategy_subtype` | `multi_select` | 二级策略，可保留 1-3 个 | `市场中性`、`股票多空`、`量化选股`、`商品 CTA`、`金融 CTA`、`跨期套利`、`跨品种套利`、`基差套利`、`期权套利`、`混合策略` |
| `implementation_style` | `single_select` | 实现方式 | `主观`、`量化`、`主观+量化` |
| `trading_universe` | `multi_select` | 主要交易对象 | `股票`、`股指期货`、`商品期货`、`国债期货`、`期权`、`多资产` |
| `alpha_source` | `multi_select` | 主要收益来源 | `选股 alpha`、`趋势跟踪`、`期限结构`、`价差收敛`、`波动率溢价`、`事件驱动` |
| `volatility_bucket` | `single_select` | 观察性波动分层 | `低波`、`中波`、`高波` |
| `drawdown_control` | `single_select` | 回撤管理风格 | `强`、`中`、`弱` |
| `preferred_regime` | `multi_select` | 更有优势的市场环境 | `低波震荡`、`高波趋势`、`单边上涨`、`风险偏好下降`、`商品趋势` |
| `weak_regime` | `multi_select` | 更脆弱的市场环境 | `高波反转`、`低波无趋势`、`流动性冲击`、`单边逼空` |
| `style_stability` | `single_select` | 风格稳定度 | `稳定`、`轻微漂移`、`明显漂移` |

### 4.2 V2 监控字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `team_stability` | `single_select` | 核心团队稳定度 | `高`、`中`、`低` |
| `transparency_quality` | `single_select` | 披露与沟通质量 | `高`、`中`、`低` |
| `capacity_bucket` | `single_select` | 容量约束 | `容量充足`、`接近容量`、`容量受限` |
| `research_conviction` | `single_select` | 研究认可度 | `高`、`中`、`低` |

## 5. 标签口径建议

### 5.1 必须区分“身份标签”和“观察标签”

例如：

- `市场中性`
  属于身份标签。
- `低波`
  属于观察标签。
- `回撤控制强`
  属于观察标签。

这三类不能混在一个字段里，否则后面无法判断产品到底是“策略变了”，还是“市场环境变了”。

### 5.2 单选字段必须收敛，多选字段必须克制

建议：

- `strategy_family`
  只能有 1 个值。
- `strategy_subtype`
  最多 3 个值。
- `preferred_regime` / `weak_regime`
  最多各 2 个值。

否则很快会退化成“什么都沾一点”，标签失去筛选价值。

### 5.3 行为字段尽量绑定客观口径

建议先用简化口径，不追求一开始就完全定量化，但要可复核：

- `volatility_bucket`
  以近 36 个月年化波动为主，产品历史不足时参考近 12 个月。
- `drawdown_control`
  以近 36 个月最大回撤、回撤恢复速度、净值修复质量综合判断。
- `preferred_regime`
  不只看收益高低，还要看该环境下收益实现是否稳定。

第一版可以人工判定，但必须写在研究笔记里说明依据。

### 5.4 风格漂移要单独监控

不要把风格漂移直接改写成“策略类型变了”。

更好的做法是：

- 保留原始 `strategy_family`
- 单独维护 `style_stability`
- 在 `strategy` / `research` manual profile 里补充变化原因

这样后续才能区分：

- 产品本来就是多策略
- 产品开始偏离原有框架
- 管理人主动做了策略升级

## 6. 你的 4 个示例产品如何打标签

### 示例 1

描述：低波、市场中性、股票类、风格稳、回撤控制好。

建议标签：

- `strategy_family`: `股票对冲`
- `strategy_subtype`: `市场中性`
- `implementation_style`: `量化` 或 `主观+量化`
- `trading_universe`: `股票`
- `alpha_source`: `选股 alpha`
- `volatility_bucket`: `低波`
- `drawdown_control`: `强`
- `preferred_regime`: `低波震荡`
- `style_stability`: `稳定`

### 示例 2

描述：高波、量化 CTA、回撤控制好、能抓趋势行情。

建议标签：

- `strategy_family`: `CTA 趋势`
- `strategy_subtype`: `商品 CTA` 或 `金融 CTA`
- `implementation_style`: `量化`
- `trading_universe`: `商品期货`、`国债期货` 或 `多资产`
- `alpha_source`: `趋势跟踪`
- `volatility_bucket`: `高波`
- `drawdown_control`: `强`
- `preferred_regime`: `高波趋势`
- `style_stability`: `稳定`

### 示例 3

描述：中波、套利策略、期货市场全品种套利、波动和回撤都小、长期稳定、和其他资产相关性低。

建议标签：

- `strategy_family`: `相对价值套利`
- `strategy_subtype`: `跨期套利`、`跨品种套利`
- `implementation_style`: `量化` 或 `主观+量化`
- `trading_universe`: `商品期货`、`国债期货`
- `alpha_source`: `期限结构`、`价差收敛`
- `volatility_bucket`: `中波`
- `drawdown_control`: `强`
- `preferred_regime`: `低波震荡`
- `style_stability`: `稳定`

### 示例 4

描述：低波、多策略、CTA 混合股票、低波市场能抓趋势，高波市场控制不住回撤。

建议标签：

- `strategy_family`: `多策略`
- `strategy_subtype`: `商品 CTA`、`混合策略`
- `implementation_style`: `主观+量化`
- `trading_universe`: `股票`、`商品期货`
- `alpha_source`: `趋势跟踪`、`选股 alpha`
- `volatility_bucket`: `低波`
- `drawdown_control`: `中` 或 `弱`
- `preferred_regime`: `低波震荡`
- `weak_regime`: `高波反转`、`流动性冲击`
- `style_stability`: `轻微漂移` 或 `明显漂移`

## 7. 在当前 Watchlist 里的落地方式

当前工程已经有合适的承载结构：

1. `instrument_attributes`
   存标签定义和值。
2. `field_registry`
   自动把标签变成 watchlist 列、筛选项、分组选项。
3. `watchlist row read model`
   存当前资产的标签快照，供 screener 和列表页直接消费。

因此私募基金标签体系不需要另建一套表，可以直接复用现有链路。

## 8. 建议推进顺序

建议按下面顺序推进：

1. 先确定 `MVP 10 个字段` 的取值集合，避免一开始就无限扩展。
2. 先给现有重点覆盖的 20-30 只私募基金回填标签。
3. 建 3 个默认 watchlist view：
   - `Strategy Map`
   - `Behavior Map`
   - `Regime Fit`
4. 等第一轮使用后，再补 `style_stability / team_stability / capacity_bucket`。

## 9. 当前产品池场景下的最小结论

如果你现在只想先定一个够用的方案，我建议就采用下面这套最小框架：

1. 主维度用 `5 类`：
   策略身份、收益来源、风险行为、市场适配、稳定性与可跟踪性。
2. 第一阶段只落 `10 个字段`：
   `strategy_family`、`strategy_subtype`、`implementation_style`、`trading_universe`、`alpha_source`、`volatility_bucket`、`drawdown_control`、`preferred_regime`、`weak_regime`、`style_stability`。
3. 其中只有 `strategy_family` 必须单选，其余只在必要时多选。
4. `低波/中波/高波`、`回撤控制强弱`、`市场适配` 一律和时间窗口绑定，避免模糊描述。
5. `风格漂移` 不改写主策略，单独作为稳定性标签跟踪。

这样设计，既能回答“它是什么”，也能回答“什么市场里更值得优先看它”以及“它最近有没有变味”。
