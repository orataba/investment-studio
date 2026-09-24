import { useState } from 'react'
import type { AskResearchAssistant, SavedResearchNotebook } from '../lib/researchDossierApi'
import { SavedFigure } from './ResearchModules'
import { dateLabel, SourceList } from './ResearchEvidence'
import ResearchReadingAside from './ResearchReadingAside'

export default function ResearchQuantObservations({ instrumentId, notebook, onAskAssistant }: { instrumentId: string; notebook?: SavedResearchNotebook | null; onAskAssistant?: AskResearchAssistant }) {
  const [all, setAll] = useState(false)
  const observation = notebook?.modules?.find(module => module.key === 'market-quantitative')
  const sources = (notebook?.sources || []).filter(source => observation?.figure_source_ids.includes(source.source_id))
  return <section className="research-quant-observations" aria-label="量化观察">
    <div className="research-dossier-section-heading"><h2>量化观察</h2>{observation?.evidence_as_of && <span className="sector-research-note">数据截至 {dateLabel(observation.evidence_as_of)}</span>}</div>
    {observation ? <>
      {observation.summary && <p translate="no">{observation.summary}</p>}
      {observation.coverage !== 'supported' && <p className="sector-research-limitation">{observation.coverage === 'partial' ? '量化数据部分可用。' : '量化证据不足。'}{observation.gaps.join('；')}</p>}
      {!sources.length && <p className="sector-research-note">尚未留存可展示的数值结果；不能据此推断风险较低。</p>}
      {(all ? sources : sources.slice(0, 5)).map(source => <SavedFigure key={`${notebook?.version_id}:${source.source_id}`} instrumentId={instrumentId} notebookVersionId={notebook?.version_id} source={source} onAskAssistant={onAskAssistant} />)}
      {sources.length > 5 && <button type="button" className="research-support-link" aria-expanded={all} onClick={() => setAll(value => !value)}>{all ? '收起更多观察' : `查看其余 ${sources.length - 5} 份观察`}</button>}
      <ResearchReadingAside label="量化解释与依据"><p className="sector-research-note">判断更新 {dateLabel(observation.updated_at)} · 最近检查 {dateLabel(observation.checked_at)}</p><p className="research-dossier-text" translate="no">{observation.analysis}</p>{observation.next_check && <p translate="no">下一观察：{observation.next_check}</p>}<SourceList instrumentId={instrumentId} versionId={notebook?.version_id} sources={(notebook?.sources || []).filter(source => observation.source_ids.includes(source.source_id))} /></ResearchReadingAside>
    </> : <p className="sector-research-note">尚未建立量化观察；缺少数据不代表没有异常或风险。</p>}
  </section>
}
