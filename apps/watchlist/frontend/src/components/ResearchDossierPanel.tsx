import { useEffect, useState, type FormEvent } from 'react'
import { useStudioAccount } from './AccountBoundary'
import ResearchMandateRecord from './ResearchMandateRecord'
import InvestmentResearchState from './InvestmentResearchState'
import ResearchModules from './ResearchModules'
import './investment-research.css'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
import { SourceList, dateLabel, sourceUrl, hasTimeZone } from './ResearchEvidence'
import ResearchThemesPanel from './ResearchThemesPanel'
import ResearchRecentEvents from './ResearchRecentEvents'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import { addResearchMaterial, getResearchDossier, uploadResearchMaterial, type AskResearchAssistant, type HistoricalResearchCase, type NotebookSource, type ResearchCatalyst, type ResearchDossier, type ResearchMaterial, type ResearchQuestion, type SavedResearchNotebook } from '../lib/researchDossierApi'

const questionStatus = { open: '当时待验证', supported: '当时证据支持', refuted: '当时证据不支持' }
const questionTrackingLabels = { active: '跟进中', paused: '已暂停', closed: '已结束' }

function Catalyst({ catalyst, sources, onAskAssistant, instrumentId, versionId }: { catalyst: ResearchCatalyst; sources: NotebookSource[]; instrumentId?: string; versionId?: string; onAskAssistant?: AskResearchAssistant }) {
  const now = new Date()
  const localDay = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  const due = /^\d{4}-\d{2}-\d{2}$/.test(catalyst.scheduled_at)
    ? catalyst.scheduled_at < localDay
    : hasTimeZone(catalyst.scheduled_at) ? Date.parse(catalyst.scheduled_at) <= now.getTime() : null
  const status = catalyst.status === 'released' ? '已发布' : catalyst.status === 'cancelled' ? '已取消' : due === null ? '时间待核实' : due ? '结果待核实' : '预定事件'
  const references = sources.filter(source => catalyst.source_ids.includes(source.source_id))
  return <article className="research-notebook-question">
    <div className="research-notebook-question-heading"><h4 translate="no">{catalyst.title}</h4><span>{status}</span></div>
    <p className="sector-research-note">预定时间 <time dateTime={catalyst.scheduled_at}>{dateLabel(catalyst.scheduled_at)}</time></p>
    {catalyst.relevance && <p translate="no">{catalyst.relevance}</p>}
    {catalyst.outcome && <p translate="no">{catalyst.outcome}</p>}
    {catalyst.next_check && <p className="research-notebook-next"><strong>下一步核实</strong> <span translate="no">{catalyst.next_check}</span></p>}
    {(catalyst.scenarios.length > 0 || references.length > 0) && <details><summary>情景与依据</summary>{catalyst.scenarios.length > 0 && <TextList items={catalyst.scenarios} />}{references.length > 0 && <SourceList instrumentId={instrumentId} versionId={versionId} sources={references} />}</details>}
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请分析事项“${catalyst.title}”（${catalyst.scheduled_at}）。当前记录状态：${status}。\n相关性：${catalyst.relevance}\n发布前情景：${catalyst.scenarios.join('；')}\n已记录结果：${catalyst.outcome}\n下一步：${catalyst.next_check}\n参考来源：${catalyst.source_ids.join('、')}\n请先核实日程状态和结果；如已发布，对照实际值、此前预期和修订值，再说明对当前标的判断的影响。`)}>追问这项事件</button>}
  </article>
}

function questionPrompt(question: ResearchQuestion) {
  return `请复核这个问题：${question.question}\n当前判断：${question.assessment}\n跟踪安排：${questionTrackingLabels[question.tracking_status || 'active']}\n安排原因：${question.tracking_reason || ''}\n支持证据：${question.evidence_for.join('；')}\n反对证据：${question.evidence_against.join('；')}\n下一步核实：${question.next_check}\n参考来源：${question.source_ids.join('、')}\n请结合当前标的已有底稿，核实新增信息并说明是否改变原判断。证据判断与跟踪安排独立；讨论或修正判断不自动恢复已暂停或结束的跟踪。`
}

function NotebookQuestion({ question, sources = [], onAskAssistant, instrumentId, versionId }: { question: ResearchQuestion; sources?: NotebookSource[]; instrumentId?: string; versionId?: string; onAskAssistant?: AskResearchAssistant }) {
  const references = sources.filter((source) => question.source_ids.includes(source.source_id))
  return <article className="research-notebook-question">
    <div className="research-notebook-question-heading"><h4 translate="no">{question.question}</h4><span>{questionStatus[question.status]}</span></div>
    {question.assessment && <p translate="no">{question.assessment}</p>}
    {question.tracking_status && <p className="sector-research-note"><strong>当时跟踪安排</strong> {questionTrackingLabels[question.tracking_status]}{question.tracking_reason && <> · <span translate="no">{question.tracking_reason}</span></>}</p>}
    {question.next_check && <p className="research-notebook-next"><strong>下一步核实</strong> <span translate="no">{question.next_check}</span></p>}
    {(question.evidence_for.length > 0 || question.evidence_against.length > 0 || references.length > 0) && <details>
      <summary>正反证据</summary>
      {question.evidence_for.length > 0 && <div><strong>支持证据</strong><TextList items={question.evidence_for} /></div>}
      {question.evidence_against.length > 0 && <div><strong>反对证据</strong><TextList items={question.evidence_against} /></div>}
      {references.length > 0 && <SourceList instrumentId={instrumentId} versionId={versionId} sources={references} />}
    </details>}
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(questionPrompt(question))}>追问这个问题</button>}
  </article>
}

function TextList({ items }: { items: string[] }) {
  return <ul className="research-dossier-list">{items.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul>
}

function NotebookBody({ notebook, onAskAssistant, instrumentId, historical = false }: { notebook: SavedResearchNotebook; instrumentId?: string; onAskAssistant?: AskResearchAssistant; historical?: boolean }) {
  return <div className="research-notebook-body">
    {Boolean(notebook.facts?.length) && <details className="research-dossier-record"><summary>事实与口径</summary>{notebook.facts!.map((fact, index) => <article key={index}>
      <h4>{fact.subject} · {fact.metric}</h4><p>{fact.value} · {fact.unit} · {fact.period}</p>
      {fact.comparison && <p>对照：{fact.comparison}</p>}{fact.uncertainty && <p>{fact.uncertainty}</p>}
      <SourceList instrumentId={instrumentId} versionId={notebook.version_id} sources={(notebook.sources || []).filter(source => fact.source_ids.includes(source.source_id))} />
    </article>)}</details>}
    {notebook.key_drivers.length > 0 && <div><h4>关键驱动</h4><TextList items={notebook.key_drivers} /></div>}
    {historical && notebook.questions.length > 0 && <section aria-label="当时研究问题与判断"><h4>当时研究问题与判断</h4>{notebook.questions.map((question) => <NotebookQuestion instrumentId={instrumentId} versionId={notebook.version_id} key={question.key} question={question} sources={notebook.sources} onAskAssistant={onAskAssistant} />)}</section>}
    {historical && notebook.important_changes.length > 0 && <div><h4>重要变化</h4><TextList items={notebook.important_changes} /></div>}
    {notebook.next_research.length > 0 && <div><h4>后续研究</h4><TextList items={notebook.next_research} /></div>}
    {historical && Boolean(notebook.sources?.length) && <SourceList instrumentId={instrumentId} versionId={notebook.version_id} sources={notebook.sources!} />}
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请复核当前标的这份研究底稿：\n分领域研究：${(notebook.modules || []).map(module => module.summary).join('；')}\n关键驱动：${notebook.key_drivers.join('；')}\n研究问题：${notebook.questions.map((question) => `${question.question}：${question.assessment}`).join('\n')}\n后续研究：${notebook.next_research.join('；')}\n请检查证据是否仍成立，并指出需要补充的资料。`)}>追问这份底稿</button>}
  </div>
}


