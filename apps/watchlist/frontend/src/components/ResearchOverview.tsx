import type { AskResearchAssistant, ResearchDossier } from '../lib/researchDossierApi'

/** Executive context for the same report; the full analysis follows immediately below. */
export default function ResearchOverview({ instrumentId, dossier, onAskAssistant }: {
  instrumentId: string; dossier: ResearchDossier; onAskAssistant?: AskResearchAssistant
}) {
  const notebook = dossier.notebook
  if (!notebook) return null
  return <div className="research-overview-body">
    {(notebook.key_drivers.length > 0 || notebook.next_research.length > 0) && <section className="research-overview-thesis" aria-label="投资逻辑与验证">
      {notebook.key_drivers.length > 0 && <div><h3>投资逻辑</h3><ul>{notebook.key_drivers.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul></div>}
      {notebook.next_research.length > 0 && <div><h3>下一步验证</h3><ul>{notebook.next_research.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul></div>}
    </section>}
    {notebook.important_changes.length > 0 && <details className="research-overview-changes"><summary>本次研究要点<span>更新于 {notebook.updated_at?.slice(0, 10) || notebook.checked_at?.slice(0, 10) || '未标注'}</span></summary>
      <ul>{notebook.important_changes.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul>
      {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请对照这份研究的前次版本，解释本次重要变化及其投资含义。区分新披露事实、首次整理与判断变化。', { instrument_id: instrumentId, notebook_version_id: notebook.version_id })}>讨论本次研究</button>}
    </details>}
  </div>
}
