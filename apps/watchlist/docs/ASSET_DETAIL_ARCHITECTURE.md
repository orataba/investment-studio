# Watchlist 与资产详情架构

## 1. 结论

Watchlist 不应继续以“基金详情页加若干条件判断”承载所有资产。当前主路径明确分成两类工作面：

- `public_fund / private_fund` 共用基金研究工作面，但字段、标签和页面文案按运营模型区分。
- `etf / equity / index` 共用 listed-security 外壳，但每类资产拥有独立的信息架构和参考数据 section。

共同能力只包括 identity、数据覆盖、收益风险、taxonomy、投资状态、研究记录、监控与来源说明。基金条款、股票财务、ETF 持仓和指数方法论不互相复用，也不为缺失能力制造占位数据。

## 2. 模块边界

### Registry

共享 Registry 是 canonical instrument、identifier、币种、交易所、行情/NAV、quote policy、return semantics 和 source contract 的唯一所有者。Watchlist 不复制这些事实，也不写回行情。

### Platform

Platform 负责 provider 适配、目录发现、首次 materialize、行情刷新和 reference data。目录返回 `catalog_provider / catalog_symbol`，不把 FMP 的内部字段名泄漏成公共 API 契约。

### Watchlist

Watchlist 只拥有：

- 名单 membership、view、筛选与导出；
- type-specific taxonomy；
- 当前投资判断、人物分析、按资产类型定义的定性评估、人工 rating 与逐条研究记录；
- monitoring assessment；
- 基于 Registry canonical series 的收益、风险和同类比较 read model。

详情页通过 Platform 的 `/api/instruments/{instrument_id}/reference-data` 读取 provider reference sections。Reference data 是可重新获取的展示数据，不成为第二份 canonical 行情或 NAV。

## 3. 单一主源策略

每只 instrument 在建档时只选一个 primary market-data source。刷新任务不会因空响应或错误转向其他 provider，不双写同一序列，也不维护双源对账兼容层。

| 市场 / 资产 | Primary source | 当前能力 |
| --- | --- | --- |
| A 股股票 | FMP | 目录、profile、EOD、财务报表、key metrics、ratios、分红、拆股 |
| A 股公募 | DataHub Tushare | `fund_basic / fund_nav / fund_portfolio` |
| A 股 ETF | DataHub Tushare | `fund_basic / fund_daily / fund_adj / fund_portfolio`；provider contract 不从 FMP 写入 canonical 行情 |
| A 股指数 | 逐只决定；当前核心指数为 DataHub Tushare | `index_basic / index_daily / index_weight` |
| 港股 / 美股股票 | FMP | profile、EOD、财务、估值指标、分红、拆股 |
| 港股 / 美股 ETF 与共同基金 | FMP，前提是具体 symbol 有覆盖 | fund info、EOD、holdings、sector/country allocation、disclosures；共同基金的 EOD→NAV/total-return 语义未核验前不自动写入基金主链路 |
| 私募 / 对冲基金内部产品 | 邮箱或人工正式材料 | NAV、文件、条款与研究记录；不以 13F 或公开行情替代 |

FMP 13F 数据描述机构披露持仓，不是私募/对冲基金 NAV，也不代表完整组合、空头、衍生品或实时敞口，因此不能接入私募 NAV 主链路。

### A 股指数与 ETF 建档规则

当前核心 A 股指数固定使用 Tushare。新增指数时，只有精确 symbol 可解析且历史 EOD 确有数据，
才可在建档时配置为 FMP；验证失败就直接配置为 Tushare。该判断发生在建档阶段，不是运行时
fallback。
已配置 FMP 的 `^GSPC`、`^IXIC`、`^HSI` 等港股/美股指数可展示 provider 返回的 name、
exchange、currency 与 EOD，但通用成分权重接口不可假定存在。

