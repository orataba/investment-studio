# Investment Analytics Methodology

当前计算契约为 `canonical-investment-analytics/v2`。后端
`investment_analytics.py` 是基金详情页投资分析的唯一计算权威；前端不得重算、补值或把
`null` 转成 `0`。

## 输入边界

- 输入必须是同一收益口径下的 canonical calculation series。
- 日期必须有效且唯一，NAV 必须为有限正数；任一非法观察使整条输入序列 fail closed。
- `period_return` 只要求两个有效端点。所有年化或统计指标执行各自更严格的最低样本门槛。
- 观察频率只有在至少有两个日期且间隔模式稳定时才可推断。空序列、单点序列或不规则序列的
  inferred frequency 为 `null`，风险计算返回 `observation_frequency_unresolved`，不得默认按日频。
- 数据源声明的频率与日期推断的频率是两个独立事实。声明频率用于 canonical series 的上游
  采样决策，但不得改写或冒充 inferred frequency。

## 收益与长期风险收益指标

| 指标 | 方法与最低门槛 | 不满足时的稳定原因 |
| --- | --- | --- |
| Annualized return | 几何年化；至少 `365` 个日历日 | `insufficient_history` |
| Calmar | 年化收益 / 最大回撤绝对值；至少 `ceil(3 × 365.25) = 1096` 个日历日 | `insufficient_history` |
| Volatility / Sharpe / downside deviation / Sortino | 至少 `12` 个同频有效收益观察；日/周/月分别使用 `252/52/12` 年化因子 | `insufficient_history` |

Sharpe 的 risk-free rate 固定为 `0`。Sortino 的 minimum acceptable return
固定为 `0`。Downside deviation 使用全部 `n` 个收益观察计算二阶下偏矩：

```text
sqrt(sum(min(0, r_t - MAR)^2) / n) * sqrt(periods_per_year)
```

所有风险指标按稳定的同频序列计算，并固定使用日/周/月 `252/52/12` 年化因子，不根据
样本覆盖的局部日历密度动态改变因子。只对推断频率范围内的收益间隔计算风险指标。被排除的异频间隔必须通过
`off_frequency_observations_excluded` 和 excluded count 暴露；不得静默混入样本。

## Benchmark 与相对指标

- Benchmark 必须在 fund window 的每一个所需日期拥有精确观察；不得 carry-forward、
  as-of join 或 pairwise 静默删除。
- 起止端点缺失返回 `comparison_boundary_mismatch`；中间频率边界缺失返回
  `comparison_frequency_mismatch`。
- Excess return 只依赖精确同期起止端点，并拥有独立 quality。
- Tracking error、information ratio 和 beta 至少要求 `12` 个精确同期、同频收益观察，
  每个指标拥有独立 status/reason。
- Upside/downside capture 各自至少要求对应 benchmark regime 中 `3` 个精确同期观察；
  不足返回 `insufficient_regime_observations`。

## 序列指标

- Monthly annualized volatility 先在完整序列上判定稳定频率，再对每个月至少 `12` 个
  同频收益观察使用对应固定因子 `252/52/12` 年化。月内个别异频间隔会被排除；排除后样本
  仍达门槛的月份可以出值，整体标记 `qualified` 和
  `observation_frequency_drift_excluded`。排除后不达门槛的月份不出值；若所有合格候选月都
  因此失败，返回 `observation_frequency_drift`。
- Rolling volatility 与 rolling Sharpe 分别返回 fund/benchmark 的 series quality。
  每个 quality 明确记录已使用和排除的窗口数；异频间隔、无法判定频率、样本不足以及
  Sharpe 的零方差窗口均不得静默删除。
- Rolling beta 使用连续日历月窗口。Fund 与 benchmark 的每个月度收益必须具有完全相同的
  start/end date；缺月或非同期窗口不产生 beta，并返回对应稳定原因。

## Quality contract

Metric quality 统一包含：

```text
status: available | qualified | unavailable | not_requested
reason: stable machine-readable code | null
observation_count
excluded_observation_count
used_window_count
excluded_window_count
```

`null` 必须对应 `unavailable` 或 `not_requested` 的指标级 quality。一个指标可用不得掩盖
同组另一个指标不可用；相对指标因此逐项返回 quality。

## Period boundary 与 freshness

本模块只消费上游提供的 canonical calculation series，不自行从多个快照中选择“最新”记录，
也不实现本地 fallback resolver。Period boundary 与 adopted/as-of freshness 必须由共享的
canonical resolver 统一决定。Watchlist consumer 只按 canonical instrument type 选择 freshness
profile：fund 为 45 个日历日，其他类型为 5 个日历日。若 current endpoint 超限，API 明确返回
current endpoint state，并把仍可使用的历史分析标成其真实 `analysis_as_of_date`，不得把它冒充
current/trailing 结果。
