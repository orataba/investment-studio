import type { RiskCaseHistory, RiskAssessment } from './instrumentRisk'
import { researchStamp } from './ResearchRunStatus'

const states: Record<string, string> = { open: '待查看', investigating: '跟进中', handled: '已处理', resolved: '已解除', active: '有效风险', pending: '待核证', dismissed: '不成立' }
function State({ value }: { value: NonNullable<RiskCaseHistory['before']> }) {
  const assessment = value.risk_assessment as RiskAssessment | null
  return <><span>{states[value.status] || value.status}</span> · <span>{value.trigger_active ? '风险条件有效' : '风险条件未触发'}</span>
    {assessment && <> · <span>{states[assessment.status] || assessment.status}</span>{assessment.reason && <span translate="no"> · {assessment.reason}</span>}</>}
  </>
}
export default function RiskChangeAudit({ change }: { change: RiskCaseHistory }) {
  return <div className="risk-change-audit">
    <p><span>任务编号</span> <code translate="no">{change.run_id || change.risk_assessment?.run_id}</code></p>
    <p><span>操作者</span> · <span translate="no">{change.actor?.display_name || change.actor?.user_id || change.actor?.service_id || '—'}</span></p>
    {change.scope && <p><span>研判范围</span> · <span translate="no">{change.scope.name}</span></p>}
    <p><span>变更时间</span> · <time dateTime={change.at} title={change.at}>{researchStamp(change.at)}</time></p>
    {change.before && <p><strong>变更前</strong> · <State value={change.before} /></p>}
    {change.after && <p><strong>变更后</strong> · <State value={change.after} /></p>}
  </div>
}
