import { useEffect, useRef, useState } from 'react'
import { getResearchEvents, pinResearchEvent, type AskResearchAssistant, type ResearchEventsResponse, type ResearchUpdate } from '../lib/researchDossierApi'
import { announceResearchPublication } from '../lib/researchUpdates'
import { useStudioAccount } from './AccountBoundary'
import { SourceList, dateLabel } from './ResearchEvidence'
import { SavedFigure } from './ResearchModules'
import ResearchUpdateCard from './ResearchUpdateCard'

export function ReferencedResearchEvent({ instrumentId, eventKey }: { instrumentId: string; eventKey: string }) {
  const [state, setState] = useState<{ event?: ResearchUpdate; error?: string }>({})
  useEffect(() => {
    const controller = new AbortController()
    setState({})
    void getResearchEvents(instrumentId, 'history', controller.signal, 0, eventKey).then(result => {
      if (!controller.signal.aborted) setState(result.events[0] ? { event: result.events[0] } : { error: '对应事件暂不可用。' })
    }).catch(reason => { if (!controller.signal.aborted) setState({ error: reason instanceof Error ? reason.message : '事件读取失败。' }) })
    return () => controller.abort()
  }, [instrumentId, eventKey])
  return state.event ? <ResearchEventCard update={state.event} initiallyOpen /> : <p role={state.error ? 'alert' : 'status'}>{state.error || 'Loading'}</p>
}

function EventHistory({ update, onAskAssistant }: { update: ResearchUpdate; onAskAssistant?: AskResearchAssistant }) {
  const [data, setData] = useState<ResearchEventsResponse | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    setError(''); setLoading(true)
    void getResearchEvents(update.reference.instrument_id, 'history', controller.signal, offset, update.event_key, true).then(value => {
      if (!controller.signal.aborted) setData(previous => ({ ...value, events: offset && previous ? [...previous.events, ...value.events] : value.events }))
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '事件历史读取失败。') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [update.reference.instrument_id, update.event_key, offset])
  return <div className="research-event-history" aria-busy={loading}>
    {error && <p role="alert">{error}</p>}
    {loading && !data && <p role="status">Loading</p>}
    {data?.events.map(record => <ResearchUpdateCard key={record.update_id} update={record} onAskAssistant={onAskAssistant} compact />)}
    {data?.has_more && data.next_offset !== null && <button type="button" disabled={loading} onClick={() => setOffset(data.next_offset!)}>加载更多版本</button>}
  </div>
}

