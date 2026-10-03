import type { PriceRuleCounts, PriceRuleSummary } from './instrumentRisk'
import { percent } from './instrumentRisk'
import { researchStamp } from './ResearchRunStatus'

const origins = { default: '默认', custom: '自定义', unknown: '来源未记录', none: '未设置' }
const states = { not_configured: '未设置', unavailable: '不可评估', triggered: '需复核', not_triggered: '未触发' }

export function PriceRuleCountsLine({ counts }: { counts: PriceRuleCounts }) {
  return <p className="research-muted">
    <span>已配置规则</span> {counts.configured} · <span>可评估</span> {counts.evaluable} · <span>触发规则</span> {counts.triggered} · <span>不可评估</span> {counts.unavailable}
    {' · '}<span>默认</span> {counts.default} · <span>自定义</span> {counts.custom}
    {counts.unknown > 0 && <> · <span>来源未记录</span> {counts.unknown}</>}
  </p>
}

export function PriceRuleDetails({ summary }: { summary: PriceRuleSummary }) {
  return <>
    <p className="research-muted"><span>设置保存时间</span> {summary.settings_updated_at ? researchStamp(summary.settings_updated_at) : <span>未记录</span>}</p>
    <div className="research-table-scroll"><table>
      <thead><tr><th>规则</th><th>复核线</th><th>来源</th><th>读数</th><th>状态</th><th>实际观察区间</th></tr></thead>
      <tbody>{summary.rules.map(rule => <tr key={rule.key}>
        <td>{rule.key === 'drawdown' ? <span>高点回撤</span> : <>{rule.observations} <span>个观测值</span></>}</td>
        <td>{rule.limit_pct == null ? '—' : `−${rule.limit_pct}%`}</td>
        <td>{origins[rule.source]}</td><td>{percent(rule.value_pct)}</td><td title={rule.limitation || undefined}>{states[rule.state]}</td>
        <td>{rule.start_date ? `${rule.start_date} → ` : ''}{rule.end_date || '—'}</td>
      </tr>)}</tbody>
    </table></div>
  </>
}
