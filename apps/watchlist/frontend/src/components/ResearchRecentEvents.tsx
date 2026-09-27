import { useEffect, useState } from 'react'
import { getResearchEvents, type AskResearchAssistant, type ResearchEventsResponse, type ResearchEventScope, type ResearchUpdate } from '../lib/researchDossierApi'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
import ResearchEventCard from './ResearchEventCard'
import ResearchLoading from '../../../../../packages/ui/src/WorkspaceSkeleton'
import InfoHint from '../../../../../packages/ui/src/InfoHint'

export default function ResearchRecentEvents({ instrumentId, reviewRunId, reviewStatus, onAskAssistant }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant
}) {
  const [scope, setScope] = useState<ResearchEventScope>('recent')
  const [offset, setOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [snapshot, setSnapshot] = useState<{ instrumentId: string; scope: ResearchEventScope; data: ResearchEventsResponse } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const data = snapshot?.instrumentId === instrumentId && snapshot.scope === scope ? snapshot.data : null
  useEffect(() => { setScope('recent'); setOffset(0) }, [instrumentId])
  useEffect(() => { setOffset(0) }, [reviewRunId, reviewStatus])
  useEffect(() => {
    const updated = (event: Event) => { if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) { setOffset(0); setRefresh(value => value + 1) } }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId])
  useEffect(() => {
    const controller = new AbortController()
    setError(''); setLoading(true)
    void getResearchEvents(instrumentId, scope, controller.signal, offset).then(result => {
      if (!controller.signal.aborted) setSnapshot(previous => {
        const priorPage = offset && previous?.instrumentId === instrumentId && previous.scope === scope ? previous.data : null
        return { instrumentId, scope, data: { ...result,
          events: priorPage ? [...new Map([...priorPage.events, ...result.events].map(event => [event.update_id, event])).values()] : result.events,
          late_arrivals: priorPage ? priorPage.late_arrivals : result.late_arrivals,
          late_arrival_count: result.late_arrival_count ?? priorPage?.late_arrival_count,
        } }
      })
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '事件读取失败') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [instrumentId, scope, offset, refresh, reviewRunId, reviewStatus])
  const lateCount = data?.late_arrival_count ?? data?.late_arrivals.length ?? 0
  const groups = new Map<string, ResearchUpdate[]>()
  for (const update of data?.events || []) { const day = update.timeline_date || '日期待核实'; groups.set(day, [...(groups.get(day) || []), update]) }
  return <section className="research-recent-events" aria-label="近期重要进展" aria-busy={loading}>
    <div className="research-dossier-section-heading"><div className="research-section-title"><h2>近期重要进展</h2><InfoHint label="重要进展范围" detail="近 7 天的重要事件、基本面变化与市场信号，按信息日期排列；持续跟进及补录记录单独保留。" /></div><div className="research-event-filters" aria-label="事件范围">
      {([['recent', '近 7 天'], ['watch', '仍在跟进'], ['history', '历史']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={scope === value} onClick={() => { setScope(value); setOffset(0) }}>{label}</button>)}
    </div></div>
    {error && <p role="alert">事件暂时无法读取：{error}{data && '。以下保留上次有效内容。'}</p>}
    {loading && !data && <ResearchLoading />}
    {data && !data.events.length && <p className="sector-research-note">{scope === 'recent' ? '近 7 天没有已记录的重要事件；这不代表没有风险。' : scope === 'watch' ? '暂无主动跟进的事件。' : '尚无事件记录。'}</p>}
    <div className="research-event-timeline">{[...groups].map(([day, events]) => <section className="research-event-day" key={day} aria-label={day}>
      <div className="research-event-date"><time dateTime={/^\d{4}-\d{2}-\d{2}$/.test(day) ? day : undefined}><span>{/^\d{4}-\d{2}-\d{2}$/.test(day) ? day.slice(5).replace('-', '.') : day}</span>{/^\d{4}-\d{2}-\d{2}$/.test(day) && <small>{day.slice(0, 4)}</small>}</time><span className="research-event-day-count">{events.length}</span></div>
      <div className="research-event-day-entries">{events.map(update => <ResearchEventCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} />)}</div>
    </section>)}</div>
    {!!data?.late_arrivals.length && <details className="research-event-backfills"><summary>补录与日期待核实 · {lateCount}</summary>{data.late_arrivals.map(update => <ResearchEventCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} />)}
      {lateCount > data.late_arrivals.length && <p className="sector-research-note">已显示 {data.late_arrivals.length} / {lateCount} 项，其余记录保留在历史中。 <button type="button" className="research-support-link" onClick={() => { setScope('history'); setOffset(0) }}>查看历史记录</button></p>}
    </details>}
    {data?.has_more && data.next_offset !== null && <button type="button" disabled={loading} onClick={() => setOffset(data.next_offset!)}>加载更多事件</button>}
  </section>
}
