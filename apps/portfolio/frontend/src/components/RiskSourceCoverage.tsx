import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { HoldingsWorkspaceResponse } from '../lib/api'
import { riskWindowStart } from '../lib/riskReturnAlignment'

export default function RiskSourceCoverage({ workspace }: { workspace: HoldingsWorkspaceResponse }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const names = new Map(workspace.rows.flatMap((row) => row.instrument_core
    ? [[row.instrument_core.instrument_id, row.instrument_core.instrument_name] as const] : []))
  const start = workspace.risk_policy
    ? riskWindowStart(workspace.as_of_date, workspace.risk_policy.lookback_days) : null
  // Complete source dates are authoritative. A truncated diagnostic sample
  // cannot establish that all gaps are outside this model's window.
  const gaps = (workspace.risk_basis?.gap_details ?? []).flatMap((gap) => {
    const row = workspace.rows.find((item) => item.instrument_core?.instrument_id === gap.instrument_id
      || item.risk_return_series?.source_instrument_ids.includes(gap.instrument_id))
    const completeDates = row?.risk_return_series?.observation_coverage?.gap_dates
      ?? (gap.gap_count === gap.gap_date_sample.length ? gap.gap_date_sample : null)
    if (!start || !completeDates) return [gap]
    const selectedPeriods = (row?.risk_return_series?.points ?? [])
      .filter((point) => point.date > start && point.date <= workspace.as_of_date)
    // A source gap before the window boundary still matters when an included
    // return spans it. Match returnWindowInputIssues' actual-period check.
    const dates = completeDates.filter((date) => (date > start && date <= workspace.as_of_date)
      || selectedPeriods.some((point) => point.start_date && date > point.start_date && date <= point.date))
    return dates.length ? [{ ...gap, gap_count: dates.length, gap_date_sample: dates }] : []
  })
  if (!gaps.length) return null
  return <details className="risk-source-coverage">
    <summary>{zh ? '历史来源覆盖' : 'Historical source coverage'} · {gaps.length} {zh ? '个标的需核对' : 'instruments to review'}</summary>
    <p>{zh
      ? '仅列出会影响当前模型样本的数据缺口，以及缺口日期尚未核实完整的标的。其他历史区间能否计算，请查看对应图表的样本说明。'
      : 'Only gaps affecting the current model sample, or instruments without complete gap dates, are listed. Historical analyses explain availability within their own charts.'}</p>
    {start ? <p>{zh ? '模型回看区间' : 'Model window'} {start} → {workspace.as_of_date}</p> : null}
    <ul>{gaps.map((gap) => <li key={gap.instrument_id}>
      <span translate="no">{names.get(gap.instrument_id) ?? gap.instrument_id}</span>
      <span> · {gap.gap_date_sample.join(', ')}</span>
      {gap.gap_count > gap.gap_date_sample.length ? <span> · {zh ? `共 ${gap.gap_count} 处` : `${gap.gap_count} in total`}</span> : null}
    </li>)}</ul>
  </details>
}
