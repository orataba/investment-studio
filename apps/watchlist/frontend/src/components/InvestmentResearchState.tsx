import { useState, type ReactNode } from 'react'
import { dateLabel } from './ResearchEvidence'
import type { AskResearchAssistant, InvestmentView, ResearchInsight, ResearchReference, SavedResearchNotebook } from '../lib/researchDossierApi'
import ResearchOpinionComposer from './ResearchOpinionComposer'
import { useStudioAccount } from './AccountBoundary'
import ResearchReadingAside from './ResearchReadingAside'

type Sources = (ids: string[]) => ReactNode

function ViewBody({ view, sources }: { view: InvestmentView; sources: Sources }) {
  return <>
    {view.coverage_status === 'limited' && <p className="sector-research-limitation">已保存的研究部分可用，未覆盖部分不能据此判断。</p>}
    {view.coverage_status === 'not_established' && <p className="sector-research-limitation">研究基线尚未建立，不能据此作出判断。</p>}
    <dl className="research-investment-dimensions">
    {([['attractiveness', '当前投资吸引力'], ['risk', '风险状况'], ['invalidation', '改判条件'], ['next_check', '下一步验证']] as const).map(([key, label]) => view[key] && !(key === 'attractiveness' && view.opportunities != null) && !(key === 'risk' && view.risks != null) && <div key={key}><dt>{label}</dt><dd translate="no">{view[key]}</dd></div>)}
  </dl>
    {([['opportunities', '机会'], ['risks', '风险']] as const).map(([key, label]) => Boolean(view[key]?.length) && <section key={key} className="research-insight-history"><h4>{label}</h4>{view[key]!.map(item => <article key={item.key}>
      <h5 translate="no">{item.title}</h5><p translate="no">{item.explanation}</p><p><span>下一观察：</span><span translate="no">{item.next_watch}</span></p>
      {Boolean(item.source_ids?.length || item.figure_source_ids?.length) && <ResearchReadingAside label="查看依据" title={item.title}>{sources([...new Set([...(item.source_ids || []), ...(item.figure_source_ids || [])])])}</ResearchReadingAside>}
    </article>)}</section>)}
  </>
}

function viewOpinionContext(view: InvestmentView, notebook: SavedResearchNotebook) {
  const attention = (label: string, items: ResearchInsight[] | null | undefined, legacy: string) => {
    if (items == null) return `${label}：${legacy || '尚未形成明确认识。'}`
    if (!items.length) return `${label}：暂无已确认${label}条目；仅限已检查范围，不表示不存在${label}。`
    return `${label}：\n${items.map(item => `${item.title}：${item.explanation}\n下一观察：${item.next_watch}`).join('\n')}`
  }
  const coverage = view.coverage_status === 'assessed' ? '已完成已披露范围检查' : view.coverage_status === 'limited' ? '部分覆盖' : view.coverage_status === 'not_established' ? '研究基线尚未建立' : '覆盖范围尚未明确'
  return {
    notebook_version_id: notebook.version_id,
    investment_view_version_id: view.version_id,
    source_ids: [...new Set([...(view.source_ids || []), ...[...(view.opportunities || []), ...(view.risks || [])].flatMap(item => [...(item.source_ids || []), ...(item.figure_source_ids || [])])])],
    background: [
      `研究判断：${view.direction}`, view.horizon && `期限：${view.horizon}`,
      attention('机会', view.opportunities, view.attractiveness), attention('风险', view.risks, view.risk),
      `覆盖状态：${coverage}`, `覆盖说明：${view.coverage_note || '仅依据已取得的资料，未覆盖部分不能据此判断。'}`,
      `判断更新：${view.updated_at || notebook.updated_at || notebook.checked_at || '日期待核实'}`,
    ].filter(Boolean).join('\n'),
  }
}