A 股 ETF 固定使用 Tushare；港股、美股 ETF 固定使用 FMP。新建市场或修改 provider 合同时，
必须用当前账户重新核验 entitlement、精确 symbol、历史 EOD、币种和字段语义，并把结果落实为
建档配置；不能把旧抽样结果变成运行时 fallback。

### 跨市场分类与同类比较

- `instrument_type` 回答载体类型，taxonomy 回答投资类别或策略；上市地、注册地和主要地域敞口不再混进同一棵分类树。
- 公募、私募、ETF 和指数按跨市场投资类别分类；股票按市场与行业分类。所有赋值只能落在叶子节点，父节点只用于导航和聚合。
- 公募、私募、ETF 和指数使用单独的 `primary_geographic_exposure` 表达主要地域敞口。它不是上市交易所，也不是基金注册地。
- 同类比较只使用完全相同的 taxonomy 叶子；基金、ETF 和指数还必须具有相同主要地域敞口，股票则由 taxonomy 市场根节点确定地域。不会向父类回退，也不会将 `其他`、`待分类` 当作有效同类组。
- `candidate_count` 是地域过滤后、日期过滤前的独立同类数量；`sample_count` 是与目标同一计算日且可比较的独立同类数量。少于四个独立同类时保留统计描述，但不生成百分位。

## 4. 详情页信息架构

| 资产类型 | Tabs | 核心内容 |
| --- | --- | --- |
| 公募 | Overview / Performance / Risk / Fees / Portfolio / Management / Strategy / Documents / Research / Monitoring | 基金档案、报价主图、费用、基准、持仓、管理人、定量筛选、研究结论 |
| 私募 | Overview / Performance / Risk / Terms / Exposure / Organization / Strategy / Documents / Research / Monitoring | NAV 主图与证据、频率、锁定/申赎/闸门/业绩报酬、团队与运营、研究和监控 |
| ETF | Overview / Research / Performance / Risk / Price / Portfolio / Monitoring | 投资论点、产品/指数/实施质量、折溢价与交易框架、管理人/做市治理、交易行情、配置、持仓与监控 |
| 股票 | Overview / Research / Performance / Risk / Price / Fundamentals / Events / Monitoring | 投资论点、商业质量、估值、管理层与治理、证伪条件、公司 profile、财务、事件与监控 |
| 指数 | Overview / Research / Performance / Risk / Price / Profile / Monitoring | 投资论点、指数构建与治理判断、适用的组合角色、指数档案、来源合同与监控 |

Morningstar 可作为信息密度、同类比较和研究工作流的参考，但页面不复制其 rating 语义，也不展示当前数据源无法支持的模块。

## 5. 投资研究工作面

Research 是所有五类资产的核心工作面，不是基金页的附属展示。每个标的统一分成四层：

1. `Current Investment View`：当前论点、当前判断、为什么是现在、优势/质量、估值框架与催化剂。
2. `Risk & Falsification`：关键风险、论点失效条件、反证、证据缺口和下一步跟踪计划。
3. `People & Decision`：人物与治理分析、组合角色、投资期限和决策依据。人物字段记录研究员的判断，不重复 provider profile 或简历事实。
4. `Research Record`：独立、带日期和作者的 evidence / meeting / event / risk / decision / review 记录，保存正文、人物、证据来源、标签、跟进日期以及创建/修改时间。

当前判断与研究记录不保存在 `instrument_manual_profile` 的整包 JSON 中。它们分别使用
`instrument_research_profile` 和 `instrument_research_note`：更新当前判断不会覆盖历史记录，单条记录
有独立新增、修改和删除接口。profile 与 note 每次实际变化都写入不可变 revision；相同内容的重复
保存不制造新版本，删除 note 只做可审计软删除。

资产专属模板只改变问题和标签，不拆出五套存储：

- 股票：商业质量/护城河、估值、管理层、资本配置与治理；
- ETF：产品与指数质量、复制/实施、折溢价与流动性、管理人及做市治理；
- 指数：编制方法、代表性、再平衡、提供方和方法论治理；
- 公募：投资流程、基金经理与团队、费用/配置、DD 与 IC；
- 私募：策略优势、关键人物、机构与运营、条款、DD / ODD / IC。

