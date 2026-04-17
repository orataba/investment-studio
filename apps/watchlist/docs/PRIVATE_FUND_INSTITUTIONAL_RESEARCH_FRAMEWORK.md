# 私募基金机构版研究框架

状态：Proposal  
日期：2026-04-16  
适用范围：面向中国私募证券投资基金的 `Watchlist / Research / Investment Committee / Monitoring`

## 1. 这版和上一版最大的区别

上一版更像“标签体系”。

这一版引入顶级 FOF、主权基金、机构尽调的常见方法后，核心变化只有一条：

不要把所有信息都塞进标签。

机构投资里通常分成三层：

1. `Discovery`
   先回答“这只产品是什么、适合干什么”，用于快速筛选和对比。
2. `Underwriting`
   再回答“这只产品能不能投”，用于尽调、立项、投决。
3. `Monitoring`
   最后回答“投完之后有没有变味、有没有出事”，用于持续跟踪。

如果把这三层混在一起，watchlist 会很快失真：

- 标签太多，筛不动；
- 研究结论和事实标签混在一起；
- 风格漂移、团队变化、合规异常没有独立位置。

## 2. 当前 Watchlist 边界下的总框架

完整机构研究当然会包含配置、尽调、投决和投后。

但按你现在给的边界，`Watchlist` 只负责“产品池”。

所以当前系统不应承担：

- 组合搭配
- 投决流转
- 完整 ODD
- 投后管理执行

当前更合适的做法是：

`5 个主标签维度 + 1 个轻量监控层`

### 5 个主标签维度

1. `Strategy Identity`
   这只产品到底是什么策略。
2. `Alpha Engine`
   它到底靠什么赚钱。
3. `Risk Behavior`
   它在哪类环境里赚钱、亏钱、失控。
4. `Regime Fit`
   它更适合哪类市场环境，不适合哪类环境。
5. `Researchability`
   这个产品是否稳定、透明、容易持续跟踪。

### Monitoring Layer

单独追踪风格漂移、团队变化、合规与披露异常。

## 3. 为什么这样更接近顶级机构做法

### 3.1 顶级机构的“角色优先”思路，对当前产品池应翻译成“场景优先”

很多大机构分配对冲基金时，不是先问“这是 CTA 还是中性”，而是先问：

- 它在总组合里承担什么角色？
- 是防守？
- 是分散？
- 是增强？
- 还是替代权益？

但对当前 `Watchlist` 来说，不应该直接把“组合角色”写成系统主标签。

更合适的翻译方式是：

- 它在什么市场环境下值得优先看？
- 它更像哪一类候选产品？
- 它的风险行为与稳定性如何？

也就是把 `role-first` 转写成 `selection-scenario-first`。

### 3.2 机构尽调不是只看业绩，而是看“前瞻 alpha + 组织能力 + 风控/运营”

顶级机构通常不会把历史收益率本身当成主要选择依据，而更看：

- forward-looking excess return 的来源是否可信；
- 团队是不是有真实的研究优势；
- 组织、合规、运营是否足够稳；
- 经理人的风格和 mandate 是否清晰可验证。

### 3.3 中国私募基金必须加一层本土监管适配

中国私募证券基金从 `2024-08-01` 起适用新版《私募证券投资基金运作指引》，而新的《私募投资基金信息披露监督管理办法》自 `2026-09-01` 起施行。

这意味着机构框架里不能只有策略和表现，还必须单列：

- 产品类型与策略是否匹配；
- 杠杆、衍生品、流动性安排是否匹配；
- 量化产品的系统与留痕能力是否过关；
- 信息披露、托管、透明度是否达标。

## 4. 三层结构怎么落到项目里

### 4.1 Discovery：放进 Watchlist 标签

这一层服务“快速找、快速筛、快速比”。

建议放入 `instrument_attributes`，直接给 watchlist / screener 用。

### 4.2 Underwriting：保留为后续研究层，不进入当前 Watchlist 主标签

这一层服务“能不能投”。

不建议和 discovery 标签混在一起。更适合放在：

- 当前 `research` manual profile
- 或后续新增 `odd / underwriting` profile