function MaterialForm({ instrumentId, onSaved, onCancel }: { instrumentId: string; onSaved: (material: ResearchMaterial) => void; onCancel: () => void }) {
  const [mode, setMode] = useState<'text' | 'file'>('text')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [source, setSource] = useState('')
  const [publishedAt, setPublishedAt] = useState('')
  const [effectiveDate, setEffectiveDate] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function save(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      const material = { title: title.trim(), source: source.trim(), ...(publishedAt ? { published_at: publishedAt } : {}), ...(effectiveDate ? { effective_date: effectiveDate } : {}) }
      const saved = mode === 'file' && file ? await uploadResearchMaterial(instrumentId, file, material)
        : await addResearchMaterial(instrumentId, { ...material, body: body.trim() })
      onSaved(saved)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '材料保存失败') }
    finally { setSaving(false) }
  }
  return <form className="research-material-form" aria-label="添加研究材料" onSubmit={(event) => void save(event)}>
    <label>材料类型<select value={mode} onChange={(event) => setMode(event.target.value as 'text' | 'file')} disabled={saving}><option value="text">文字材料</option><option value="file">上传文件</option></select></label>
    <label>标题{mode === 'file' ? '（可选）' : ''}<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={300} required={mode === 'text'} disabled={saving} /></label>
    {mode === 'file' ? <label className="research-material-wide">文件<input type="file" onChange={(event) => setFile(event.target.files?.[0] || null)} required disabled={saving} /></label>
      : <label className="research-material-wide">材料正文<textarea value={body} onChange={(event) => setBody(event.target.value)} rows={6} maxLength={60000} required disabled={saving} /></label>}
    <label className="research-material-wide">来源（可选）<input value={source} onChange={(event) => setSource(event.target.value)} placeholder="原文链接、机构或资料出处" disabled={saving} /></label>
    <label>发布日期（可选）<input type="date" value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} disabled={saving} /></label>
    <label>资料截至日期（可选）<input type="date" value={effectiveDate} onChange={(event) => setEffectiveDate(event.target.value)} disabled={saving} /></label>
    <p className="sector-research-note research-material-wide">发布日期与资料覆盖的日期分别记录；不清楚可留空。</p>
    {error && <p className="research-material-wide" role="alert">{error}</p>}
    <div className="sector-research-actions research-material-wide"><button type="submit" disabled={saving || (mode === 'text' ? !title.trim() || !body.trim() : !file)}>{saving ? '保存中…' : '保存材料'}</button><button type="button" onClick={onCancel} disabled={saving}>取消</button></div>
  </form>
}

