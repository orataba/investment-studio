import { useState } from 'react'
import type { AskResearchAssistant } from '../lib/researchDossierApi'
import { useResearchActivity } from '../lib/useResearchActivity'
import ResearchUpdateCard from './ResearchUpdateCard'

export default function ResearchRecentEvents({ instrumentId, reviewRunId, reviewStatus, onAskAssistant }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant
}) {
  const { data, error } = useResearchActivity(instrumentId, reviewRunId, reviewStatus)
  const [showAll, setShowAll] = useState(false)
  const events = [...new Map([...(data?.updates || []), ...(data?.recent_events || [])].map(update => [update.update_id, update])).values()]
    .filter(update => ['event', 'schedule'].includes(update.kind) && !update.superseded && !update.withdrawn && !['organized', 'citation_corrected'].includes(update.change || '') && update.status !== 'cancelled')
    .sort((a, b) => ({ immediate: 2, review_soon: 1, monitor: 0 }[b.urgency || 'monitor'] - { immediate: 2, review_soon: 1, monitor: 0 }[a.urgency || 'monitor'])
      || ({ major: 2, material: 1, limited: 0 }[b.impact_level || 'limited'] - { major: 2, material: 1, limited: 0 }[a.impact_level || 'limited'])
      || Date.parse(b.recorded_at) - Date.parse(a.recorded_at))
  if (data && !events.length && !error) return null
  return <section className="research-recent-events" aria-label="近期重要事项">
    <div className="research-dossier-section-heading"><h3>近期重要事项</h3></div>
    {error && <p role="alert">事项暂时无法读取：{error}</p>}
    {!data && !error && <p className="sector-research-note" role="status">正在读取近期事项…</p>}
    {(showAll ? events : events.slice(0, 5)).map(update => <ResearchUpdateCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} readable />)}
    {events.length > 5 && <button className="research-more-events" type="button" aria-expanded={showAll} onClick={() => setShowAll(value => !value)}>{showAll ? '收起较早事项' : `查看较早事项 · ${events.length - 5}`}</button>}
  </section>
}