### 4.3 Monitoring：放进轻量监控字段或研究状态

这一层服务“投后/持续观察”。

适合做成：

- 监控 flag
- 最近一次 review date
- 漂移 / 异常事件记录

## 5. Discovery 层：推荐 5 个主标签维度

这 5 类才是你在产品池中真正需要的主标签。

## 5.1 Strategy Identity

这层回答“这到底是什么产品”，是产品池里的第一层过滤。

### 核心字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `strategy_family` | `single_select` | 一级策略 | `股票对冲`、`CTA 趋势`、`相对价值套利`、`宏观/事件驱动`、`多策略` |
| `strategy_subtype` | `multi_select` | 二级策略 | `市场中性`、`股票多空`、`量化选股`、`商品 CTA`、`金融 CTA`、`跨期套利`、`跨品种套利`、`基差套利`、`期权套利`、`复合多策略` |
| `implementation_style` | `single_select` | 实现方式 | `主观`、`量化`、`主观+量化` |

### 为什么重要

这一步决定你先把谁放到同一个比较池里。

例如：

- 市场中性产品优先和市场中性产品比；
- CTA 产品优先和 CTA 产品比；
- 套利产品优先和套利产品比。

对当前产品池来说，先做清晰策略分桶，比过早上升到组合角色更实用。

## 5.2 Alpha Engine

回答“它靠什么赚钱”。

### 核心字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `trading_universe` | `multi_select` | 交易对象 | `股票`、`股指期货`、`商品期货`、`国债期货`、`期权`、`多资产` |
| `alpha_source` | `multi_select` | alpha 来源 | `选股 alpha`、`趋势跟踪`、`价差收敛`、`期限结构`、`波动率溢价`、`事件驱动` |

### 关键原则

- `strategy_family` 只能单选。
- `strategy_subtype` 最多 3 个。
- `alpha_source` 最多 2 个。

否则会退化成“什么都做一点”的空标签。

## 5.3 Risk Behavior

这一层回答“它的风险收益行为像什么”。

它比只看年化收益、夏普更接近真实配置决策。

### 核心字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `volatility_bucket` | `single_select` | 波动层级 | `低波`、`中波`、`高波` |
| `drawdown_control` | `single_select` | 回撤控制能力 | `强`、`中`、`弱` |
| `equity_correlation_bucket` | `single_select` | 对权益相关性 | `低相关`、`中相关`、`高相关` |
| `preferred_regime` | `multi_select` | 占优环境 | `低波震荡`、`高波趋势`、`单边上涨`、`风险偏好下降`、`商品趋势` |
| `weak_regime` | `multi_select` | 脆弱环境 | `高波反转`、`低波无趋势`、`流动性冲击`、`单边逼空` |

### 关键原则

这层必须绑定观察窗口。

建议默认口径：

- 波动：近 `36` 个月为主，不足时看近 `12` 个月；
- 回撤控制：看最大回撤、修复速度、二次下探；
- 相关性：看对权益主风险因子的实际暴露；
- 表现：看风险收益实现是否稳定，而不是偶然赚过钱。

## 5.4 Regime Fit

这一层回答“什么环境优先看它，什么环境先排除它”。

### 核心字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `preferred_regime` | `multi_select` | 占优环境 | `低波震荡`、`高波趋势`、`单边上涨`、`风险偏好下降`、`商品趋势` |
| `weak_regime` | `multi_select` | 脆弱环境 | `高波反转`、`低波无趋势`、`流动性冲击`、`单边逼空` |

### 为什么重要

你现在要解决的问题，本质上是：

- 当市场处在某种状态时，先把哪一批产品挑出来；
- 当市场切换时，哪一批产品不该优先看；
- 同一策略内部，谁的环境适配更稳定。

这正是产品池系统最该服务的事情。

## 5.5 Researchability

这一层不是完整 ODD，而是“这个产品是否值得持续覆盖、是否容易持续跟踪”。

### 核心字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `style_stability` | `single_select` | 风格稳定性 | `稳定`、`轻微漂移`、`明显漂移` |
| `transparency_quality` | `single_select` | 透明度 | `高`、`中`、`低` |
| `team_stability` | `single_select` | 团队稳定性 | `高`、`中`、`低` |
| `operational_maturity` | `single_select` | 运营成熟度 | `高`、`中`、`低` |