export default function ResearchEventCard({ update, onAskAssistant, initiallyOpen = false }: { update: ResearchUpdate; onAskAssistant?: AskResearchAssistant; initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  const [recordOpen, setRecordOpen] = useState(false)
  const [pinState, setPinState] = useState<{ updateId: string; version: string; pinned: boolean } | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const reference = update.reference
  const sources = update.sources.map((source, index) => ({ ...source, source_id: source.source_id || `${update.update_id}:${index}` }))
  const versionId = pinState?.updateId === update.update_id ? pinState.version : reference.event_version_id
  const pinned = pinState?.updateId === update.update_id ? pinState.pinned : update.follow_up_pinned
  const views = update.market_views || []
  const reaction = update.market_reaction
  async function togglePin() {
    if (!canWrite || saving || !reference.event_case_id || !versionId) return
    setSaving(true); setError('')
    try {
      const result = await pinResearchEvent(reference.event_case_id, versionId, !pinned)
      if (mounted.current) setPinState({ updateId: update.update_id, version: result.event_version_id, pinned: result.follow_up_pinned })
      announceResearchPublication([reference.instrument_id])
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : '固定状态保存失败。') }
    finally { if (mounted.current) setSaving(false) }
  }
  return <article id={update.event_key ? `research-event-${encodeURIComponent(update.event_key)}` : undefined} className="research-event-card">
    <div className="research-event-meta">
      <span>{update.timeline_date || '日期待核实'}</span>
      <span className={update.importance_score === 5 ? 'research-impact-risk' : ''}>重要性 {update.importance_score ?? '未评估'}</span>
      {update.information_type === 'rumor' ? <span className="research-impact-risk">待核实消息</span> : update.information_type === 'opinion' ? <span>公开观点</span> : <span>{update.information_type === 'fact' ? '事实信息' : '信息性质待核实'}</span>}
      <span>{update.follow_up === 'watch' ? '跟进中' : '不再主动跟进'}</span>
      {pinned && <span>PM 已固定</span>}
      {update.follow_up_review_status === 'due' && <span className="research-impact-risk">到期待复核</span>}
      {update.late_arrival && <span>补录</span>}
      {update.risk_assessment?.status === 'pending' && <span>已提交／待风控复核</span>}
      {update.risk_assessment?.status === 'failed' && <span className="research-impact-risk">风控复核未完成</span>}
    </div>
    <h4 translate="no">{update.title}</h4>
    {(update.impact_analysis || update.body) && <p className="research-event-summary" translate="no">{update.impact_analysis || update.body}</p>}
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>查看影响、公开观点与依据</summary>
      {open && <div className="research-event-details">
        <section><h5>发生了什么</h5><p translate="no">{update.body || '事实内容尚待核实。'}</p>
          <p className="sector-research-note">事件发生 {dateLabel(update.occurred_at)} · 信息发布 {dateLabel(update.published_at)} · 记录 {dateLabel(update.recorded_at)}</p>
          {update.importance_reason && <p className="sector-research-note">重要性依据：<span translate="no">{update.importance_reason}</span></p>}
        </section>
        <section><h5>可能有哪些影响</h5><p translate="no">{update.impact_analysis || '尚未形成单独的影响分析。'}</p>{update.action_condition && <p translate="no">{update.action_condition}</p>}</section>
        <section><h5>公开市场怎么看</h5>{views.length ? views.map((view, index) => <article key={index}><p className="sector-research-note"><span translate="no">{view.publisher}</span> · {dateLabel(view.published_at)}</p><p translate="no">{view.view}</p><SourceList instrumentId={reference.instrument_id} versionId={versionId} sources={sources.filter(source => view.source_ids.includes(source.source_id))} /></article>) : <p className="sector-research-note">未取得足够的公开观点材料，不能据此判断市场共识。</p>}</section>
        <section><h5>市场反应</h5>
          <p className="sector-research-note">{reaction?.status === 'pending' ? '事件窗口尚待观察。' : reaction?.status === 'unavailable' || !reaction ? '暂无可比事件窗口数据。' : reaction.status === 'partial' ? '事件窗口数据部分可用。' : '已留存事件窗口数据。'}</p>
          {reaction?.explanation && <p translate="no">{reaction.explanation}</p>}
          {sources.filter(source => reaction?.figure_source_ids.includes(source.source_id)).map(source => <SavedFigure key={source.source_id} instrumentId={reference.instrument_id} eventVersionId={versionId} source={source} />)}
        </section>
        <section><h5>接下来观察什么</h5><p translate="no">{update.next_check || update.follow_up_reason || '未安排新的观察节点。'}</p>
          {update.follow_up_until && <p className="sector-research-note">跟进期限 {update.follow_up_until}</p>}
          {update.next_observation_on && <p className="sector-research-note">下一观察日期 {update.next_observation_on}</p>}
          {update.follow_up_reason && update.follow_up_reason !== update.next_check && <p className="sector-research-note" translate="no">{update.follow_up_reason}</p>}
          {canWrite && reference.event_case_id && versionId && <button type="button" disabled={saving} onClick={() => void togglePin()}>{pinned ? '取消固定' : '固定跟进'}</button>}
          {error && <p role="alert">{error}</p>}
        </section>
        <section><h5>来源</h5><SourceList instrumentId={reference.instrument_id} versionId={versionId} sources={sources} /></section>
        <details className="research-event-record" onToggle={event => setRecordOpen(event.currentTarget.open)}><summary>研究历史与 PM 操作</summary>{recordOpen && (update.event_key ? <EventHistory update={update} onAskAssistant={onAskAssistant} /> : <ResearchUpdateCard update={update} onAskAssistant={onAskAssistant} />)}</details>
      </div>}
    </details>
  </article>
}
