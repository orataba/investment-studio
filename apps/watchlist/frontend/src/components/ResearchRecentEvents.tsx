import { useState } from 'react'
import type { AskResearchAssistant } from '../lib/researchDossierApi'
import { useResearchActivity } from '../lib/useResearchActivity'
import ResearchUpdateCard from './ResearchUpdateCard'

export default function ResearchRecentEvents({ instrumentId, reviewRunId, reviewStatus, onAskAssistant }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant
}) {
  const { data, error } = useResearchActivity(instrumentId, reviewRunId, reviewStatus)
  const [showAll, setShowAll] = useState(false)
  const events = (data?.recent_events || []).filter(update => update.kind === 'event' && !update.superseded && !update.withdrawn)
    .sort((a, b) => Date.parse(b.recorded_at) - Date.parse(a.recorded_at))
  return <section className="research-recent-events" aria-label="近期重要事项">
    <div className="research-dossier-section-heading"><h2>近期重要事项</h2><span className="sector-research-note">已关联主题的进展见主题时间线</span></div>
    {error && <p role="alert">事项暂时无法读取：{error}</p>}
    {!data && !error && <p className="sector-research-note" role="status">Loading</p>}
    {data && !events.length && <p className="sector-research-note">暂无新的独立重要事项。</p>}
    {(showAll ? events : events.slice(0, 5)).map(update => <ResearchUpdateCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} />)}
    {events.length > 5 && <button className="research-more-events" type="button" aria-expanded={showAll} onClick={() => setShowAll(value => !value)}>{showAll ? '收起较早事项' : `查看较早事项 · ${events.length - 5}`}</button>}
  </section>
}