function MaterialRecord({ material }: { material: ResearchMaterial }) {
  const href = sourceUrl(material.source)
  const attribution = typeof material.metadata.source === 'string' ? material.metadata.source : ''
  return <details className="research-dossier-record">
    <summary>{material.title || '研究材料'}{!material.body.trim() && ' · 正文未读取'}</summary>
    <p className="sector-research-note">发布 {dateLabel(material.metadata.published_at)} · 资料截至 {dateLabel(material.metadata.effective_date)} · 保存 {dateLabel(material.recorded_at)}</p>
    {material.source && <p className="sector-research-note">{href ? <a href={href} target="_blank" rel="noopener noreferrer">查看原件或来源</a> : `来源：${material.source}`}</p>}
    {attribution && <p className="sector-research-note">资料出处：{attribution}</p>}
    {typeof material.metadata.extraction === 'string' && <p className="sector-research-note">{material.metadata.extraction}</p>}
    {material.body.trim() ? <p className="research-dossier-text" translate="no">{material.body}</p> : <p className="sector-research-note">已保存资料记录，尚无可供研究使用的正文。</p>}
  </details>
}

function HistoricalCase({ record, onAskAssistant }: { record: HistoricalResearchCase; onAskAssistant?: AskResearchAssistant }) {
  return <details className="research-dossier-record">
    <summary translate="no">{record.case_title}</summary>
    <p className="sector-research-note">历史案例 · 未经本轮核证 · 原记录区间 {record.event.information_window.start} 至 {record.event.information_window.end}</p>
    <p className="research-dossier-text" translate="no">{record.event.verified_new_information}</p>
    <p className="research-dossier-text" translate="no">{record.event.session_mapping}</p>
    <h4>传导机制</h4><TextList items={record.analysis.causal_chain} />
    <p className="research-dossier-text" translate="no">{record.analysis.market_interpretation}</p>
    <h4>原研究启示</h4><p className="research-dossier-text" translate="no">{record.current_use.lesson}</p>
    {record.current_use.similarity_requirements.length > 0 && <><h4>类比前需核实的条件</h4><TextList items={record.current_use.similarity_requirements} /></>}
    {record.current_use.important_differences.length > 0 && <><h4>原记录指出的差异</h4><TextList items={record.current_use.important_differences} /></>}
    {record.limitations.length > 0 && <TextList items={record.limitations} />}
    <ul className="sector-event-sources">{record.sources.map((source, index) => {
      const href = sourceUrl(source.url)
      return <li key={index}>{href ? <a href={href} target="_blank" rel="noopener noreferrer" translate="no">{source.title}</a> : source.title}<small>{source.supports}</small></li>
    })}</ul>
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请复核历史案例“${record.case_title}”对当前标的是否有参考价值。\n历史记录区间：${record.event.information_window.start} 至 ${record.event.information_window.end}\n原研究启示：${record.current_use.lesson}\n类比所需条件：${record.current_use.similarity_requirements.join('；')}\n已知差异：${record.current_use.important_differences.join('；')}\n这是旧研究，未经本轮核证。请先核实历史证据和当前条件，不要将案例叙述当作今天的新事件。`)}>追问这个历史案例</button>}
  </details>
}

function PriorAnalysis({ notebook, instrumentId }: { notebook: SavedResearchNotebook; instrumentId: string }) {
  const prior = notebook.prior_analysis
  if (!prior) return null
  return <details className="research-prior-analysis"><summary>既有综合分析 · {dateLabel(prior.updated_at)}</summary>
    <p className="sector-research-note">{prior.note}</p>
    {prior.fundamental_view && <><h4>原基本面判断</h4><p className="research-dossier-text" translate="no">{prior.fundamental_view}</p></>}
    {prior.valuation_view && <><h4>原估值判断</h4><p className="research-dossier-text" translate="no">{prior.valuation_view}</p></>}
    <SourceList instrumentId={instrumentId} versionId={prior.version_id || undefined} sources={prior.sources} />
  </details>
}

export default function ResearchDossierPanel({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, variant = 'full', readingMode = false }: { instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; variant?: 'full' | 'summary'; readingMode?: boolean }) {
  const canWrite = useStudioAccount()?.team_role !== 'reader' && !readingMode
  const [dossier, setDossier] = useState<ResearchDossier | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [adding, setAdding] = useState(false)
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const updated = (event: Event) => {
      if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) setRefresh(value => value + 1)
    }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId])
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    void getResearchDossier(instrumentId, controller.signal, variant === 'full').then((value) => {
      if (!controller.signal.aborted) setDossier(value)
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '研究档案读取失败')
    })
    return () => controller.abort()
  }, [instrumentId, reviewRunId, reviewStatus, refresh, variant])
  const notebook = dossier?.notebook
  const sources = [...(dossier?.prior_sources || []), ...(notebook?.sources || [])]
  const selectedSources = (ids: string[]) => [...new Map(sources.filter(source => ids.includes(source.source_id)).map(source => [source.source_id, source])).values()]
  const ask: AskResearchAssistant | undefined = onAskAssistant ? ((question, reference) => onAskAssistant(question, reference || { instrument_id: instrumentId, notebook_version_id: notebook?.version_id })) : undefined
  const currentVersion = notebook?.version_id || notebook?.run_id
  const previousNotebooks = dossier?.notebook_history?.filter((item) => (item.version_id || item.notebook?.version_id || item.run_id) !== currentVersion) || []
  return <div className={`research-dossier-panel research-dossier-${variant}`}>
    {error && <p role="alert">研究档案暂时无法读取：{error}</p>}
    {!dossier && !error && <p className="sector-research-note" role="status">正在读取投资研究…</p>}
    {notebook?.investment_view ? <InvestmentResearchState mode="view" compact={!readingMode} instrumentId={instrumentId} notebook={notebook} sources={ids => <SourceList instrumentId={instrumentId} versionId={notebook.version_id} sources={selectedSources(ids)} />} onAskAssistant={ask} /> : dossier && <p className="research-empty-judgment">尚未形成当前投资判断。已保存资料与既有研究可在下方查阅。</p>}
    {readingMode && Boolean(notebook?.important_changes.length) && <section className="research-important-changes" aria-label="重要变化"><h3>重要变化</h3><TextList items={notebook!.important_changes} /></section>}
    {variant === 'full' && <>
      {readingMode ? <div className="research-report-body">
        {dossier?.research_plan?.scope && <p className="research-scope"><strong>研究范围</strong> <span translate="no">{dossier.research_plan.scope}</span></p>}
        {Boolean(dossier?.research_plan?.gaps.length) && <div className="research-plan-gaps"><strong>范围与资料仍有缺口</strong><TextList items={dossier!.research_plan!.gaps} /></div>}
        <ResearchModules instrumentId={instrumentId} notebook={notebook} plan={dossier?.research_plan} onAskAssistant={ask} />
        {notebook && <NotebookBody instrumentId={instrumentId} notebook={notebook} onAskAssistant={ask} />}
        {notebook && <PriorAnalysis instrumentId={instrumentId} notebook={notebook} />}
      </div> : <><ResearchThemesPanel instrumentId={instrumentId} reviewRunId={reviewRunId} reviewStatus={reviewStatus} onAskAssistant={ask} /><ResearchRecentEvents instrumentId={instrumentId} reviewRunId={reviewRunId} reviewStatus={reviewStatus} onAskAssistant={ask} /></>}
      {dossier && <details className="research-method-plan"><summary>研究设置与范围<span>{readingMode ? '查看当前范围与方法' : '方法、关注重点与约束'}</span></summary>
        {dossier.mandate && <ResearchMandateRecord key={instrumentId} instrumentId={instrumentId} mandate={dossier.mandate} availableModules={dossier.available_modules || dossier.frameworks} readOnly={readingMode} onSaved={mandate => { setDossier(current => current && ({ ...current, mandate })); setRefresh(value => value + 1) }} />}
        {dossier.research_plan && <>
          <h4>本次采用的领域方法</h4><TextList items={dossier.research_plan.basis} />
          {dossier.research_plan.modules.map(module => <details className="research-dossier-record" key={module.id}><summary>{module.title} · {module.applicability === 'unconfirmed' ? '适用范围待核实' : '已采用'}</summary>
            <p className="sector-research-note">方法版本 {module.version} · {module.reason}</p><p className="research-dossier-text" translate="no">{module.body}</p>
            {module.questions.length > 0 && <><h4>核心问题</h4><TextList items={module.questions} /></>}
            {module.evidence_requirements.length > 0 && <><h4>证据要求</h4><TextList items={module.evidence_requirements} /></>}
          </details>)}
        </>}
      </details>}
    <details className="research-dossier-archive" onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>研究档案<span>来源、预测验证、材料与历史版本</span></summary>
      {expanded && <div className="research-dossier-archive-body">
        {!dossier && !error && <p className="sector-research-note">正在读取研究档案…</p>}
        {dossier && <>
          <section aria-label="研究底稿"><h4>研究底稿</h4>
            {notebook && <InvestmentResearchState mode="records" instrumentId={instrumentId} notebook={notebook} sources={ids => <SourceList instrumentId={instrumentId} versionId={notebook.version_id} sources={selectedSources(ids)} />} onAskAssistant={ask} />}
            {notebook && <details className="research-dossier-record"><summary>底稿原始记录</summary><NotebookBody historical instrumentId={instrumentId} notebook={notebook} onAskAssistant={ask} /></details>}
            {notebook ? <><p className="sector-research-note">底稿更新于 {dateLabel(notebook.updated_at || notebook.checked_at)}</p><details className="research-dossier-record"><summary>本份研究的全部依据 · {notebook.sources?.length || 0}</summary><SourceList instrumentId={instrumentId} versionId={notebook.version_id} sources={notebook.sources || []} /></details></> : <p className="sector-research-note">尚未完成研究底稿。已保存的资料会留在档案中。</p>}
            {Boolean(notebook?.catalysts?.length) && <details className="research-dossier-record"><summary>研究日程与结果</summary>
              {notebook!.catalysts!.map(catalyst => <Catalyst instrumentId={instrumentId} versionId={notebook?.version_id} key={catalyst.key} catalyst={catalyst} sources={notebook?.sources || []} onAskAssistant={ask} />)}
            </details>}
            {previousNotebooks.length > 0 && <details className="research-dossier-record"><summary>以往底稿变化 · {previousNotebooks.length} 次</summary>
              {previousNotebooks.map((item) => <article key={item.run_id}><h4>{dateLabel(item.checked_at)}</h4>{item.notebook ? <><InvestmentResearchState historical instrumentId={instrumentId} notebook={item.notebook} sources={ids => <SourceList instrumentId={instrumentId} versionId={item.notebook?.version_id} sources={(item.notebook?.sources || []).filter(source => ids.includes(source.source_id))} />} onAskAssistant={onAskAssistant} /><ResearchModules instrumentId={instrumentId} notebook={item.notebook} plan={item.notebook.method_plan} /><PriorAnalysis instrumentId={instrumentId} notebook={item.notebook} /><NotebookBody historical instrumentId={instrumentId} notebook={item.notebook} onAskAssistant={onAskAssistant && (() => onAskAssistant('请复核这份历史底稿，按当时已知信息审视原判断，并区分后来的变化。', { instrument_id: instrumentId, notebook_version_id: item.notebook?.version_id }))} /></> : item.important_changes.length > 0 ? <TextList items={item.important_changes} /> : <p className="sector-research-note">本次未保存重要变化摘要。</p>}</article>)}
            </details>}
          </section>
          {Boolean(dossier.prior_sources?.length) && <details className="research-dossier-record"><summary>已取得的公开原文 · {dossier.prior_sources!.length}</summary>
            <p className="sector-research-note">原文独立于报告留存，研究时仍需核实其内容和日期。</p><SourceList instrumentId={instrumentId} sources={dossier.prior_sources!} />
          </details>}
          {dossier.frameworks.length > 0 && <section aria-label="研究方法"><h4>研究方法</h4>{dossier.frameworks.map((framework) => <details key={framework.id} className="research-dossier-record"><summary translate="no">{framework.title}</summary>
            <p className="sector-research-note">研究方法 · 版本 {framework.version}</p><p className="research-dossier-text" translate="no">{framework.body}</p>
          </details>)}</section>}
          <section aria-label="研究材料"><div className="research-dossier-section-heading"><h4>研究材料{dossier.materials.length > 0 ? ` · ${dossier.materials.length}` : ''}</h4><button type="button" disabled={!canWrite} onClick={() => { setAdding(true); setNotice(null) }}>添加材料</button></div>
            <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
            {adding && canWrite && <MaterialForm instrumentId={instrumentId} onCancel={() => setAdding(false)} onSaved={(material) => { setDossier((current) => current && ({ ...current, materials: [material, ...current.materials] })); setAdding(false); setNotice({ id: Date.now(), message: '材料已保存。', tone: 'success' }) }} />}
            {dossier.materials.length ? dossier.materials.map((material) => <MaterialRecord key={material.source_id} material={material} />) : <p className="sector-research-note">尚未添加研究材料。</p>}
          </section>
          {dossier.historical_cases.length > 0 && <section aria-label="历史案例"><h4>历史案例 · 未经本轮核证</h4>
            <p className="sector-research-note">以下沿用旧研究的语境与结论，仅供机制和背景参考，不是本日事件。类比当前标的前需重新核实。</p>
            {dossier.historical_case_limitations && <TextList items={dossier.historical_case_limitations} />}
            {dossier.historical_cases.map((record) => <HistoricalCase key={record.source_id} record={record} onAskAssistant={ask} />)}
          </section>}
        </>}
      </div>}
    </details>
    </>}
  </div>
}