### 5.1 基金判断与评分

基金研究使用四层结构，不计算自动综合评分：

1. `Instrument Taxonomy`：回答“它是什么”，用于定义可比 universe。
2. 定量 read model：收益、回撤、波动、Sharpe、peer rank，只描述可验证结果。
3. `Qualitative Assessment`：edge、流程、团队、组合构建、风险、容量、条款、治理和组合角色等基金专属受控结论；可用于详情、列和筛选，不作为多资产 Group By 维度。
4. `Manual Rating`：1–5 星的当前 conviction，由研究员给出，并在 `Research Record` 留下证据与变更原因。

收益率、Sharpe、规模和定性标签不能直接相加成一个自动投资评级。若未来需要 model score，必须先定义同类 universe、字段方向、缺失值含义、权重版本、as-of 和适用范围，并与人工 conviction 分开展示。

## 6. 当前实现决策

- 证券目录公共契约使用 provider-neutral 的 `catalog_provider / catalog_symbol`。
- A 股股票固定使用 FMP；A 股公募和 A 股 ETF 固定使用 Tushare，港股/美股 ETF 固定使用 FMP。
- 系统没有 CSI 官网 fallback、运行时 provider fallback 或双源主序列。
- FMP 股票、海外 ETF 和指数，以及 Tushare 公募、A 股 ETF 和指数，只展示各自真实可得的 reference sections。
- Listed detail 按 ETF、股票、指数拆分 tabs；公募与私募分别使用适合其运营模型的条款、敞口和组织字段。
- 私募 Organization 记录管理人、GP/受托人、行政管理人、托管人、审计师和 prime broker；公募记录管理公司、基金经理和任期。
- Fund Detail 没有第二套 People、Strategy、Documents 状态；基金风险指标、同类排名与人工 conviction 分开展示，不计算未经校准的自动风险积分。
- 投资研究只使用显式 profile、逐条 note 和不可变 revision。
- 五类资产共用研究记录合同和编辑器，但各自使用 type-specific 问题模板。
- 基金/ETF/指数 taxonomy 使用跨市场投资分类，股票使用市场与行业分类。
- Peer policy 固定为同一叶子、同一地域和同一计算日，不向父级回退，也不用其他期限替代 1Y 主排名。
- Watchlist 研究字段直接投影自当前 profile/note；Group By 只使用对当前名单全部资产成立的聚合维度。Monitoring 同时暴露缺失判断、到期复核和到期跟进。

## 7. 数据能力边界

以下内容暂不伪造，也不通过另一 provider 兜底：

- FMP 未覆盖指数的行情、任意指数的官方方法论 PDF 与调仓规则；
- FMP 未覆盖基金的持仓、费用或 AUM；
- 13F 未披露的对冲基金 NAV、空头、衍生品和完整风险敞口；
- 缺少分红再投资证据时的基金 total-return NAV；
- 缺少同日共同观测时的 benchmark 相对指标。

这些是来源能力边界，不应以空对象、推断值、静默 fallback 或第二套数据管线掩盖。

## 8. Provider 参考资料

- [FMP ETF 与共同基金数据集](https://site.financialmodelingprep.com/datasets/etf-mutual-funds)：基金档案、披露持仓和配置数据能力。
- [FMP 指数数据集](https://site.financialmodelingprep.com/datasets/indexes)：指数目录与已支持的数据集；是否可用仍需逐只核验精确 symbol 与 EOD。
- [FMP Developer Documentation](https://site.financialmodelingprep.com/developer/docs)：股票 profile、财务报表、指标、比率、分红、拆股与 EOD 合约。
- [FMP Form 13F 数据集](https://site.financialmodelingprep.com/datasets/form-13f)：机构披露持仓，不是对冲基金 NAV 或完整账簿数据源。