### 为什么 Discovery 里还要留这一层

因为产品池不是基金百科，也不是简单净值库。

你最终要的是“值得继续跟踪和进一步研究的候选池”。

很多产品不是因为策略不行，而是因为：

- 风格经常变；
- 团队不稳定；
- 信息不透明；
- 运营成熟度不够。

这些信号即使不进入投决，也应该在产品池里提前显性化。

## 6. Underwriting 层：保留为后续研究能力，不进入当前 Watchlist 主字段

这一层仍然重要，但属于后续研究体系，不应混入当前 watchlist 主标签。

如果以后扩展，再拆成：

1. `compliance_gate`
2. `structure_gate`
3. `operations_gate`
4. `transparency_gate`
5. `team_gate`
6. `capacity_fee_gate`
7. `underwriting_status`

## 7. 中国私募基金特别要纳入后续研究层的项目

下面这些在中国市场不是可选项，而是 hard check：

1. `产品类型与策略是否匹配`
   新版《私募证券投资基金运作指引》要求私募证券基金具有明确投资方向、清晰投资策略和风险收益特征，并明确属于固定收益类、权益类、期货和衍生品类、混合类等类型。
2. `流动性条款是否合理`
   开放式私募证券基金一般至多每月开放一次申购赎回，并应根据产品类型、策略和风险特征约定不少于 6 个月的份额锁定安排。
3. `总杠杆是否可接受`
   私募证券基金总资产不得超过净资产的 `200%`。
4. `衍生品使用是否合规且匹配策略`
   参与衍生品交易期间，基金净资产一般不低于 `5000 万元`；向单一交易对手方缴纳的保证金不得超过净资产的 `20%`；衍生品名义本金合计一般不得超过净资产的 `200%`。
5. `量化产品的系统与留痕是否过关`
   量化基金需要具备系统安全、数据安全、研发测试验证流程、防异常交易制度，并保存历史交易记录、策略源码及说明等资料不少于 `20 年`。
6. `信息披露是否能满足机构要求`
   中国证监会发布的《私募投资基金信息披露监督管理办法》自 `2026-09-01` 起施行，后续机构投资者对披露完整性、及时性、重大事件报告的要求只会更高。

## 8. Monitoring 层：观察期和持续覆盖要追什么

这一层建议单独做 flag，而不是反复改写原有标签。

### 核心监控字段

| attribute_key | 类型 | 说明 | 建议取值 |
| --- | --- | --- | --- |
| `style_drift_flag` | `single_select` | 风格是否漂移 | `无`、`轻微`、`明显` |
| `team_change_flag` | `single_select` | 核心团队变化 | `无`、`一般`、`重大` |
| `drawdown_anomaly_flag` | `single_select` | 是否出现异常回撤 | `无`、`关注`、`预警` |
| `disclosure_delay_flag` | `single_select` | 披露延迟或异常 | `无`、`关注`、`预警` |
| `capacity_pressure_flag` | `single_select` | 容量与拥挤度压力 | `无`、`关注`、`预警` |

### 关键原则

- `strategy_family` 不因为一次漂移就改写；
- 漂移应体现在 `style_drift_flag` 和研究结论中；
- 主策略标签只有在长期稳定变化后才调整。

## 9. 适合当前 Watchlist 的最小字段集

如果你现在要先把系统做出来，我建议：

### 9.1 Watchlist 标签层只放 12 个字段

1. `strategy_family`
2. `strategy_subtype`
3. `implementation_style`
4. `trading_universe`
5. `alpha_source`
6. `volatility_bucket`
7. `drawdown_control`
8. `equity_correlation_bucket`
9. `preferred_regime`
10. `weak_regime`
11. `style_stability`
12. `transparency_quality`

### 9.2 后续 Research / ODD 层再单独放研究字段

当前不进 watchlist 主字段。

### 9.3 Monitoring 层单独放 5 个 flag

