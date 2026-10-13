import type { AskResearchAssistant, ResearchDossier } from '../lib/researchDossierApi'
import InvestmentResearchState from './InvestmentResearchState'
import { SourceList } from './ResearchEvidence'

export default function ResearchOverview({ instrumentId, dossier, onAskAssistant }: {
  instrumentId: string; dossier: ResearchDossier; onAskAssistant?: AskResearchAssistant
}) {
  const notebook = dossier.notebook
  const hasJudgment = Boolean(notebook?.investment_view || (notebook?.decision_brief && !notebook.decision_brief.needs_review))
  return <section className="research-overview-body" aria-label="总览">
    <div className="research-dossier-section-heading"><h2>总览</h2>{onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请综合标的动态、重要研究主题、量化指标和我的投资观点，复核当前投资建议及最重要的依据。资料不足时明确无法判断。', { instrument_id: instrumentId, notebook_version_id: notebook?.version_id })}>讨论总览</button>}</div>
    {hasJudgment && notebook ? <InvestmentResearchState instrumentId={instrumentId} notebook={notebook} sources={ids => <SourceList instrumentId={instrumentId} versionId={notebook.version_id} sources={(notebook.sources || []).filter(source => ids.includes(source.source_id))} />} onAskAssistant={onAskAssistant} /> : <div className="research-empty-judgment"><p><strong>研究基线待建立</strong> · 尚未形成投资建议。</p><p>可先查看标的动态、保留的研究主题和量化数据；历史记录不代表本轮已复核的判断。</p></div>}
  </section>
}
