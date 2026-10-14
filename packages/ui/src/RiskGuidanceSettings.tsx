import { useEffect, useState } from 'react'
import { useLanguage } from './i18n'
import type { RiskRequest } from './instrumentRisk'
import { researchStamp } from './ResearchRunStatus'

type GuidanceVersion = { text: string; revision: number; content_revision?: number; updated_at: string | null; author?: { display_name?: string; reason?: string } | string | null; reason?: string }
type Guidance = GuidanceVersion & { versions: GuidanceVersion[]; instrument_id?: string }
type GuidanceResponse = { general: Guidance; instrument: Guidance | null; constraints: string[] }
type GuidanceSettingsProps = { request: RiskRequest; instrumentId?: string; scopeQuery?: string; canWrite: boolean }

function GuidanceEditor({ scope, value, canWrite, onSave }: { scope: 'general' | 'instrument'; value: Guidance; canWrite: boolean; onSave: (scope: 'general' | 'instrument', text: string, reason: string, revision: number) => Promise<void> }) {
  const zh = useLanguage().language === 'zh-Hans'
  const [text, setText] = useState(value.text)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <form className="risk-guidance-editor" onSubmit={async event => {
    event.preventDefault(); if (!canWrite || busy) return
    setBusy(true); setError('')
    try { await onSave(scope, text, reason, value.revision) } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)) } finally { setBusy(false) }
  }}>
    <h4>{scope === 'general' ? (zh ? '团队通用指导' : 'Team guidance') : (zh ? '标的专门指导' : 'Instrument guidance')}</h4>
    <p className="risk-officer-muted">{zh ? '版本' : 'Version'} {value.content_revision ?? value.revision} · {value.updated_at ? researchStamp(value.updated_at) : (zh ? '尚未保存' : 'Not saved')} · <span translate="no">{typeof value.author === 'string' ? value.author : value.author?.display_name}</span></p>
    <textarea aria-label={scope === 'general' ? (zh ? '团队通用指导' : 'Team guidance') : (zh ? '标的专门指导' : 'Instrument guidance')} rows={5} value={text} readOnly={!canWrite || busy} onChange={event => setText(event.target.value)} />
    {canWrite && <><label>{zh ? '修改原因' : 'Reason for change'}<input value={reason} onChange={event => setReason(event.target.value)} required disabled={busy} /></label><button disabled={busy || text === value.text || !reason.trim()}>{busy ? (zh ? '保存中…' : 'Saving…') : (zh ? '保存指导' : 'Save guidance')}</button></>}
    {error && <p role="alert">{error}</p>}
    {!!value.versions.length && <details><summary>{zh ? '修改记录与恢复' : 'History and restore'}</summary>{value.versions.map(version => <div key={version.revision} className="risk-guidance-version"><p>{zh ? '版本' : 'Version'} {version.revision} · {version.updated_at ? researchStamp(version.updated_at) : '—'} · {typeof version.author === 'string' ? version.author : version.author?.display_name}</p><p translate="no">{version.text || '—'}</p>{(version.reason || (typeof version.author === 'object' && version.author?.reason)) && <p translate="no">{version.reason || (typeof version.author === 'object' && version.author?.reason)}</p>}{canWrite && version.revision !== value.revision && <button type="button" onClick={() => { setText(version.text); setReason(zh ? `恢复版本 ${version.revision}` : `Restore version ${version.revision}`) }}>{zh ? '采用此版本内容' : 'Use this version'}</button>}</div>)}</details>}
  </form>
}

export default function RiskGuidanceSettings(props: GuidanceSettingsProps) {
  return <GuidanceSettingsSession key={JSON.stringify([props.instrumentId ?? null, props.scopeQuery ?? null])} {...props} />
}

