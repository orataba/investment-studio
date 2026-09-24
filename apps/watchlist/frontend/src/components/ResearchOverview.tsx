import type { AskResearchAssistant, ResearchDossier } from '../lib/researchDossierApi'
import { dateLabel, SourceList } from './ResearchEvidence'
import ResearchReadingAside from './ResearchReadingAside'

/** Executive context for the same report; the full analysis follows immediately below. */
export default function ResearchOverview({ instrumentId, dossier, onAskAssistant }: {
  instrumentId: string; dossier: ResearchDossier; onAskAssistant?: AskResearchAssistant
}) {
  const notebook = dossier.notebook
  if (!notebook) return null
  const changes = notebook.changes || []
  return <div className="research-overview-body">
    {(changes.length > 0 || notebook.important_changes.length > 0) && <section className="research-overview-changes" aria-label="变化与投资影响">
      <header className="research-section-heading"><h2>变化与投资影响</h2>{!changes.length && <span>研究修订于 {dateLabel(notebook.updated_at || notebook.checked_at)}</span>}</header>
      {changes.length > 0 ? <div className="research-change-list">{changes.map(change => <article className="research-change" key={change.key}>
        <header><h3 translate="no">{change.title}</h3>{change.updated_at && <time dateTime={change.updated_at}>{dateLabel(change.updated_at)}</time>}</header>
        {(change.before || change.after) && <dl className="research-change-comparison">{change.before && <div><dt>此前认识{change.baseline_as_of && <span> · {dateLabel(change.baseline_as_of)}</span>}</dt><dd translate="no">{change.before}</dd></div>}{change.after && <div><dt>现在的变化</dt><dd translate="no">{change.after}</dd></div>}</dl>}
        {change.mechanism && <p translate="no">{change.mechanism}</p>}
        {change.decision_implication && <p className="research-change-implication"><strong>投资含义</strong><span translate="no">{change.decision_implication}</span></p>}
        {change.condition && <p className="research-change-condition"><strong>继续观察</strong><span translate="no">{change.condition}</span></p>}
        {change.source_ids.length > 0 && <ResearchReadingAside label="查看变化依据" title={change.title}><SourceList instrumentId={instrumentId} versionId={notebook.version_id} sources={(notebook.sources || []).filter(source => change.source_ids.includes(source.source_id))} /></ResearchReadingAside>}
      </article>)}</div> : <ul className="research-change-notes">{notebook.important_changes.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul>}
      {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请对照这份研究的前次版本，解释本次重要变化及其投资含义。区分新披露事实、首次整理与判断变化。', { instrument_id: instrumentId, notebook_version_id: notebook.version_id })}>讨论本次研究</button>}
    </section>}
    {(notebook.key_drivers.length > 0 || notebook.next_research.length > 0) && <section className="research-overview-thesis" aria-label="投资逻辑与验证">
      {notebook.key_drivers.length > 0 && <div><h3>投资逻辑</h3><ul>{notebook.key_drivers.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul></div>}
      {notebook.next_research.length > 0 && <div><h3>下一步验证</h3><ul>{notebook.next_research.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul></div>}
    </section>}
  </div>
}