export default function InvestmentResearchState({ instrumentId, notebook, sources, onAskAssistant, historical = false }: { instrumentId: string; notebook: SavedResearchNotebook; sources: Sources; onAskAssistant?: AskResearchAssistant; historical?: boolean }) {
  const [writing, setWriting] = useState(false)
  const [notice, setNotice] = useState('')
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const view = notebook.investment_view
  const brief = notebook.decision_brief?.needs_review ? undefined : notebook.decision_brief
  const priorBrief = notebook.decision_brief?.needs_review ? notebook.decision_brief : undefined
  const reference: ResearchReference = { instrument_id: instrumentId, notebook_version_id: notebook.version_id }
  return <>
    {(view || brief) && <section className="research-notebook-current" aria-label={historical ? '当时投资判断' : '当前投资判断'}>
      <div className="research-judgment-heading"><h3>{historical ? '当时投资判断' : '当前投资判断'}</h3><time dateTime={view?.updated_at || brief?.updated_at || undefined}>{dateLabel(view?.updated_at || brief?.updated_at)}</time></div>
      {brief && <div className="research-decision-brief" aria-label="投资建议">
        <p className="research-current-direction" translate="no">{brief.recommendation}</p>
        {brief.rationale && <p className="research-decision-rationale" translate="no">{brief.rationale}</p>}
        {brief.updated_at && brief.updated_at !== view?.updated_at && <p className="sector-research-note">建议修订于 <time dateTime={brief.updated_at}>{dateLabel(brief.updated_at)}</time></p>}
        {(brief.horizon || brief.next_decision) && <dl className="research-decision-conditions">{brief.horizon && <div><dt>建议期限</dt><dd translate="no">{brief.horizon}</dd></div>}{brief.next_decision && <div><dt>下一决策节点</dt><dd translate="no">{brief.next_decision}</dd></div>}</dl>}
      </div>}
      {!brief && <p className="research-recommendation-unavailable">无法判断 · 尚无投资建议</p>}
      {view?.direction && <p className="research-current-summary-text" translate="no">{view.direction}</p>}
      {notebook.key_drivers.length > 0 && <ul className="research-lead-reasons">{notebook.key_drivers.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul>}
      {view && <details className="research-dossier-record"><summary>机会、风险与改判条件</summary><ViewBody view={view} sources={sources} /></details>}
      <div className="research-judgment-tools">
      <ResearchReadingAside label="判断依据与适用范围">
        {view?.coverage_note && <p className="sector-research-note" translate="no">{view.coverage_note}</p>}
        {Boolean(brief?.conditions.length) && <div className="research-decision-triggers"><h4>建议成立的条件</h4><ul>{brief!.conditions.map((condition, index) => <li key={index} translate="no">{condition}</li>)}</ul></div>}
        {view?.horizon && <p><strong>适用期限</strong> <span translate="no">{view.horizon}</span></p>}
        {view?.conviction && <p><strong>判断把握程度</strong> <span translate="no">{view.conviction}</span></p>}
        {notebook.publication && <section className="research-publication-byline"><h4>编制说明</h4><p><span translate="no">{notebook.publication.display_name}</span> 编制 · 研究截至 <time dateTime={notebook.publication.research_as_of}>{notebook.publication.research_as_of}</time>{notebook.publication.baseline && ' · 首次研究基线'}</p><p translate="no">{notebook.publication.verification_note}</p>{!notebook.publication.independent_model_review && <p>作者核对来源；未经独立模型核证。</p>}</section>}
        {(view?.assumptions.length || 0) > 0 && <><h4>关键假设</h4><ul className="research-dossier-list">{view?.assumptions.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul></>}
        {sources([...new Set([...(view?.source_ids || []), ...(brief?.source_ids || [])])])}
      </ResearchReadingAside>
      {priorBrief && <p className="research-prior-decision"><span>投资判断已变化，原建议待复核。</span> <ResearchReadingAside label="查看原建议"><p className="sector-research-note">原建议更新于 {dateLabel(priorBrief.updated_at)}</p><p translate="no">{priorBrief.recommendation}</p><p translate="no">{priorBrief.rationale}</p>{sources(priorBrief.source_ids)}</ResearchReadingAside></p>}
      {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(historical ? '请复核这份历史底稿中的投资判断。先按当时已知信息审视判断，再区分后来出现的变化与遗漏。' : '请复核当前投资判断。结合新的信息与当前定价，说明后续方向、吸引力或风险是否需要调整；没有实质变化时直接说明。', reference)}>{historical ? '追问当时的观点' : '追问当前观点'}</button>}
      {!historical && view && canWrite && <button className="sector-event-ask" type="button" onClick={() => setWriting(value => !value)}>记录投资观点</button>}
      </div>
      {writing && view && <ResearchOpinionComposer instrumentId={instrumentId} title="对当前研究判断的观点" context={viewOpinionContext(view, notebook)} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setNotice('投资观点已保存。') }} />}
      {notice && <p role="status">{notice}</p>}
      {Boolean(view?.versions?.length) && <details className="research-dossier-record"><summary>观点修订历史 · {view!.versions!.length} 次</summary>{view!.versions!.map(version => <article key={version.version_id}><h4>{dateLabel(version.updated_at)}</h4><p translate="no">{version.direction}</p><ViewBody view={version} sources={sources} />{version.assumptions.length > 0 && <><h4>当时的关键假设</h4><ul className="research-dossier-list">{version.assumptions.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul></>}{sources(version.source_ids)}{onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请复核这个历史观点。先按当时已知信息审视原判断，再区分后来出现的变化与遗漏；不要把后来的结果当作当时已知。', { ...reference, investment_view_version_id: version.version_id })}>追问当时的观点</button>}</article>)}</details>}
    </section>}
  </>
}
