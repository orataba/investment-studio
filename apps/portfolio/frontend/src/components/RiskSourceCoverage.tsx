import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { HoldingsWorkspaceResponse } from '../lib/api'
import { riskWindowStart } from '../lib/riskReturnAlignment'

export default function RiskSourceCoverage({ workspace }: { workspace: HoldingsWorkspaceResponse }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const gaps = workspace.risk_basis?.gap_details ?? []
  if (!gaps.length) return null
  const names = new Map(workspace.rows.flatMap((row) => row.instrument_core
    ? [[row.instrument_core.instrument_id, row.instrument_core.instrument_name] as const] : []))
  const start = workspace.risk_policy
    ? riskWindowStart(workspace.as_of_date, workspace.risk_policy.lookback_days) : null
  return <details className="risk-source-coverage">
    <summary>{zh ? '历史来源覆盖' : 'Historical source coverage'} · {gaps.length} {zh ? '个标的有历史缺口' : 'instruments with historical gaps'}</summary>
    <p>{zh
      ? '此处检查标的总回报历史；组合净值核对的是实际持有期间的估值。每项风险指标按自身窗口判断是否可用。'
      : 'This checks instrument total-return history. Portfolio NAV checks valuations while positions were held. Each risk metric assesses its own observation window.'}</p>
    {workspace.risk_basis?.window_start_date && workspace.risk_basis.window_end_date ? <p>
      {zh ? '来源检查区间' : 'Source review period'} {workspace.risk_basis.window_start_date} → {workspace.risk_basis.window_end_date}
    </p> : null}
    <ul>{gaps.map((gap) => <li key={gap.instrument_id}>
      <span translate="no">{names.get(gap.instrument_id) ?? gap.instrument_id}</span>
      <span> · {gap.gap_date_sample.join(', ')}</span>
      {gap.gap_count > gap.gap_date_sample.length ? <span> · {zh ? `共 ${gap.gap_count} 处` : `${gap.gap_count} in total`}</span> : null}
      {start && gap.gap_count === gap.gap_date_sample.length && gap.gap_date_sample.every((date) => date < start || date > workspace.as_of_date)
        ? <span> · {zh ? '在当前模型窗口之外' : 'Outside the current model window'}</span> : null}
    </li>)}</ul>
  </details>
}
