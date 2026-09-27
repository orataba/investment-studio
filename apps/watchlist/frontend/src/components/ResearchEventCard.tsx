import { useEffect, useRef, useState } from 'react'
import { getResearchEvents, pinResearchEvent, type AskResearchAssistant, type ResearchEventsResponse, type ResearchUpdate } from '../lib/researchDossierApi'
import { announceResearchPublication } from '../lib/researchUpdates'
import { useStudioAccount } from './AccountBoundary'
import { SourceList, dateLabel } from './ResearchEvidence'
import { SavedFigure } from './ResearchModules'
import ResearchUpdateCard from './ResearchUpdateCard'
import ResearchEntryActions from './ResearchEntryActions'
import ResearchReadingAside from './ResearchReadingAside'
import InfoHint from '../../../../../packages/ui/src/InfoHint'
import ResearchLoading from '../../../../../packages/ui/src/WorkspaceSkeleton'

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
  return state.event ? <ResearchEventCard update={state.event} initiallyOpen /> : state.error ? <p role="alert">{state.error}</p> : <ResearchLoading />
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
    {loading && !data && <ResearchLoading />}
    {data?.events.map(record => <ResearchUpdateCard key={record.update_id} update={record} onAskAssistant={onAskAssistant} compact />)}
    {data?.has_more && data.next_offset !== null && <button type="button" disabled={loading} onClick={() => setOffset(data.next_offset!)}>加载更多版本</button>}
  </div>
}

export default function ResearchEventCard({ update, onAskAssistant, initiallyOpen = false }: { update: ResearchUpdate; onAskAssistant?: AskResearchAssistant; initiallyOpen?: boolean }) {
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
  const importance = update.importance_score
  const scored = typeof importance === 'number' && Number.isInteger(importance) && importance >= 1 && importance <= 5
  const details = <div className="research-event-details">
    <section><h5>发生了什么</h5><p translate="no">{update.body || '事实内容尚待核实。'}</p>
      <p className="sector-research-note">事件发生 {dateLabel(update.occurred_at)} · 信息发布 {dateLabel(update.published_at)} · 记录 {dateLabel(update.recorded_at)}</p>
    </section>
    {update.impact_analysis && <section><h5>影响与应对条件</h5><p translate="no">{update.impact_analysis}</p>{update.action_condition && <p translate="no">{update.action_condition}</p>}</section>}
    <section><h5>公开市场怎么看</h5>{views.length ? views.map((view, index) => <article className="research-market-view" key={index}><p className="sector-research-note"><span translate="no">{view.publisher}</span> · {dateLabel(view.published_at)}</p><p translate="no">{view.view}</p><SourceList instrumentId={reference.instrument_id} versionId={versionId} sources={sources.filter(source => view.source_ids.includes(source.source_id))} /></article>) : <p className="sector-research-note">未取得足够的公开观点材料，不能据此判断市场共识。</p>}</section>
    <section><h5>市场反应</h5>
      <p className="sector-research-note">{reaction?.status === 'pending' ? '事件窗口尚待观察。' : reaction?.status === 'unavailable' || !reaction ? '暂无可比事件窗口数据。' : reaction.status === 'partial' ? '事件窗口数据部分可用。' : '已留存事件窗口数据。'}</p>
      {reaction?.explanation && <p translate="no">{reaction.explanation}</p>}
      {sources.filter(source => reaction?.figure_source_ids.includes(source.source_id)).map(source => <SavedFigure key={source.source_id} instrumentId={reference.instrument_id} eventVersionId={versionId} source={source} />)}
    </section>
    <section><h5>接下来观察什么</h5><p translate="no">{update.next_check || update.follow_up_reason || '未安排新的观察节点。'}</p>
      {(update.follow_up_until || update.next_observation_on) && <p className="sector-research-note">{update.follow_up_until && <>跟进期限 {update.follow_up_until}</>}{update.next_observation_on && <> · 下一观察日期 {update.next_observation_on}</>}</p>}
      {canWrite && reference.event_case_id && versionId && <button type="button" disabled={saving} onClick={() => void togglePin()}>{pinned ? '取消固定' : '固定跟进'}</button>}
      {error && <p role="alert">{error}</p>}
    </section>
    <section><h5>来源</h5><SourceList instrumentId={reference.instrument_id} versionId={versionId} sources={sources} /></section>
  </div>
  return <article id={update.event_key ? `research-event-${encodeURIComponent(update.event_key)}` : undefined} className="research-event-card">
    <header className="research-event-title-row">
      <h4 translate="no">{update.title}</h4>
      <div className="research-event-tags">
        <span className={`research-importance${importance === 5 ? ' major' : ''}`} aria-label={scored ? `重要性 ${importance}/5` : '重要性 未评估'}>{scored ? <span aria-hidden="true">{'★'.repeat(importance)}<span className="research-star-empty">{'☆'.repeat(5 - importance)}</span></span> : '重要性 未评估'}</span>
        {update.importance_reason && <InfoHint label="重要性依据" detail={update.importance_reason} />}
        <span className={`research-status-tag${update.follow_up === 'watch' ? ' active' : ''}`}>{update.follow_up === 'watch' ? '跟进中' : '不再主动跟进'}</span>
        {update.information_type === 'rumor' && <span className="research-status-tag warning">待核实消息</span>}
        {update.information_type === 'opinion' && <span className="research-status-tag">公开观点</span>}
        {pinned && <span className="research-status-tag">已固定</span>}
        {update.follow_up_review_status === 'due' && <span className="research-status-tag warning">到期待复核</span>}
      </div>
    </header>
    <time className="research-event-inline-date" dateTime={update.timeline_date || undefined}>{update.timeline_date || '日期待核实'}</time>
    {update.body && <p className="research-event-summary" translate="no">{update.body}</p>}
    {update.impact_analysis && update.impact_analysis !== update.body && <p className="research-event-impact"><span>投资影响</span><span translate="no">{update.impact_analysis}</span></p>}
    {(update.risk_assessment?.status === 'pending' || update.risk_assessment?.status === 'failed') && <p className="research-event-risk-status">{update.risk_assessment.status === 'pending' ? '已提交／待风控复核' : '风控复核未完成'}</p>}
    <div className="research-event-reading-tools">
      {!initiallyOpen && <ResearchReadingAside label="查看影响、公开观点与依据" title={update.title}>{details}</ResearchReadingAside>}
      {update.event_key && <ResearchReadingAside label="研究修订历史" title={`${update.title} · 研究修订历史`} onOpenChange={setRecordOpen}>{recordOpen && <EventHistory update={update} onAskAssistant={onAskAssistant} />}</ResearchReadingAside>}
      {update.theme_ids.map(id => <a className="research-support-link" key={id} href={`#research-theme-${encodeURIComponent(id)}`}>关联主题</a>)}
    </div>
    {initiallyOpen && details}
    <ResearchEntryActions update={update} onAskAssistant={onAskAssistant} />
  </article>
}
