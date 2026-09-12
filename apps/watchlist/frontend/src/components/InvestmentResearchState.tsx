import type { ReactNode } from 'react'
import type { AskResearchAssistant, InvestmentView, ResearchForecast, ResearchForecastReview, ResearchLesson, ResearchReference, SavedResearchNotebook } from '../lib/researchDossierApi'

const forecastStatus = { active: '持续观察', confirmed: '结果已出现', refuted: '预测未成立', expired: '观察期已结束', withdrawn: '已撤回' }
const dateLabel = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '时间未记录'
type Sources = (ids: string[]) => ReactNode

function ViewBody({ view }: { view: InvestmentView }) {
  return <dl className="research-investment-dimensions">
    {([['direction', '方向判断'], ['horizon', '适用期限'], ['attractiveness', '当前投资吸引力'], ['risk', '风险状况'], ['conviction', '判断把握程度'], ['invalidation', '改判条件'], ['next_check', '下一步验证']] as const).map(([key, label]) => view[key] && <div key={key}><dt>{label}</dt><dd translate="no">{view[key]}</dd></div>)}
  </dl>
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

export default function InvestmentResearchState({ instrumentId, notebook, sources, onAskAssistant, historical = false, mode = 'full' }: { instrumentId: string; notebook: SavedResearchNotebook; sources: Sources; onAskAssistant?: AskResearchAssistant; historical?: boolean; mode?: 'full' | 'view' | 'records' }) {
  const view = notebook.investment_view
  const forecasts = notebook.forecasts || []
  const activeForecasts = forecasts.filter(item => item.status === 'active')
  const concludedForecasts = forecasts.filter(item => item.status !== 'active')
  const reviews = notebook.forecast_reviews || []
  const lessons = notebook.lessons || []
  const reference: ResearchReference = { instrument_id: instrumentId, notebook_version_id: notebook.version_id }
  return <>
    {view && mode !== 'records' && <section className="research-notebook-current" aria-label={historical ? '当时投资判断' : '当前投资判断'}>
      {mode !== 'view' && <h3>{historical ? '当时投资判断' : '当前投资判断'}</h3>}
      <p className="sector-research-note">观点更新于 <time dateTime={view.updated_at || undefined}>{dateLabel(view.updated_at)}</time></p>
      <ViewBody view={view} />
      {(view.assumptions.length > 0 || view.source_ids.length > 0) && <details><summary>关键假设与依据</summary><ul className="research-dossier-list">{view.assumptions.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul>{sources(view.source_ids)}</details>}
      {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(historical ? '请复核这份历史底稿中的投资判断。先按当时已知信息审视判断，再区分后来出现的变化与遗漏。' : '请复核当前投资判断。结合新的信息与当前定价，说明后续方向、吸引力或风险是否需要调整；没有实质变化时直接说明。', reference)}>{historical ? '追问当时的观点' : '追问当前观点'}</button>}
      {Boolean(view.versions?.length) && <details className="research-dossier-record"><summary>观点修订历史 · {view.versions!.length} 次</summary>{view.versions!.map(version => <article key={version.version_id}><h4>{dateLabel(version.updated_at)}</h4><ViewBody view={version} />{version.assumptions.length > 0 && <><h4>当时的关键假设</h4><ul className="research-dossier-list">{version.assumptions.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul></>}{sources(version.source_ids)}{onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant('请复核这个历史观点。先按当时已知信息审视原判断，再区分后来出现的变化与遗漏；不要把后来的结果当作当时已知。', { ...reference, investment_view_version_id: version.version_id })}>追问当时的观点</button>}</article>)}</details>}
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
