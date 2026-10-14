import { useEffect, useState } from 'react'
import { getResearchEvents, type AskResearchAssistant, type ResearchEventsResponse, type ResearchEventCategory, type ResearchEventScope, type ResearchUpdate } from '../lib/researchDossierApi'
import { dateLabel } from './ResearchEvidence'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
import ResearchEventCard, { researchCategoryLabels } from './ResearchEventCard'
import ResearchLoading from '../../../../../packages/ui/src/WorkspaceSkeleton'
import InfoHint from '../../../../../packages/ui/src/InfoHint'

export default function ResearchRecentEvents({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, themeNames = {}, initialScope = 'recent', hideWhenEmpty = false }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; themeNames?: Record<string, string>; initialScope?: ResearchEventScope; hideWhenEmpty?: boolean
}) {
  const [scope, setScope] = useState<ResearchEventScope>(initialScope)
  const [category, setCategory] = useState<ResearchEventCategory | ''>('')
  const [offset, setOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [snapshot, setSnapshot] = useState<{ instrumentId: string; scope: ResearchEventScope; category: ResearchEventCategory | ''; data: ResearchEventsResponse } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const data = snapshot?.instrumentId === instrumentId && snapshot.scope === scope && snapshot.category === category ? snapshot.data : null
  useEffect(() => { setScope(initialScope); setCategory(''); setOffset(0) }, [instrumentId, initialScope])
  useEffect(() => { setOffset(0) }, [reviewRunId, reviewStatus])
  useEffect(() => {
    const updated = (event: Event) => { if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) { setOffset(0); setRefresh(value => value + 1) } }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId])
  useEffect(() => {
    const controller = new AbortController()
    setError(''); setLoading(true)
    void (category ? getResearchEvents(instrumentId, scope, controller.signal, offset, undefined, false, category) : getResearchEvents(instrumentId, scope, controller.signal, offset)).then(result => {
      if (!controller.signal.aborted) setSnapshot(previous => {
        const priorPage = offset && previous?.instrumentId === instrumentId && previous.scope === scope && previous.category === category ? previous.data : null
        return { instrumentId, scope, category, data: { ...result,
          events: priorPage ? [...new Map([...priorPage.events, ...result.events].map(event => [event.update_id, event])).values()] : result.events,
          late_arrivals: priorPage ? priorPage.late_arrivals : result.late_arrivals,
          late_arrival_count: result.late_arrival_count ?? priorPage?.late_arrival_count,
        } }
      })
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '事件读取失败') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [instrumentId, scope, category, offset, refresh, reviewRunId, reviewStatus])
  const retainedAt = data ? [...data.events, ...data.late_arrivals].map(item => item.checked_at || item.recorded_at).filter(value => value && Number.isFinite(Date.parse(value))).sort((a, b) => Date.parse(a) - Date.parse(b)).slice(-1)[0] : null
  const lateCount = data?.late_arrival_count ?? data?.late_arrivals.length ?? 0
  const groups = new Map<string, ResearchUpdate[]>()
  for (const update of data?.events || []) { const day = update.timeline_date || '日期待核实'; groups.set(day, [...(groups.get(day) || []), update]) }
  if (hideWhenEmpty && data && !data.events.length && !error && !loading && scope === initialScope) return null
  return <section className="research-recent-events" aria-label="标的动态" aria-busy={loading}>
    <div className="research-dossier-section-heading"><div className="research-section-title"><h2>标的动态</h2><InfoHint label="重要进展范围" detail="公司、行业与底层资产的新闻、公告、观点及会议进展；持续跟进不受日期限制。时间线按进展、发生或披露日期归组（UTC），未知发生时间保持未知。" /></div><div className="research-event-filters" aria-label="事件范围">
      {([['recent', '近 7 天'], ['watch', '仍在跟进'], ['history', '历史']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={scope === value} onClick={() => { setScope(value); setOffset(0) }}>{label}</button>)}
    </div></div>
    <div className="research-update-controls"><label>信息类别<select aria-label="信息类别" value={category} onChange={event => { setCategory(event.target.value as ResearchEventCategory | ''); setOffset(0) }}><option value="">全部类别</option>{Object.entries(researchCategoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请分析该标的的${category ? researchCategoryLabels[category] : '重要动态'}，核实事实、判断投资影响，并识别需要持续跟进的事项。`, { instrument_id: instrumentId })}>讨论标的动态</button>}</div>
    {error && <aside className="research-retained-notice" role="alert"><strong>{data ? '动态更新暂不可用，保留上次有效内容' : '标的动态暂时无法读取'}</strong>{data && <p>以下为已取得的有效版本，尚未确认是否有更新。{retainedAt && <> 最近检查或记录 <time dateTime={retainedAt}>{dateLabel(retainedAt)}</time></>}</p>}<button type="button" disabled={loading} onClick={() => setRefresh(value => value + 1)}>重试读取</button><details><summary>技术详情</summary><p translate="no">{error}</p></details></aside>}
    {loading && !data && <ResearchLoading />}
    {data && !data.events.length && <p className="sector-research-note">{scope === 'recent' ? '近 7 天没有已记录的重要事件；这不代表没有风险。' : scope === 'watch' ? '暂无主动跟进的事件。' : '尚无事件记录。'}</p>}
    <div className="research-event-timeline">{[...groups].map(([day, events]) => <section className="research-event-day" key={day} aria-label={day}>
      <div className="research-event-date"><time dateTime={/^\d{4}-\d{2}-\d{2}$/.test(day) ? day : undefined}><span>{/^\d{4}-\d{2}-\d{2}$/.test(day) ? day.slice(5).replace('-', '.') : day}</span>{/^\d{4}-\d{2}-\d{2}$/.test(day) && <small>{day.slice(0, 4)}</small>}</time><span className="research-event-day-count">{events.length}</span></div>
      <div className="research-event-day-entries">{events.map(update => <ResearchEventCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} themeNames={themeNames} />)}</div>
    </section>)}</div>
    {!!data?.late_arrivals.length && <details className="research-event-backfills"><summary>补录与日期待核实 · {lateCount}</summary>{data.late_arrivals.map(update => <ResearchEventCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} themeNames={themeNames} />)}
      {lateCount > data.late_arrivals.length && <p className="sector-research-note">已显示 {data.late_arrivals.length} / {lateCount} 项，其余记录保留在历史中。 <button type="button" className="research-support-link" onClick={() => { setScope('history'); setOffset(0) }}>查看历史记录</button></p>}
    </details>}
    {data?.has_more && data.next_offset !== null && <button type="button" disabled={loading} onClick={() => setOffset(data.next_offset!)}>加载更多事件</button>}
  </section>
}
