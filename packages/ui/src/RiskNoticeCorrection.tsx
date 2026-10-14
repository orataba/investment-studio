import { useState } from 'react'
import { useLanguage } from './i18n'
import type { RiskNotice } from './RiskOfficerPanel'
import type { RiskRequest } from './instrumentRisk'

export default function RiskNoticeCorrection({ notice, request, onSaved }: { notice: RiskNotice; request: RiskRequest; onSaved: () => void }) {
  const zh = useLanguage().language === 'zh-Hans'
  const [level, setLevel] = useState(notice.level || 'medium')
  const [title, setTitle] = useState(notice.title)
  const [body, setBody] = useState(notice.body)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <details className="risk-notice-correction"><summary>{zh ? '更正等级与措辞' : 'Correct priority and wording'}</summary><form className="risk-guidance-editor" onSubmit={async event => {
    event.preventDefault(); if (!notice.run_id || busy) return
    setBusy(true); setError('')
    try { await request(`/risk/notices/${encodeURIComponent(notice.run_id)}/${encodeURIComponent(notice.notice_id)}`, { method: 'PATCH', body: JSON.stringify({ expected_notice_version: notice.notice_version, level, title, body, reason }) }); onSaved() } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)) } finally { setBusy(false) }
  }}>
    <p>{zh ? '保留原稿与修改记录；不改变事项状态或严重规则事实。' : 'The original text and edit history are retained. Case state and severe-rule facts are unchanged.'}</p>
    <label>{zh ? '关注等级' : 'Attention priority'}<select value={level} disabled={busy} onChange={event => setLevel(event.target.value as 'high' | 'medium' | 'low')}><option value="high">{zh ? '高' : 'High'}</option><option value="medium">{zh ? '中' : 'Medium'}</option><option value="low">{zh ? '低' : 'Low'}</option></select></label>
    <label>{zh ? '提示标题' : 'Alert title'}<input required value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
    <label>{zh ? '简短说明' : 'Short explanation'}<textarea required rows={3} value={body} disabled={busy} onChange={event => setBody(event.target.value)} /></label>
    <label>{zh ? '更正原因' : 'Reason for correction'}<input required value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
    <button disabled={busy || !reason.trim()}>{zh ? '保存更正' : 'Save correction'}</button>{error && <p role="alert">{error}</p>}
  </form></details>
}

export function RiskNoticePreferences({ notice, request, onSaved }: { notice: RiskNotice; request: RiskRequest; onSaved: () => void }) {
  const zh = useLanguage().language === 'zh-Hans'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(action: 'read' | 'mute' | 'unmute') {
    setBusy(true); setError('')
    try { await request('/risk/notices/receipt', { method: 'PUT', body: JSON.stringify({ notice_id: notice.notice_id, notice_version: notice.notice_version, rule_versions: Object.fromEntries((notice.current_rule_facts || []).filter(fact => fact.notice_id && fact.notice_version != null).map(fact => [fact.notice_id!, String(fact.notice_version)])), ...(notice.run_id ? { run_id: notice.run_id } : {}), action, ...(action === 'mute' ? { muted_until: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString() } : {}) }) }); onSaved() } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)) } finally { setBusy(false) }
  }
  return <div className="risk-notice-preferences"><p className="risk-officer-muted">{zh ? '通知偏好仅作用于你和当前版本；已读或静默不会解除风险。新版本仍会提示。' : 'Preferences apply only to you and this version. Reading or muting does not resolve risk. New versions still alert.'}</p><div>{notice.receipt?.unread !== false && <button type="button" disabled={busy} onClick={() => void save('read')}>{zh ? '标为已读' : 'Mark read'}</button>}<button type="button" disabled={busy} onClick={() => void save(notice.receipt?.muted ? 'unmute' : 'mute')}>{notice.receipt?.muted ? (zh ? '恢复提醒' : 'Resume alerts') : (zh ? '静默此版本 24 小时' : 'Mute this version for 24 hours')}</button></div>{error && <p role="alert">{error}</p>}</div>
}
