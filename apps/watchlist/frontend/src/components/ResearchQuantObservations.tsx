import ResearchCurrentNumbers from './ResearchCurrentNumbers'
import { useState } from 'react'
import type { AskResearchAssistant, ResearchApplicability, SavedResearchNotebook } from '../lib/researchDossierApi'
import { SavedFigure } from './ResearchModules'
import { dateLabel, SourceList } from './ResearchEvidence'
import ResearchReadingAside from './ResearchReadingAside'

export default function ResearchQuantObservations({ instrumentId, notebook, applicability, onAskAssistant }: { instrumentId: string; notebook?: SavedResearchNotebook | null; applicability?: ResearchApplicability; onAskAssistant?: AskResearchAssistant }) {
  const [all, setAll] = useState(false)
  const numericSourceIds = new Set((notebook?.sources || []).filter(source => ['market', 'fundamental'].includes(source.observation_domain || '')).map(source => source.source_id))
  const observations = (notebook?.modules || []).filter(module => module.key === 'market-quantitative'
    || [...module.source_ids, ...module.figure_source_ids].some(sourceId => numericSourceIds.has(sourceId)))
  const figureIds = new Set((notebook?.modules || []).flatMap(module => module.figure_source_ids))
  const sources = (notebook?.sources || []).filter(source => figureIds.has(source.source_id) && ['market', 'fundamental'].includes(source.observation_domain || ''))
  return <section className="research-quant-observations" aria-label="量化指标">
    <div className="research-dossier-section-heading"><h2>量化指标</h2></div>
    <ResearchCurrentNumbers instrumentId={instrumentId} applicability={applicability} />
      <div className="research-quant-grid">{(all ? sources : sources.slice(0, 5)).map(source => <SavedFigure hideSummary={observations.some(observation => Boolean(observation.summary) && observation.figure_source_ids.includes(source.source_id))} key={`${notebook?.version_id}:${source.source_id}`} instrumentId={instrumentId} notebookVersionId={notebook?.version_id} source={source} onAskAssistant={onAskAssistant} />)}</div>
      {sources.length > 5 && <button type="button" className="research-support-link" aria-expanded={all} onClick={() => setAll(value => !value)}>{all ? '收起更多观察' : `查看其余 ${sources.length - 5} 份观察`}</button>}
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请检查该标的量化指标和历史变化：价格收益、量价关系、分析师预期与基本面数据。识别重要变化和风险，说明证据时点与缺口。', { instrument_id: instrumentId, notebook_version_id: notebook?.version_id })}>讨论量化指标</button>}
    {observations.some(observation => observation.coverage !== 'supported' || observation.gaps.length > 0) && <div className="research-quant-coverage"><span>部分分析受数据范围限制</span><ResearchReadingAside label="数据覆盖与缺口" title="数据覆盖与缺口">{observations.filter(observation => observation.coverage !== 'supported' || observation.gaps.length > 0).map(observation => <section key={observation.key}><h4 translate="no">{observation.title}</h4><p>{observation.coverage === 'insufficient' ? '量化证据不足。' : '量化数据部分可用。'}</p><ul className="research-dossier-list">{observation.gaps.map((gap, index) => <li key={index} translate="no">{gap}</li>)}</ul></section>)}</ResearchReadingAside></div>}
    {observations.length ? <><h3>量化分析</h3>{observations.map(observation => <article className="research-quant-analysis" key={observation.key}>
      {observation.title && <h4 translate="no">{observation.title}</h4>}
      {observation.evidence_as_of && <p className="sector-research-note">分析样本截至 {dateLabel(observation.evidence_as_of)}</p>}
      {observation.summary && <p translate="no">{observation.summary}</p>}

      <ResearchReadingAside label="量化解释与依据"><p className="sector-research-note">判断更新 {dateLabel(observation.updated_at)} · 最近检查 {dateLabel(observation.checked_at)}</p><p className="research-dossier-text" translate="no">{observation.analysis}</p>{observation.next_check && <p><span>下一观察：</span><span translate="no">{observation.next_check}</span></p>}<SourceList instrumentId={instrumentId} versionId={notebook?.version_id} sources={(notebook?.sources || []).filter(source => observation.source_ids.includes(source.source_id))} /></ResearchReadingAside>
    </article>)}</> : <p className="sector-research-note">尚未形成量化分析。上方数据不依赖自动研究运行。</p>}

  </section>
}
