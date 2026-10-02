import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { WorkspaceToolIcon } from '../../../../../packages/ui/src/WorkspaceTools'
import InfoHint from './InfoHint'
import type { CorrelationHistoryObservation } from '../lib/correlationHistory'
import { formatNumber, formatPercent } from '../lib/format'

export type CorrelationRiskObservation = { scopeLabel: string; observation: CorrelationHistoryObservation }

export default function CorrelationObservation({ scopeLabel, observation, alertOnly = false }: CorrelationRiskObservation & { alertOnly?: boolean }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const { current, previous, averageChange, risingPairCount, pairCount, attention } = observation
  if (current.averageCorrelation === null || (alertOnly && !attention)) return null
  const correlationNumber = (value: number) => value !== 0 && Math.abs(value) < 0.001
    ? value.toExponential(2) : formatNumber(value, 3)
  const reason = {
    too_few_members: zh ? '至少需要三个标的或分类，才能判断相关性是否普遍上升。' : 'Broad-rise screening requires at least three instruments or categories.',
    current_window: zh ? '所选截止日前一个月的样本不可用。' : 'The monthly sample ending at the selected cutoff is unavailable.',
    previous_window: zh ? '前一个月样本不可用，无法比较变化。' : 'The preceding monthly sample is unavailable; change cannot be compared.',
    history_incomplete: zh ? '需有当前月及此前十二个月的完整样本，暂不生成异常提醒。' : 'The current month and twelve prior monthly samples are required; no anomaly signal is produced.',
    overlapping_periods: zh ? '相邻月份的实际收益区间重叠，暂不生成异常提醒。' : 'Actual return periods overlap between adjacent months; no anomaly signal is produced.',
    flat_baseline: zh ? '历史水平或月度变化的四分位距过小，无法形成有效参考线，暂不生成异常提醒。' : 'The historical reference has no interquartile dispersion; no anomaly signal is produced.',
  }
  const detail = [
    zh ? '固定当前标的、分类及权重，比较所选截止日前一个月与此前一个月的相关性；两个收益区间互不重叠。此处固定使用月度样本，不随矩阵的观察窗口变化，也不还原历史实际持仓。'
      : 'Holds current instruments, classifications and weights fixed. Compares the month ending at the selected cutoff with the preceding non-overlapping month, independently of the matrix window. This does not reconstruct historical holdings.',
    zh ? '平均相关性为所有不重复配对的相关系数的等权平均，保留正负号。平均水平和月度增幅均超过各自过去一年的异常上界（上四分位数 Q3 + 1.5 × 四分位距 IQR），且超过半数配对上升时，提示分散效果减弱。此前十二个月的水平和十一个月度变化用于计算参考线，不含当前月。'
      : 'Averages signed correlations equally over unique pairs. A cue requires both the level and its monthly increase to exceed their prior-year Q3 + 1.5 × IQR fences, with a majority of pairs rising. The reference uses twelve prior monthly levels and eleven changes, excluding the current month.',
    zh ? '这是探索性的异常观察，不是经过校准的危机预测或统计显著性结论；历史很稳定时，较小的绝对变化也可能触发。需要结合实际变化幅度、波动率、跌幅与持仓复核。'
      : 'This is an exploratory outlier screen, not a calibrated crisis forecast or a significance test. A very stable reference may flag a small absolute change. Review the change magnitude, volatility, drawdowns and holdings alongside it.',
    `${zh ? '本期' : 'Current'} ${current.windowStartDate} → ${current.asOfDate} · ${current.observationCount} ${zh ? '个共同观测' : 'common observations'}; ${zh ? '前期' : 'Previous'} ${previous.windowStartDate} → ${previous.asOfDate} · ${previous.observationCount}`,
    ...(observation.unavailableReason ? [reason[observation.unavailableReason]] : [
      `${zh ? '本次异常上界：平均水平' : 'Reference fences: level'} ${observation.levelUpperFence == null ? '—' : correlationNumber(observation.levelUpperFence)} · ${zh ? '月度增幅' : 'monthly change'} ${observation.changeUpperFence == null ? '—' : correlationNumber(observation.changeUpperFence)}`,
    ]),
  ]
  return <div className={`risk-correlation-observation${attention ? ' risk-correlation-observation-attention' : ''}`} role={attention ? 'status' : undefined}>
    {attention ? <div className="risk-correlation-signal"><WorkspaceToolIcon kind="risk" /><strong>{zh ? '相关性异常上升，分散效果可能减弱' : 'Unusual correlation rise — diversification may weaken'}</strong></div> : null}
    <div className="risk-correlation-observation-values">
      <span>{zh ? '近月平均相关性' : 'Latest monthly mean correlation'} <strong>{correlationNumber(current.averageCorrelation)}</strong></span>
      {averageChange !== null ? <span>{zh ? '较前月' : 'Change'} <strong>{averageChange > 0 ? '+' : ''}{correlationNumber(averageChange)}</strong></span> : null}
      {risingPairCount !== null ? <span>{zh ? '上升配对' : 'Rising pairs'} <strong>{risingPairCount}/{pairCount}</strong> ({formatPercent(risingPairCount / pairCount)})</span> : null}
      <InfoHint label={zh ? '相关性变化观察方法' : 'Correlation change method'} detail={detail} />
    </div>
    {attention ? <p><span translate="no">{scopeLabel}</span> · {current.asOfDate} · {zh ? '请结合波动率和跌幅复核；本提示不等同于市场危机预测。' : 'Review volatility and drawdowns; this cue does not predict a market crisis.'}</p> : null}
  </div>
}
