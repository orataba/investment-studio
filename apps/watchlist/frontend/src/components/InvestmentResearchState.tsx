import { useEffect, useState, type ReactNode } from 'react'
import { dateLabel, SourceList } from './ResearchEvidence'
import type { AskResearchAssistant, InvestmentView, ResearchForecast, ResearchForecastReview, ResearchLesson, ResearchInsight, ResearchReference, SavedResearchNotebook } from '../lib/researchDossierApi'
import ResearchOpinionComposer from './ResearchOpinionComposer'
import { useStudioAccount } from './AccountBoundary'
import ResearchReadingAside from './ResearchReadingAside'
import { ReferencedResearchEvent } from './ResearchEventCard'
import { getResearchTheme, type ResearchTheme } from '../lib/researchDossierApi'

const forecastStatus = { active: '持续观察', confirmed: '结果已出现', refuted: '预测未成立', expired: '观察期已结束', withdrawn: '已撤回' }
type Sources = (ids: string[]) => ReactNode

function ReferencedThemeEvidence({ instrumentId, themeId }: { instrumentId: string; themeId: string }) {
  const [theme, setTheme] = useState<ResearchTheme | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void getResearchTheme(instrumentId, themeId, controller.signal).then(value => { if (!controller.signal.aborted) setTheme(value) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '主题依据读取失败。') })
    return () => controller.abort()
  }, [instrumentId, themeId])
  return error ? <p role="alert">{error}</p> : theme ? <section className="research-insight-theme-evidence">
    <h3 translate="no">{theme.title}</h3>
    <p className="sector-research-note">实质更新 {dateLabel(theme.last_changed_at)} · 最近检查 {dateLabel(theme.last_reviewed_at)}</p>
    <p className="research-dossier-text" translate="no">{theme.synthesis || '主题基线尚待建立。'}</p>
    {theme.latest_development && <p translate="no">{theme.latest_development}</p>}
    {theme.next_check && <p translate="no">下一观察：{theme.next_check}</p>}
    <SourceList instrumentId={instrumentId} versionId={theme.source_version_id} sources={theme.sources || []} />
  </section> : <p role="status">Loading</p>
}

function InsightList({ title, items, legacy, view, instrumentId, sources }: { title: string; items?: ResearchInsight[] | null; legacy: string; view: InvestmentView; instrumentId: string; sources: Sources }) {
  const [all, setAll] = useState(false)
  return <section className="research-insight-list" aria-label={title}>
    <h4>{title}{Boolean(items?.length) && <span> · {items!.length}</span>}</h4>
    {items?.length ? <>
      {(all ? items : items.slice(0, 3)).map(item => <article key={item.key} className="research-insight">
        <h5 translate="no">{item.title}</h5><p translate="no">{item.explanation}</p>
        <ResearchReadingAside label="依据与下一观察" title={`${item.title} · 依据与下一观察`}>
          <p translate="no">{item.explanation}</p>
          <p><strong>下一观察</strong> <span translate="no">{item.next_watch || '尚待明确。'}</span></p>
          {sources([...new Set([...(item.source_ids || []), ...(item.figure_source_ids || [])])])}
          {item.event_keys?.map(key => <ReferencedResearchEvent key={`${instrumentId}:${key}`} instrumentId={instrumentId} eventKey={key} />)}
          {item.theme_ids?.map(id => <ReferencedThemeEvidence key={`${instrumentId}:${id}`} instrumentId={instrumentId} themeId={id} />)}
        </ResearchReadingAside>
      </article>)}
      {items.length > 3 && <button className="research-support-link" type="button" aria-expanded={all} onClick={() => setAll(value => !value)}>{all ? '收起' : `展开其余 ${items.length - 3} 项`}</button>}
    </> : items == null && legacy ? <p translate="no">{legacy}</p> : <p className="sector-research-note">{view.coverage_status === 'assessed' ? `在已检查范围内，未发现足够明确的新${title}线索。` : view.coverage_status === 'not_established' ? '研究基线尚未建立，不能据此作出判断。' : '资料或覆盖范围不足，尚不能形成明确判断。'}</p>}
  </section>
}

