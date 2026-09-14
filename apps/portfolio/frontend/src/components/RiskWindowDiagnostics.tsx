import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { RiskWindowDiagnostics as WindowDiagnostics } from '../lib/riskWindowData'

const DIAGNOSTIC_MESSAGES: Record<string, string> = {
  'Correlation comparison requires at least two members in the selected scope.': '此范围不足两个成员，无法进行相关性比较。',
  'Correlation is undefined because at least one member has zero return variation in this window.': '至少一个成员在此窗口的收益没有变化，无法定义相关系数。',
  'The selected return window crosses missing source observations.': '此窗口跨越了来源观测缺口，需要补齐所列日期的总回报数据。',
  'Return dates do not match every other member in the selected window.': '成员的收益日期不一致，无法建立共同样本。',
  'Return dates do not match every other member in the selected scope.': '成员的收益日期不一致，无法建立共同样本。',
  'The selected window contains non-finite returns.': '此窗口包含无效收益值。',
  'The selected window contains duplicate period end dates.': '此窗口包含重复收益日期。',
  'Return periods require a known start date before their end date.': '部分收益缺少有效的区间起点，无法确认可比期间。',
  'Return series contains an observation without a period end date.': '收益记录缺少区间结束日期。',
  'No eligible market-risk return observations are available.': '暂无符合计算条件的市场风险收益观测。',
  'The selected sample cannot produce a volatility estimate.': '此样本无法计算波动率。',
  'The actual portfolio market-risk return is incomplete or its return chain breaks in this window.': '此窗口的实际组合市场风险收益不完整，或收益链存在中断。',
  'The actual portfolio daily history is missing a valuation period.': '实际组合每日历史缺少一个估值区间。',
  'An eligible actual portfolio market-risk return is unavailable.': '应有的实际组合市场风险收益观测不可用。',
  'The supplied return series do not include every member of the selected scope.': '收益序列未覆盖所选范围的全部成员。',
  'Complete source-gap dates are missing for this member; its selected window cannot be verified.': '缺少此标的完整的来源缺口日期，无法核验所选窗口。',
}

export function riskDiagnosticMessage(message: string, zh: boolean, translate: (message: string) => string) {
  if (!zh) return message
  if (DIAGNOSTIC_MESSAGES[message]) return DIAGNOSTIC_MESSAGES[message]
  const start = message.match(/^Risk window lacks a valid daily start anchor near (\d{4}-\d{2}-\d{2})\.$/)
  if (start) return `缺少 ${start[1]} 附近的有效起始观测，历史尚未覆盖所选窗口。`
  const count = message.match(/^Risk window requires at least (\d+) daily observations; got (\d+)\.$/)
  if (count) return `至少需要 ${count[1]} 个日收益观测，目前有 ${count[2]} 个。`
  const span = message.match(/^Risk window covers (\d+) days; at least (\d+) days are required for (.+)\.$/)
  if (span) return `实际历史跨度 ${span[1]} 天；此窗口至少需要 ${span[2]} 天。`
  const stale = message.match(/^Risk window latest observation is (\d+) days before (\d{4}-\d{2}-\d{2}); maximum allowed for daily is (\d+) days\.$/)
  if (stale) return `最新观测距 ${stale[2]} 已有 ${stale[1]} 天，超过允许的 ${stale[3]} 天。`
  const period = message.match(/^Return ending (\d{4}-\d{2}-\d{2}) does not share one period identity across the selected members\.$/)
  if (period) return `截至 ${period[1]} 的收益起止区间不一致，不能直接比较。`
  return translate(message)
}

export default function RiskWindowDiagnostics({ diagnostics }: { diagnostics: WindowDiagnostics }) {
  const { language, t } = useLanguage()
  const zh = language === 'zh-Hans'
  const { observedStartDate, observedEndDate, observationCount, requiredObservationCount, issues } = diagnostics
  const available = diagnostics.status === 'available'
  const reasons = {
    scope_unavailable: zh ? '此分析范围暂不可用' : 'Scope unavailable',
    missing_member: zh ? '缺少标的资料' : 'Missing instrument information',
    missing_weight: zh ? '缺少当前持仓权重' : 'Missing current weight',
    missing_series: zh ? '缺少可用的总回报历史' : 'Missing total-return history',
    window_coverage: zh ? '样本不足以覆盖所选窗口' : 'Insufficient window coverage',
    misaligned_dates: zh ? '收益区间缺失或不一致' : 'Missing or misaligned return periods',
    calculation_unavailable: zh ? '无法计算此项指标' : 'Metric unavailable',
  }
  return <div className="risk-window-diagnostics">
    <div className="risk-window-sample" aria-label={zh ? '分析样本' : 'Analysis sample'}>
      <span>{zh ? '观察窗口' : 'Observation window'} {diagnostics.windowStartDate} → {diagnostics.asOfDate}</span>
      <span>{available ? (zh ? '有效样本' : 'Valid observations') : (zh ? '观测日期数' : 'Observed dates')} {observationCount}</span>
      {observedStartDate && observedEndDate ? <span>{available ? (zh ? '实际样本' : 'Actual sample') : (zh ? '来源区间' : 'Source range')} {observedStartDate} → {observedEndDate}</span> : null}
    </div>
    {diagnostics.status === 'unavailable' ? <div className="risk-window-unavailable" role="status">
      <span>{observationCount < requiredObservationCount
        ? zh ? `所选窗口至少需要 ${requiredObservationCount} 个有效观测，目前窗口内有 ${observationCount} 个观测日期。` : `This window requires ${requiredObservationCount} valid observations; ${observationCount} dates were observed.`
        : issues[0] ? riskDiagnosticMessage(issues[0].coverageReason, zh, t)
          : zh ? '所选范围暂无法计算此项指标。' : 'This metric is unavailable for the selected scope.'}</span>
      {issues.length ? <details>
        <summary>{zh ? '查看原因与影响标的' : 'View reasons and affected instruments'}</summary>
        <ul>{issues.map((issue, index) => <li key={`${issue.memberKey}:${issue.reason}:${index}`}>
          <span translate="no">{issue.memberKey === 'portfolio' || issue.memberKey === 'realized-portfolio' ? (zh ? '组合' : 'Portfolio') : issue.memberKey === 'scope' ? (zh ? '所选范围' : 'Selected scope') : issue.memberLabel}</span>
          <span> · {reasons[issue.reason]}</span>
          {issue.missingDates.length ? <span> · {issue.missingDates.join(', ')}</span> : null}
          <div className="portfolio-detail-meta">{riskDiagnosticMessage(issue.coverageReason, zh, t)}</div>
        </li>)}</ul>
      </details> : null}
    </div> : null}
  </div>
}