function GuidanceSettingsSession({ request, instrumentId, scopeQuery, canWrite }: GuidanceSettingsProps) {
  const zh = useLanguage().language === 'zh-Hans'
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<GuidanceResponse | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [preview, setPreview] = useState<{ notice_items: Array<{ notice_id: string; title: string; body: string; level: string | null }>; cases: Array<{ case_id: string; title: string; body: string; selection: string; assessment_reason: string | null }>; usage_note: string } | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    request<GuidanceResponse>(`/risk/guidance${instrumentId ? `?instrument_id=${encodeURIComponent(instrumentId)}` : ''}`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setData(value); setError('') } }, failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : String(failure)) })
    return () => controller.abort()
  }, [request, instrumentId, open])
  return <details className="risk-guidance" onToggle={event => setOpen(event.currentTarget.open)}><summary>{zh ? '风险提示设置 · Agent 指导' : 'Risk alert settings · Agent guidance'}</summary>
    <p>{zh ? '指导用于后续分析的关注重点、筛选、分级与表达。保存不会立即重跑、修改指标规则或发送通知。' : 'Guidance controls focus, selection, priority and wording for future reviews. Saving does not run a review, change metric rules or send notifications.'}</p>
    <p className="risk-officer-muted">{zh ? '指标方法、阈值与恢复条件在风险监测中配置。' : 'Configure metric methods, thresholds and recovery conditions in Risk monitoring.'}</p>
    {error && <p role="alert">{error}</p>}
    {open && !data && !error && <p role="status">{zh ? '正在读取指导…' : 'Loading guidance…'}</p>}
    {data && <><button type="button" disabled={previewBusy} onClick={async () => { setPreviewBusy(true); try { setPreview(await request(`/risk/guidance/preview${scopeQuery || instrumentId ? `?${scopeQuery || `instrument_id=${encodeURIComponent(instrumentId!)}`}` : ''}`)); setError('') } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)) } finally { setPreviewBusy(false) } }}>{previewBusy ? (zh ? '正在读取案例…' : 'Loading cases…') : (zh ? '已有案例对照' : 'Compare retained cases')}</button><p className="risk-officer-muted">{zh ? '展示上次正式筛选，未按未保存指导调用模型；不修改正式状态或通知。' : 'Shows the last formal selection; unsaved guidance is not sent to a model. Formal state and notifications remain unchanged.'}</p>
      {preview && <details open><summary>{zh ? '已有案例与筛选结果' : 'Retained cases and selection'}</summary><p translate="no">{preview.usage_note}</p>{preview.notice_items.map(item => <p key={item.notice_id} translate="no"><strong>{item.title}</strong> {item.body}</p>)}{preview.cases.map(item => <details key={item.case_id}><summary><span translate="no">{item.title}</span> · {({ selected: zh ? '已入选' : 'Selected', not_selected: zh ? '未入选' : 'Not selected', not_assessed: zh ? '未评估' : 'Not assessed' } as Record<string, string>)[item.selection] || item.selection}</summary><p translate="no">{item.body}</p><p translate="no">{item.assessment_reason}</p></details>)}</details>}
      {[...data.constraints].map(constraint => <p key={constraint} className="risk-officer-muted">{constraint}</p>)}<div className="risk-guidance-layers">{(['general', ...(instrumentId ? ['instrument'] : [])] as Array<'general' | 'instrument'>).map(scope => {
      const value = data[scope]
      return value && <GuidanceEditor key={JSON.stringify([scope, value.revision, value.text])} scope={scope} value={value} canWrite={canWrite} onSave={async (scope, text, reason, revision) => {
        const updated = await request<GuidanceResponse>('/risk/guidance', { method: 'PUT', body: JSON.stringify({ scope, ...(instrumentId ? { instrument_id: instrumentId } : {}), text, expected_revision: revision, reason }) })
        setData(updated); setMessage(zh ? '指导已保存，将作用于后续分析。' : 'Guidance saved for future reviews.')
      }} />
    })}</div><p role="status">{message}</p></>}
  </details>
}