1. `style_drift_flag`
2. `team_change_flag`
3. `drawdown_anomaly_flag`
4. `disclosure_delay_flag`
5. `capacity_pressure_flag`

## 10. 四个例子按机构版框架怎么打

### 产品 A

低波、股票市场中性、回撤控制好、风格稳定。

- `strategy_family`: `股票对冲`
- `strategy_subtype`: `市场中性`
- `implementation_style`: `量化`
- `trading_universe`: `股票`
- `alpha_source`: `选股 alpha`
- `volatility_bucket`: `低波`
- `drawdown_control`: `强`
- `equity_correlation_bucket`: `低相关`
- `preferred_regime`: `低波震荡`
- `weak_regime`: `单边逼空`
- `style_stability`: `稳定`
- `transparency_quality`: `高` 或 `中`

### 产品 B

高波、量化 CTA、抓趋势能力强、回撤控制好。

- `strategy_family`: `CTA 趋势`
- `strategy_subtype`: `商品 CTA`
- `implementation_style`: `量化`
- `trading_universe`: `商品期货`、`国债期货`
- `alpha_source`: `趋势跟踪`
- `volatility_bucket`: `高波`
- `drawdown_control`: `强`
- `equity_correlation_bucket`: `低相关`
- `preferred_regime`: `高波趋势`、`商品趋势`
- `weak_regime`: `低波无趋势`
- `style_stability`: `稳定`
- `transparency_quality`: `中` 或 `高`

### 产品 C

期货全品种套利、长期稳定、波动和回撤都小、低相关。

- `strategy_family`: `相对价值套利`
- `strategy_subtype`: `跨期套利`、`跨品种套利`
- `implementation_style`: `量化`
- `trading_universe`: `商品期货`
- `alpha_source`: `价差收敛`、`期限结构`
- `volatility_bucket`: `中波`
- `drawdown_control`: `强`
- `equity_correlation_bucket`: `低相关`
- `preferred_regime`: `低波震荡`
- `weak_regime`: `流动性冲击`
- `style_stability`: `稳定`
- `transparency_quality`: `中` 或 `高`

### 产品 D

低波、多策略、CTA 混合股票、低波市场抓趋势好，高波环境回撤失控。

- `strategy_family`: `多策略`
- `strategy_subtype`: `商品 CTA`、`复合多策略`
- `implementation_style`: `主观+量化`
- `trading_universe`: `股票`、`商品期货`
- `alpha_source`: `趋势跟踪`、`选股 alpha`
- `volatility_bucket`: `低波`
- `drawdown_control`: `中`
- `equity_correlation_bucket`: `中相关`
- `preferred_regime`: `低波震荡`
- `weak_regime`: `高波反转`、`流动性冲击`
- `style_stability`: `轻微漂移` 或 `明显漂移`
- `transparency_quality`: `中`

## 11. 对当前项目最有价值的优化点

如果只说一条最重要的优化，我建议是：

不要把“标签体系”理解成配置系统或投决系统。

更好的结构是：

1. `Watchlist`
   只解决“找谁、比谁、什么环境下优先看谁”。
2. `Research / ODD`
   只解决“能不能投、为什么能投、不能投卡在哪”。
3. `Monitoring`
   只解决“投后有没有漂移、有没有异常、是否要降级或退出”。

这才更接近顶级 FOF、养老金、主权基金的做法。

## 12. 我建议的最终版本

如果你现在要推进项目，我建议直接采用下面这个当前边界下的最小方案：

1. `Watchlist 标签` 只保留 5 个主维度：
   `Strategy Identity / Alpha Engine / Risk Behavior / Regime Fit / Researchability`
2. `ODD / 投决` 先不进入 watchlist 主界面，只保留后续扩展位
3. `Monitoring` 只保留 `5 个 drift / anomaly flag`
4. 先从中国私募证券基金最常见的 4 类产品开始：
   `股票对冲`、`CTA 趋势`、`相对价值套利`、`多策略`
5. 第一阶段先服务“候选产品检索与比较”，而不是服务配置或投决

用一句话概括就是：

先用 `策略 + 行为 + 环境` 找基金，再用 `稳定性与透明度` 缩小候选池，后续如有需要再进入单独研究。
