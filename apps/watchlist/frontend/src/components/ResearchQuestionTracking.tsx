import { useState, type FormEvent } from 'react'
import { useStudioAccount } from './AccountBoundary'
import type { AskResearchAssistant, ResearchUpdate } from '../lib/researchDossierApi'

export const questionTrackingLabels = { active: '持续跟踪', paused: '已暂停跟踪', closed: '已结束跟踪' }

export default function ResearchQuestionTracking({ update, onAskAssistant }: { update: ResearchUpdate; onAskAssistant?: AskResearchAssistant }) {
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const current = update.tracking_status || 'active'
  const [editing, setEditing] = useState(false)
  const [status, setStatus] = useState(current)
  const [reason, setReason] = useState('')
  if (!canWrite || !onAskAssistant || update.kind !== 'question' || update.superseded || update.withdrawn) return null
  function submit(event: FormEvent) {
    event.preventDefault()
    if (status !== 'active' && !reason.trim()) return
    onAskAssistant?.(`请将研究问题“${update.title}”的跟踪安排明确调整为${questionTrackingLabels[status]}，保存并发布这项团队研究更新。${reason.trim() ? `我的原因：${reason.trim()}。` : ''}请先读取所关联的当前问题版本，只改变跟踪安排及原因，保留证据判断、原正文和原始出处；如果此版本已经被更新，先说明差异再处理。`, { ...update.reference, research_update_id: update.update_id })
    setEditing(false)
  }
  return <div className="research-theme-actions">
    <button type="button" className="sector-event-ask" onClick={() => { setEditing(value => !value); setStatus(current); setReason('') }}>调整跟踪</button>
    {editing && <form className="research-theme-editor" onSubmit={submit}>
      <label>跟踪安排<select value={status} onChange={event => setStatus(event.target.value as typeof status)}>{Object.entries(questionTrackingLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>调整原因<textarea value={reason} required={status !== 'active'} onChange={event => setReason(event.target.value)} /></label>
      <p className="sector-research-note">跟踪安排不改变证据判断。交给助手保存后，以发布结果为准。</p>
      <button type="submit" disabled={status !== 'active' && !reason.trim()}>交给助手保存</button>
      <button type="button" onClick={() => setEditing(false)}>取消</button>
    </form>}
  </div>
}