function ViewBody({ view, sources }: { view: InvestmentView; sources: Sources }) {
  return <><dl className="research-investment-dimensions">
    {([['direction', '方向判断'], ['horizon', '适用期限'], ['attractiveness', '当前投资吸引力'], ['risk', '风险状况'], ['conviction', '判断把握程度'], ['invalidation', '改判条件'], ['next_check', '下一步验证']] as const).map(([key, label]) => view[key] && <div key={key}><dt>{label}</dt><dd translate="no">{view[key]}</dd></div>)}
  </dl>
    {view.coverage_note && <p className="sector-research-note" translate="no">{view.coverage_note}</p>}
    {([['opportunities', '机会'], ['risks', '风险']] as const).map(([key, label]) => Boolean(view[key]?.length) && <section key={key} className="research-insight-history"><h4>{label}</h4>{view[key]!.map(item => <article key={item.key}>
      <h5 translate="no">{item.title}</h5><p translate="no">{item.explanation}</p><p translate="no">下一观察：{item.next_watch}</p>
      {sources([...new Set([...(item.source_ids || []), ...(item.figure_source_ids || [])])])}
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

function ForecastBody({ forecast, sources }: { forecast: ResearchForecast; sources: Sources }) {
  return <>
    <p translate="no">{forecast.claim}</p>
    <p className="sector-research-note">提出于 <time dateTime={forecast.created_at || undefined}>{dateLabel(forecast.created_at)}</time>{forecast.updated_at !== forecast.created_at && <> · 修订于 <time dateTime={forecast.updated_at || undefined}>{dateLabel(forecast.updated_at)}</time></>}</p>
    {forecast.variable && <p><strong>预测对象</strong> <span translate="no">{forecast.variable}</span></p>}
    {forecast.horizon && <p><strong>观察期限</strong> <span translate="no">{forecast.horizon}</span></p>}
    {forecast.review_on && <p className="sector-research-note">复核日期 <time dateTime={forecast.review_on}>{forecast.review_on}</time></p>}
    {forecast.observation_condition && <p><strong>观察条件</strong> <span translate="no">{forecast.observation_condition}</span></p>}
    {forecast.invalidation && <p><strong>改变判断的条件</strong> <span translate="no">{forecast.invalidation}</span></p>}
    {(forecast.assumptions.length > 0 || forecast.source_ids.length > 0) && <details><summary>假设与依据</summary>
      {forecast.assumptions.length > 0 && <ul className="research-dossier-list">{forecast.assumptions.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul>}{sources(forecast.source_ids)}
    </details>}
  </>
}

function ForecastRecord({ forecast, reference, sources, onAskAssistant }: { forecast: ResearchForecast; reference: ResearchReference; sources: Sources; onAskAssistant?: AskResearchAssistant }) {
  const ask = (value: ResearchForecast) => onAskAssistant?.(`请复核这项预测“${value.claim}”。对照当时的假设与证据，分析新增信息、后续演化和是否需要修订判断。`, { ...reference, forecast_key: forecast.key, forecast_version_id: value.version_id })
  return <article className="research-notebook-question">
    <div className="research-notebook-question-heading"><h4 translate="no">{forecast.variable || '研究预测'}</h4><span>{forecastStatus[forecast.status]}</span></div>
    <ForecastBody forecast={forecast} sources={sources} />
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => ask(forecast)}>追问这项预测</button>}
    {Boolean(forecast.versions?.length) && <details className="research-dossier-record"><summary>预测修订历史 · {forecast.versions!.length} 次</summary>
      {forecast.versions!.map((version) => <article key={version.version_id}><h4>{forecastStatus[version.status]} · {dateLabel(version.updated_at)}</h4><ForecastBody forecast={version} sources={sources} />
        {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => ask(version)}>追问当时的预测</button>}
      </article>)}
    </details>}
  </article>
}

function ReviewBody({ review, forecasts, sources }: { review: ResearchForecastReview; forecasts: ResearchForecast[]; sources: Sources }) {
  const forecast = forecasts.find(item => item.key === review.forecast_key)
  const original = [forecast, ...(forecast?.versions || [])].find(item => item?.version_id === review.forecast_version_id)
  return <>
    <p className="sector-research-note">复盘于 {dateLabel(review.updated_at)}</p>
    <p><strong>实际结果</strong> <span translate="no">{review.outcome}</span></p>
    {review.mechanism_assessment && <p><strong>机制检验</strong> <span translate="no">{review.mechanism_assessment}</span></p>}
    {review.alternative_explanations.length > 0 && <><h4>其他可能解释</h4><ul className="research-dossier-list">{review.alternative_explanations.map((item, i) => <li key={i} translate="no">{item}</li>)}</ul></>}
    {original && <details className="research-dossier-record"><summary>对应的事前预测</summary><ForecastBody forecast={original} sources={sources} /></details>}
    {sources(review.source_ids)}
  </>
}

function LessonBody({ lesson, sources }: { lesson: ResearchLesson; sources: Sources }) {
  return <>
    {lesson.status === 'withdrawn' && <p className="sector-research-note"><strong>经验已停用</strong>{lesson.withdrawal_reason && <> · <span translate="no">{lesson.withdrawal_reason}</span></>}</p>}
    <p translate="no">{lesson.lesson}</p>
    {lesson.applicability && <p><strong>适用条件</strong> <span translate="no">{lesson.applicability}</span></p>}
    {lesson.limitations && <p><strong>适用边界</strong> <span translate="no">{lesson.limitations}</span></p>}
    <p className="sector-research-note">记录于 {dateLabel(lesson.updated_at)}</p>{sources(lesson.source_ids)}
  </>
}

export default function InvestmentResearchState({ instrumentId, notebook, sources, onAskAssistant, historical = false, mode = 'full', compact = false }: { instrumentId: string; notebook: SavedResearchNotebook; sources: Sources; onAskAssistant?: AskResearchAssistant; historical?: boolean; mode?: 'full' | 'view' | 'records'; compact?: boolean }) {
  const [writing, setWriting] = useState(false)
  const [notice, setNotice] = useState('')
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const view = notebook.investment_view
  const brief = notebook.decision_brief?.needs_review ? undefined : notebook.decision_brief
  const priorBrief = notebook.decision_brief?.needs_review ? notebook.decision_brief : undefined
  const forecasts = notebook.forecasts || []
  const activeForecasts = forecasts.filter(item => item.status === 'active')
  const concludedForecasts = forecasts.filter(item => item.status !== 'active')
  const reviews = notebook.forecast_reviews || []
  const lessons = notebook.lessons || []
  const reference: ResearchReference = { instrument_id: instrumentId, notebook_version_id: notebook.version_id }
  return <>
    {(view || brief) && mode !== 'records' && <section className={`research-notebook-current${compact ? ' research-current-brief' : ''}`} aria-label={historical ? '当时投资判断' : compact ? '当前机会与风险' : '当前投资判断'}>
      <div className="research-judgment-heading"><h3>{historical ? '当时投资判断' : compact ? '当前机会与风险' : '当前投资判断'}</h3><time dateTime={view?.updated_at || brief?.updated_at || undefined}>{dateLabel(view?.updated_at || brief?.updated_at)}</time></div>
      {brief && !compact && <div className="research-decision-brief" aria-label="投资建议">
        <p className="research-current-direction" translate="no">{brief.recommendation}</p>
        {brief.rationale && <p className="research-decision-rationale" translate="no">{brief.rationale}</p>}
        {brief.updated_at && brief.updated_at !== view?.updated_at && <p className="sector-research-note">建议修订于 <time dateTime={brief.updated_at}>{dateLabel(brief.updated_at)}</time></p>}
        {(brief.horizon || brief.next_decision) && <dl className="research-decision-conditions">{brief.horizon && <div><dt>建议期限</dt><dd translate="no">{brief.horizon}</dd></div>}{brief.next_decision && <div><dt>下一决策节点</dt><dd translate="no">{brief.next_decision}</dd></div>}</dl>}
        {brief.conditions.length > 0 && <div className="research-decision-triggers"><h4>建议成立的条件</h4><ul>{brief.conditions.map((condition, index) => <li key={index} translate="no">{condition}</li>)}</ul></div>}
      </div>}
      {view && (compact ? <>
        <p className="research-current-direction" translate="no">{view.direction || '当前总结尚待补充。'}</p>
        {view.coverage_note && <p className="sector-research-note" translate="no">{view.coverage_note}</p>}
        {view.coverage_status === 'limited' && <p className="sector-research-limitation">本次研究部分可用，未覆盖部分不能据此判断。</p>}
        <div className="research-insight-grid"><InsightList title="机会" items={view.opportunities} legacy={view.attractiveness} {...{ view, instrumentId, sources }} /><InsightList title="风险" items={view.risks} legacy={view.risk} {...{ view, instrumentId, sources }} /></div>
      </> : <ViewBody view={view} sources={sources} />)}
      {!view && compact && brief && <><p className="research-current-direction" translate="no">{brief.recommendation}</p><p className="sector-research-note">沿用历史研究摘要；当前机会与风险尚未形成结构化认识。</p></>}
      <div className="research-judgment-tools">
      <ResearchReadingAside label="判断依据与适用范围">
        {compact && notebook.key_drivers.length > 0 && <><h4>主要理由</h4><ul>{notebook.key_drivers.map((text, index) => <li key={index} translate="no">{text}</li>)}</ul></>}
        {compact && view?.invalidation && <p><strong>改判条件</strong> <span translate="no">{view.invalidation}</span></p>}
        {compact && view?.next_check && <p><strong>下一验证</strong> <span translate="no">{view.next_check}</span></p>}
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
      {!compact && Boolean(view?.versions?.length) && <details className="research-dossier-record"><summary>观点修订历史 · {view!.versions!.length} 次</summary>{view!.versions!.map(version => <article key={version.version_id}><h4>{dateLabel(version.updated_at)}</h4><ViewBody view={version} sources={sources} />{version.assumptions.length > 0 && <><h4>当时的关键假设</h4><ul className="research-dossier-list">{version.assumptions.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul></>}{sources(version.source_ids)}{onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请复核这个历史观点。先按当时已知信息审视原判断，再区分后来出现的变化与遗漏；不要把后来的结果当作当时已知。', { ...reference, investment_view_version_id: version.version_id })}>追问当时的观点</button>}</article>)}</details>}
    </section>}
    {mode !== 'view' && activeForecasts.length > 0 && <section className="research-notebook-current" aria-label={historical ? '当时持续预测' : '持续预测'}><h3>{historical ? '当时持续预测' : '持续预测'}</h3>{activeForecasts.map(forecast => <ForecastRecord key={forecast.key} {...{ forecast, reference, sources, onAskAssistant }} />)}</section>}
    {mode !== 'view' && (concludedForecasts.length > 0 || reviews.length > 0 || lessons.length > 0) && <details className="research-dossier-archive"><summary>预测复盘与研究经验</summary>
      {concludedForecasts.map(forecast => <ForecastRecord key={forecast.key} {...{ forecast, reference, sources, onAskAssistant }} />)}
      {reviews.map(review => <article className="research-notebook-question" key={review.key}><h4>预测复盘</h4><ReviewBody {...{ review, forecasts, sources }} />
        {Boolean(review.versions?.length) && <details className="research-dossier-record"><summary>复盘修订历史 · {review.versions!.length} 次</summary>{review.versions!.map(version => <article key={version.version_id}><ReviewBody review={version} {...{ forecasts, sources }} /></article>)}</details>}
      </article>)}
      {lessons.map(lesson => <article className="research-notebook-question" key={lesson.key}><h4>研究经验</h4><LessonBody {...{ lesson, sources }} />
        {onAskAssistant && lesson.version_id && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请复核这条研究经验“${lesson.lesson}”的适用条件、局限和反例，区分已验证机制与单次结果，说明应保留、修订还是停用。${lesson.status === 'withdrawn' ? '这条经验已经停用，不得默认为当前规则；恢复使用需要明确判断和依据。' : ''}`, { instrument_id: instrumentId, research_update_id: `research:${lesson.version_id}` })}>复核这条经验</button>}
        {Boolean(lesson.versions?.length) && <details className="research-dossier-record"><summary>经验修订历史 · {lesson.versions!.length} 次</summary>{lesson.versions!.map(version => <article key={version.version_id}><LessonBody lesson={version} sources={sources} /></article>)}</details>}
      </article>)}
    </details>}
  </>
}
