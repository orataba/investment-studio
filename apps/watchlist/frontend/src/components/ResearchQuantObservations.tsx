import ResearchCurrentNumbers from './ResearchCurrentNumbers'
import { useState } from 'react'
import type { AskResearchAssistant, SavedResearchNotebook } from '../lib/researchDossierApi'
import { SavedFigure } from './ResearchModules'
import { dateLabel, SourceList } from './ResearchEvidence'
import ResearchReadingAside from './ResearchReadingAside'

export default function ResearchQuantObservations({ instrumentId, notebook, onAskAssistant }: { instrumentId: string; notebook?: SavedResearchNotebook | null; onAskAssistant?: AskResearchAssistant }) {
  const [all, setAll] = useState(false)
  const observation = notebook?.modules?.find(module => module.key === 'market-quantitative')
  const figureIds = new Set((notebook?.modules || []).flatMap(module => module.figure_source_ids))
  const sources = (notebook?.sources || []).filter(source => figureIds.has(source.source_id) && ['market', 'fundamental'].includes(source.observation_domain || ''))
  return <section className="research-quant-observations" aria-label="量化指标">
    <div className="research-dossier-section-heading"><h2>量化指标</h2></div>
    <ResearchCurrentNumbers instrumentId={instrumentId} />
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请检查该标的量化指标和历史变化：价格收益、量价关系、分析师预期与基本面数据。识别重要变化和风险，说明证据时点与缺口。', { instrument_id: instrumentId, notebook_version_id: notebook?.version_id })}>讨论量化指标</button>}
    {observation ? <><h3>量化分析</h3>{observation.evidence_as_of && <p className="sector-research-note">分析样本截至 {dateLabel(observation.evidence_as_of)}</p>}
      {observation.summary && <p translate="no">{observation.summary}</p>}
      {(observation.coverage !== 'supported' || observation.gaps.length > 0) && <div className="sector-research-limitation">
        {observation.coverage !== 'supported' && <span>{observation.coverage === 'partial' ? '量化数据部分可用。' : '量化证据不足。'}</span>}
        <p>缺少的资料不支持完整比较；下方已留存结果仍可查阅。请先核对缺口对应的范围与时点。</p>
        {observation.gaps.length > 0 && <ResearchReadingAside label="查看数据缺口"><ul className="research-dossier-list">{observation.gaps.map((gap, index) => <li key={index} translate="no">{gap}</li>)}</ul></ResearchReadingAside>}
      </div>}
      {!sources.length && <p className="sector-research-note">尚未留存可展示的数值结果；不能据此推断风险较低。</p>}
      <ResearchReadingAside label="量化解释与依据"><p className="sector-research-note">判断更新 {dateLabel(observation.updated_at)} · 最近检查 {dateLabel(observation.checked_at)}</p><p className="research-dossier-text" translate="no">{observation.analysis}</p>{observation.next_check && <p><span>下一观察：</span><span translate="no">{observation.next_check}</span></p>}<SourceList instrumentId={instrumentId} versionId={notebook?.version_id} sources={(notebook?.sources || []).filter(source => observation.source_ids.includes(source.source_id))} /></ResearchReadingAside>
    </> : <p className="sector-research-note">尚未形成量化分析。上方数据不依赖自动研究运行。</p>}
      <div className="research-quant-grid">{(all ? sources : sources.slice(0, 5)).map(source => <SavedFigure hideSummary={Boolean(observation?.summary)} key={`${notebook?.version_id}:${source.source_id}`} instrumentId={instrumentId} notebookVersionId={notebook?.version_id} source={source} onAskAssistant={onAskAssistant} />)}</div>
      {sources.length > 5 && <button type="button" className="research-support-link" aria-expanded={all} onClick={() => setAll(value => !value)}>{all ? '收起更多观察' : `查看其余 ${sources.length - 5} 份观察`}</button>}
  </section>
}
