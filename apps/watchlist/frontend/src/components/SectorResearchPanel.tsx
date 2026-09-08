import { useStudioAccount } from './AccountBoundary'
import { useEffect, useState } from 'react'
import { today } from '../../../../../packages/ui/src/instrumentRisk'
import { fetchJson } from '../lib/api'
import ResearchDossierPanel from './ResearchDossierPanel'
import type { AskResearchAssistant } from '../lib/researchDossierApi'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
import './sector-research.css'
import InfoHint from '../../../../../packages/ui/src/InfoHint'

type Review = { run_id: string; status: string; checked_at: string | null; view_updated_at?: string | null; view_run_id?: string | null; change_kind?: 'none' | 'knowledge' | 'investment'; summary: string; coverage: string[] }
// Published research keeps the clock actually retained with its source evidence.
type EstimateSnapshot = { observation_id?: string; collected_at?: string; run_id?: string; read_at?: string | null; cutoff?: string | null }
export type SectorResearch = { instrument_id: string; ticker: string; sector_name: string; latest_review: Review | null; last_completed_review?: Review | null }
type EventSource = {
  source_id?: string; url?: string; title?: string; published_at?: string | null
  published_at_raw?: string | null; retrieved_at?: string | null; discovered_at?: string | null; time_status?: string
  source_type?: string; document_id?: string; version_id?: string
  as_of?: string | null
  measurement?: {
    current?: { date: string; volatility_pct: number } | null
    previous?: { date: string; volatility_pct: number } | null
    change_pp?: number | null; five_session_change_pp?: number | null
    historical_reference?: { start_date: string; end_date: string; median_pct: number; minimum_pct: number; maximum_pct: number } | null
  }
  methodology?: { half_life_sessions?: number; annualization?: number } | null
  current_snapshot?: EstimateSnapshot | null
  previous_snapshot?: EstimateSnapshot | null
  changes?: Array<{
    symbol: string; name?: string | null; frequency: string; target_period_end: string; metric: string; currency: string | null
    previous_value: number; current_value: number; delta_pct: number | null
    previous_collected_at: string | null; current_collected_at: string | null; analyst_count_changed: boolean | null
    current_currency_status?: string
  }>
}
export type EventSnapshot = {
  title: string; body: string; direction: 'risk' | 'opportunity' | 'uncertain'
  information_type: 'fact' | 'opinion' | 'rumor' | null; confidence: string; next_watch: string
  published_at: string | null; occurred_at: string | null; discovered_at: string | null
  recording_type?: 'new' | 'update' | 'backfill'; sources: EventSource[]; coverage: string[]
}
export type SectorEventRecord = EventSnapshot & {
  case_id: string; instrument_id: string; event_key: string; status: string; trigger_active: boolean; updated_at: string | null
  withdrawn?: boolean; withdrawal_reason?: string | null; withdrawn_at?: string | null
  history: Array<{ at: string; action: string; detail: string; snapshot: EventSnapshot | null }>
}
type ResearchResponse = { available: boolean; research_enabled?: boolean; message?: string; sectors: SectorResearch[]; events: SectorEventRecord[] }
type Progress = { key: string; snapshot: EventSnapshot; action: string }
const running = (status?: string) => status === 'queued' || status === 'running'
const directions = { risk: '风险', opportunity: '机会', uncertain: '重大不确定性' }
const confidenceLabels: Record<string, string> = { confirmed: '已确认', reported: '报道线索', unverified: '待证实' }
const statusLabels: Record<string, string> = { queued: '等待更新', running: '研究更新中', completed: '研究已更新', limited: '已更新，覆盖受限', failed: '研究更新未完成' }
function completedReview(sector: SectorResearch) {
  const review = sector.last_completed_review || sector.latest_review
  return review && ['completed', 'limited'].includes(review.status) ? review : null
}
const eventQuestion = (snapshot: EventSnapshot) => `请进一步分析“${snapshot.title}”：${snapshot.body}\n请核实关键证据，说明对当前标的的影响，以及下一步需要观察什么。`
function calendarDay(value: string | null | undefined) {
  if (!value) return ''
  if (value.length === 10) return value
  const date = new Date(value)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
const timeFormatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short' })
const time = (value: string | null | undefined) => !value ? '待核实' : value.length === 10 ? value : timeFormatter.format(new Date(value))
const eventDay = (snapshot: EventSnapshot) => calendarDay(snapshot.occurred_at || snapshot.published_at)
function sourceHref(value?: string) {
  try { const url = new URL(value || ''); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined } catch { return undefined }
}

function ResearchRunSources({ review, instruments, expanded }: { review: Review; instruments: string[]; expanded: boolean }) {
  type Original = { url: string; title: string; published_at?: string | null; retrieved_at?: string | null }
  const [sources, setSources] = useState<Original[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!expanded || sources !== null) return
    const controller = new AbortController()
    setError('')
    void fetchJson<{ web_evidence?: Array<{ operation: string; sources?: Array<EventSource & { text?: string }> }>; market_text_sources?: Array<EventSource & { text?: string }> }>(
      `/api/research/runs/${encodeURIComponent(review.run_id)}/context`, { signal: controller.signal },
    ).then((context) => {
      if (controller.signal.aborted) return
      const originals = new Map<string, Original>()
      for (const capture of [...(context.web_evidence || []), { operation: 'fetch', sources: context.market_text_sources || [] }]) {
        if (capture.operation !== 'fetch') continue
        for (const source of capture.sources || []) {
          const url = sourceHref(source.url)
          if (url && ((typeof source.text === 'string' && source.text.trim()) || (source.document_id && source.version_id))) originals.set(url, {
            url, title: source.title || url, published_at: source.published_at, retrieved_at: source.retrieved_at,
          })
        }
      }
      setSources([...originals.values()])
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '原文列表读取失败')
    })
    return () => controller.abort()
  }, [expanded, review.run_id, sources])
  return <section className="sector-run-sources" aria-label="对应研究查阅原文">
    <h4>对应研究查阅原文</h4>
    <p className="sector-research-note">对应研究截至 {time(review.checked_at)} · {instruments.join('、')}</p>
    {instruments.length > 1 && <p className="sector-research-note">这些标的共享本轮查阅资料，不代表每篇原文均支持每个标的的结论。</p>}
    {error ? <p className="sector-research-limitation" role="alert">原文列表暂时无法读取：{error}</p>
      : sources === null ? <p className="sector-research-note">正在读取已保存的原文记录…</p>
        : sources.length ? <ul className="sector-event-sources">{sources.map((source) => <li key={source.url}>
          <a href={source.url} target="_blank" rel="noopener noreferrer" translate="no">{source.title}</a>
          <small>发布 <time dateTime={source.published_at || undefined}>{source.published_at ? time(source.published_at) : '时间未知'}</time> · 取得 <time dateTime={source.retrieved_at || undefined}>{source.retrieved_at ? time(source.retrieved_at) : '时间未知'}</time></small>
        </li>)}</ul> : <p className="sector-research-note">本轮未保留可打开的原文记录。</p>}
  </section>
}

function progressFor(event: SectorEventRecord): Progress[] {
  const revisions = event.history.flatMap((item, index) => item.snapshot
    ? [{ key: `${event.case_id}:${item.at}:${index}`, snapshot: item.snapshot, action: item.action }] : [])
  return revisions.length ? revisions : [{ key: event.case_id, snapshot: event, action: 'current' }]
}
function EventGroup({ event, progress, supplementary = false, planned = false, onAskAssistant }: { event: SectorEventRecord; progress: Progress[]; supplementary?: boolean; planned?: boolean; onAskAssistant?: AskResearchAssistant }) {
  return <article className="sector-event-group">
    <header><h3 translate="no">{event.title}</h3><span>{event.withdrawn ? '核证撤回' : planned ? '待发生' : event.trigger_active ? '持续关注' : '已结束'}</span></header>
    {event.withdrawn && <><p className="sector-research-limitation">{event.withdrawal_reason || '原有判断未通过核证，已撤回。'}</p><p className="sector-research-note">撤回时间 {time(event.withdrawn_at)} · 以下旧稿仅供追溯，不再作为当前研究判断。</p></>}
    {progress.map(({ key, snapshot, action }) => <div className="sector-event-progress" key={key}>
      <div className="sector-event-meta">
        <strong>{directions[snapshot.direction]}</strong>
        {snapshot.information_type === 'rumor' && <span className="sector-research-limitation">传闻</span>}
        {snapshot.information_type === 'opinion' && <span>观点</span>}
        {(snapshot.recording_type === 'backfill' || (supplementary && Boolean(eventDay(snapshot)))) && <span>补录</span>}
        <span>{event.withdrawn ? '撤回前旧稿' : action === 'resolved' ? '结束跟进' : action === 'updated' ? '进展更新' : action === 'new' ? '新增记录' : '当前记录'}</span>
        <span>{confidenceLabels[snapshot.confidence] || '证据待核实'}</span>
      </div>
      {snapshot.title !== event.title && <h4 translate="no">{snapshot.title}</h4>}
      <p className="sector-event-impact" translate="no">{snapshot.body}</p>
      <p className="sector-event-dates"><span>{planned ? '预计发生' : '发生'} {time(snapshot.occurred_at)}</span><span>发布 {time(snapshot.published_at)}</span><span>收录 {time(snapshot.discovered_at)}</span></p>
      {!snapshot.occurred_at && <p className="sector-research-note">发生时间未知{snapshot.published_at ? '，按发布日期归入时间线。' : '，暂不归入近期发生的事件。'}</p>}
      <details><summary>影响、证据与下一步</summary>
        <div className="sector-event-detail"><p translate="no">{snapshot.body}</p>
          {snapshot.next_watch && <p><strong>下一步观察</strong> <span translate="no">{snapshot.next_watch}</span></p>}
          <ul className="sector-event-sources">{snapshot.sources.map((source, index) => {
            if (source.source_type === 'computed_metric') {
              const measure = source.measurement
              const current = measure?.current
              const historical = measure?.historical_reference
              const change = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)} 个百分点`
              return <li key={source.source_id || `computed:${index}`}>
                <strong>{current ? '数值风险依据' : '计算依据'}</strong>{source.title && <span translate="no"> · {source.title}</span>}
                <small>计算截至 <time dateTime={source.as_of || undefined}>{time(source.as_of)}</time></small>
                {current && <>
                  <small>观测日期 <time dateTime={current.date}>{time(current.date)}</time> · 当前波动率 {current.volatility_pct.toFixed(2)}%</small>
                  {measure?.previous && <small>前次 {measure.previous.volatility_pct.toFixed(2)}% · {time(measure.previous.date)}</small>}
                  {measure?.change_pp != null && <small>较前次 {change(measure.change_pp)}</small>}
                  {measure?.five_session_change_pp != null && <small>近 5 个交易观察 {change(measure.five_session_change_pp)}</small>}
                  {(source.methodology || historical) && <details><summary>计算口径与历史参考</summary>
                    {source.methodology?.half_life_sessions != null && <small>EWMA 半衰期 {source.methodology.half_life_sessions} 个交易观察</small>}
                    {source.methodology?.annualization != null && <small>年化观察数 {source.methodology.annualization}</small>}
                    {historical && <small>历史参考 {historical.start_date} 至 {historical.end_date} · 中位数 {historical.median_pct.toFixed(2)}% · 范围 {historical.minimum_pct.toFixed(2)}%–{historical.maximum_pct.toFixed(2)}%</small>}
                  </details>}
                </>}
              </li>
            }
            if (source.source_type === 'analyst_estimate_changes') return <li key={source.source_id || `estimates:${index}`}>
              <strong>FMP预期快照比较</strong>
              {source.current_snapshot?.collected_at
                ? <small>快照采集：前次 {time(source.previous_snapshot?.collected_at)} · 本次 {time(source.current_snapshot.collected_at)}</small>
                : <small>快照读取：前次 {time(source.previous_snapshot?.read_at)} · 本次 {time(source.current_snapshot?.read_at)}</small>}
              <small>以下为两次源数据采集之间的观测变化，不能确定精确调整或发布日期；财期是预测对象。</small>
              {source.changes?.some((change) => change.current_currency_status === 'inferred_from_reporting_currency')
                && <small>部分预期币种按公司财报币种推定，来源已留存；预期接口本身未直接披露币种。</small>}
              <details><summary>同财期预期变动 · {source.changes?.length || 0} 项</summary>
                <ul>{source.changes?.map((change) => <li key={`${change.symbol}:${change.frequency}:${change.target_period_end}:${change.metric}`}>
                  <span>{change.symbol}{change.name ? ` · ${change.name}` : ''} · {change.frequency === 'annual' ? '年度' : change.frequency === 'quarter' ? '季度' : change.frequency}财期截至 {change.target_period_end}</span>
                  <small>{change.metric === 'revenue_avg' ? '平均营收预期' : change.metric === 'eps_avg' ? '平均每股收益预期' : change.metric}：{change.previous_value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })} → {change.current_value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })} {change.currency || '币种待核实'}{change.metric === 'eps_avg' ? '/股' : ''}{change.delta_pct !== null ? ` · ${change.delta_pct > 0 ? '+' : ''}${change.delta_pct.toFixed(2)}%` : ''}</small>
                  <small>源数据采集区间：<time dateTime={change.previous_collected_at || undefined}>{time(change.previous_collected_at)}</time> → <time dateTime={change.current_collected_at || undefined}>{time(change.current_collected_at)}</time></small>
                  {change.analyst_count_changed && <small>分析师样本数量发生变化，共识变化不等于每位分析师均调整预测。</small>}
                </li>)}</ul>
              </details>
            </li>
            const href = sourceHref(source.url)
            return <li key={source.source_id || `${source.url}:${index}`}>
              {href ? <a href={href} target="_blank" rel="noopener noreferrer" translate="no">{source.title || source.url}</a> : <span translate="no">{source.title || '来源记录'}</span>}
              <small>原文发布 {time(source.published_at)}{source.retrieved_at && ` · 获取 ${time(source.retrieved_at)}`}</small>
            </li>
          })}</ul>
          {snapshot.coverage.length > 0 && <ul className="sector-research-limitation">{snapshot.coverage.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
        </div>
      </details>
      {onAskAssistant && !event.withdrawn && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(eventQuestion(snapshot))}>追问这条进展</button>}
    </div>)}
    {event.history.some((item) => !item.snapshot) && <details className="sector-event-legacy"><summary>早期跟进记录</summary>
      <p className="sector-research-note">以下记录未保存当时的完整事件版本。</p>
      <ul>{event.history.filter((item) => !item.snapshot).map((item, index) => <li key={`${item.at}:${index}`}>记录于 {time(item.at)} · {item.detail}</li>)}</ul>
    </details>}
  </article>
}

export default function SectorResearchPanel({ instrumentId, watchlistId, variant = 'timeline', onOpenEvents, onAskAssistant }: {
  instrumentId?: string; watchlistId?: string; variant?: 'timeline' | 'summary' | 'status'
  onOpenEvents?: () => void; onAskAssistant?: AskResearchAssistant
}) {
  const query = instrumentId ? `instrument_id=${encodeURIComponent(instrumentId)}` : watchlistId ? `watchlist_id=${encodeURIComponent(watchlistId)}` : ''
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const [snapshot, setSnapshot] = useState<{ query: string; data: ResearchResponse } | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [submitted, setSubmitted] = useState<{ query: string; runId: string } | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [pollingPaused, setPollingPaused] = useState(false)
  const [days, setDays] = useState(7)
  const [expandedSourceScope, setExpandedSourceScope] = useState<string | null>(null)
  const data = snapshot?.query === query ? snapshot.data : null
  const submittedRun = submitted?.query === query ? submitted.runId : null
  const hasRunningReview = data?.sectors.some((sector) => running(sector.latest_review?.status))
  const awaitingSubmittedRun = Boolean(submittedRun) && !data?.sectors.some((sector) => sector.latest_review?.run_id === submittedRun)
  const busy = starting || hasRunningReview || awaitingSubmittedRun

  useEffect(() => {
    const updated = (event: Event) => {
      if (!instrumentId || (event as CustomEvent<string[]>).detail.includes(instrumentId)) setRefresh(value => value + 1)
    }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId])

  useEffect(() => {
    if (!query) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let polls = 0
    setError('')
    setPollingPaused(false)
    async function load() {
      try {
        const response = await fetchJson<ResearchResponse>(`/api/sector-research?${query}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setSnapshot({ query, data: response })
        const submittedReviews = response.sectors.filter((sector) => sector.latest_review?.run_id === submittedRun)
        const stillRunning = (Boolean(submittedRun) && submittedReviews.length === 0) || response.sectors.some((sector) => running(sector.latest_review?.status))
        if (!stillRunning) return
        // Match the backend's 30-minute analysis timeout.
        if (polls >= 360) { setPollingPaused(true); return }
        polls += 1
        timer = setTimeout(load, 5000)
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '研究更新状态读取失败')
      }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [query, refresh, submittedRun])

  async function start() {
    if (!data) return
    setStarting(true)
    setError('')
    try {
      const result = await fetchJson<{ run_id: string; status: string }>('/api/sector-research/runs', {
        method: 'POST', body: JSON.stringify({ instrument_ids: data.sectors.map((sector) => sector.instrument_id) }),
      })
      if (running(result.status)) setSubmitted({ query, runId: result.run_id })
      else setRefresh((value) => value + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '无法启动研究更新') }
    finally { setStarting(false) }
  }

  if (!data && !error && query && variant === 'timeline') return <p className="sector-research-note" role="status">正在读取研究追踪…</p>
  if (!data?.sectors.length) return data?.message ? <p className="sector-research-limitation">{data.message}</p> : error ? <p role="alert">研究追踪暂时无法读取：{error}</p>
    : data && variant === 'timeline' ? <section className="sector-research-panel" aria-label="研究追踪"><h2>研究追踪</h2><p className="sector-research-note">尚未完成研究。</p>{instrumentId && <ResearchDossierPanel key={instrumentId} instrumentId={instrumentId} onAskAssistant={onAskAssistant} />}</section> : null
  const statusSummary = busy ? '研究更新中'
    : data.sectors.some((sector) => sector.latest_review?.status === 'failed') ? '最新更新未完成'
      : data.sectors.some((sector) => sector.latest_review?.status === 'limited') ? '已更新，覆盖受限'
        : data.sectors.some((sector) => !completedReview(sector)) ? '尚未完成研究'
          : data.sectors.every(sector => sector.latest_review?.change_kind === 'none') ? '已检查，无新增投资变化'
            : data.sectors.every(sector => sector.latest_review?.change_kind !== 'investment' && sector.latest_review?.change_kind !== undefined) ? '资料已更新，投资判断沿用' : '研究已更新'
  const end = today()
  const startDate = new Date(`${end}T12:00:00`)
  startDate.setDate(startDate.getDate() - days + 1)
  const windowStart = days === 0 ? '' : `${startDate.getFullYear()}-${String(startDate.getMonth() + 1).padStart(2, '0')}-${String(startDate.getDate()).padStart(2, '0')}`
  const inWindow = (item: EventSnapshot) => Boolean(eventDay(item)) && eventDay(item) >= windowStart && eventDay(item) <= end
  const withdrawn = data.events.filter((event) => event.withdrawn)
  const groups = data.events.filter((event) => !event.withdrawn).map((event) => ({ event, progress: progressFor(event) }))
  const recent = groups.map((group) => ({ ...group, progress: group.progress.filter((item) => inWindow(item.snapshot)) }))
    .filter((group) => group.progress.length).sort((a, b) => eventDay(b.progress[b.progress.length - 1].snapshot).localeCompare(eventDay(a.progress[a.progress.length - 1].snapshot)))
  const supplemental = groups.map((group) => ({ ...group, progress: group.progress.filter(({ snapshot: item }) => !eventDay(item)
    || (eventDay(item) < windowStart && calendarDay(item.discovered_at) >= windowStart)) })).filter((group) => group.progress.length && !recent.some((item) => item.event.case_id === group.event.case_id))
  const planned = groups.map((group) => ({ ...group, progress: group.progress.filter(({ snapshot: item }) => Boolean(item.occurred_at) && eventDay(item) > end) }))
    .filter((group) => group.progress.length && ![...recent, ...supplemental].some((item) => item.event.case_id === group.event.case_id))
  const active = groups.filter((group) => group.event.trigger_active && !recent.some((item) => item.event.case_id === group.event.case_id)
    && !supplemental.some((item) => item.event.case_id === group.event.case_id) && !planned.some((item) => item.event.case_id === group.event.case_id))
  const coverage = [...new Set(data.sectors.flatMap((sector) => [
    ...(sector.latest_review?.coverage || []), ...(completedReview(sector)?.coverage || []),
  ]))]
  const completedRuns = new Map<string, { review: Review; instruments: string[] }>()
  for (const sector of data.sectors) {
    const completed = completedReview(sector)
    if (!completed) continue
    const sourceRunId = completed.view_run_id || completed.run_id
    const group = completedRuns.get(sourceRunId) || { review: { ...completed, run_id: sourceRunId, checked_at: completed.view_updated_at || completed.checked_at }, instruments: [] }
    group.instruments.push(sector.ticker || sector.sector_name)
    completedRuns.set(sourceRunId, group)
  }
  const currentRisks = data.events.filter((event) => event.trigger_active && !event.withdrawn)
  const visibleRisks = variant === 'summary' ? currentRisks.slice(0, 3) : currentRisks
  const title = variant === 'summary' ? '研究摘要' : variant === 'status' ? '每日研究更新' : '研究追踪'
  return <section className={`sector-research-panel sector-research-${variant}`} aria-label={title}>
    <header className="sector-research-heading">
      <div className="sector-research-title"><h2>{title}</h2><InfoHint label="研究更新口径" detail="持续检查新信息，只有重要观点、预测或风险变化才形成研究更新。" /><span>{data.sectors.length === 1 ? data.sectors[0].sector_name || data.sectors[0].ticker : `${data.sectors.length} 个标的`} · {statusSummary}</span></div>
      <div className="sector-research-actions">
        {variant === 'summary' ? onOpenEvents && <button onClick={onOpenEvents}>查看研究追踪</button> : <>
          <button onClick={() => setRefresh((value) => value + 1)} disabled={starting}>刷新状态</button>
          <button onClick={() => void start()} disabled={!canWrite || busy || !data.available || data.research_enabled === false}>{busy ? '研究更新中…' : '更新研究'}</button>
        </>}
      </div>
    </header>
    {(!data.available || data.research_enabled === false) && <p className="sector-research-limitation">{data.message || '研究所需来源暂不可用，已保存的研究仍可查看。'}</p>}
    {error && <p role="alert">{error}</p>}
    {pollingPaused && <p role="status">研究仍在更新，已暂停自动刷新，可手动刷新查看进展。</p>}

    {variant !== 'status' && <section className="sector-current-conclusion" aria-label="当前研究结论">
      <h3>当前研究结论</h3>
      {data.sectors.map((sector) => {
        const completed = completedReview(sector)
        const latest = sector.latest_review
        return <article key={sector.instrument_id}>
          {data.sectors.length > 1 && <h4>{sector.ticker} · {sector.sector_name}</h4>}
          {completed?.summary ? <>
            <p className="sector-conclusion-date">观点更新于 <time dateTime={completed.view_updated_at || completed.checked_at || undefined}>{time(completed.view_updated_at || completed.checked_at)}</time></p>
            <p className="sector-conclusion-body" translate="no">{completed.summary}</p>
          </> : <p className="sector-research-note">{completed ? '尚无已发布的投资判断。' : '尚未完成研究。'}</p>}
          {latest && ['completed', 'limited'].includes(latest.status) && latest.change_kind && <p className="sector-latest-run">本轮检查 <time dateTime={latest.checked_at || undefined}>{time(latest.checked_at)}</time> · {latest.change_kind === 'none' ? latest.status === 'limited' ? '在已覆盖的信息中未发现新增投资变化，资料缺口见研究覆盖' : '未发现新增投资变化' : latest.change_kind === 'knowledge' ? '研究资料已更新，投资判断沿用' : '投资研究已更新'}</p>}
          {latest && (latest.run_id !== completed?.run_id || latest.status !== completed?.status) && <p className="sector-latest-run" role="status">最近更新：{statusLabels[latest.status] || '状态待核实'} · {time(latest.checked_at)}</p>}
        </article>
      })}
    </section>}

    {variant === 'timeline' && instrumentId && <ResearchDossierPanel key={instrumentId} instrumentId={instrumentId} reviewRunId={data.sectors[0].latest_review?.run_id} reviewStatus={data.sectors[0].latest_review?.status} onAskAssistant={onAskAssistant} />}

    <details key={query} className="sector-check-coverage" onToggle={(event) => setExpandedSourceScope(event.currentTarget.open ? query : null)}><summary>研究来源与覆盖{coverage.length ? ` · ${coverage.length} 项限制` : ''}</summary>
      {data.sectors.map((sector) => <div key={sector.instrument_id}><strong>{sector.ticker} · {sector.sector_name}</strong><p>最新更新 {time(sector.latest_review?.checked_at)} · {sector.latest_review ? statusLabels[sector.latest_review.status] || '状态待核实' : '尚未完成研究'}</p>
        {sector.latest_review?.status === 'failed' && sector.latest_review.summary && <p>本轮未完成原因：{sector.latest_review.summary}</p>}</div>)}
      {coverage.length > 0 && <ul className="sector-research-limitation">{coverage.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
      {[...completedRuns].map(([runId, group]) => <ResearchRunSources key={runId} {...group} expanded={expandedSourceScope === query} />)}
    </details>

    {variant !== 'status' && currentRisks.length > 0 && <section className="sector-current-risks" aria-label="当前风险与机会">
      <h3>风险与机会</h3>
      {visibleRisks.map((event) => <article className="sector-current-risk" key={event.case_id}>
        <div className="sector-current-risk-heading"><strong>{directions[event.direction]}</strong><h4 translate="no">{event.title}</h4>
          {event.information_type === 'rumor' && <span className="sector-research-limitation">传闻 · 待证实</span>}
          {event.information_type === 'opinion' && <span>观点</span>}
        </div>
        {variant === 'timeline' && <>
          {event.next_watch && <p className="sector-current-risk-next"><span>继续观察</span> <span translate="no">{event.next_watch}</span></p>}
          {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(eventQuestion(event))}>追问这项判断</button>}
        </>}
      </article>)}
      {variant === 'summary' && currentRisks.length > visibleRisks.length && <p className="sector-research-note">另有 {currentRisks.length - visibleRisks.length} 项持续关注，可在研究追踪中查看。</p>}
    </section>}

    {variant === 'timeline' && data.events.length > 0 && <section className="sector-research-progress" aria-label="研究进展">
      <div className="sector-timeline-heading"><h3>研究进展</h3><label>浏览范围 <select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>近 7 日</option><option value={30}>近 30 日</option><option value={90}>近 90 日</option><option value={0}>全部记录</option></select></label></div>
      <p className="sector-research-note">按发生日期归入时间线；发生时间未知时按发布日期。收录日期单独展示，旧闻补录不计作近期发生。</p>
      <div className="sector-event-timeline" aria-label="所选日期内研究进展">{recent.length ? recent.map((group) => <EventGroup key={group.event.case_id} {...group} onAskAssistant={onAskAssistant} />) : <p className="sector-event-empty">{days ? `近 ${days} 日` : '当前'}未记录重要研究进展。</p>}</div>
      {supplemental.length > 0 && <section className="sector-supplementary"><h3>补录与时间待核实</h3><p className="sector-research-note">以下记录的实际日期在当前范围外，或仍待核实。</p>{supplemental.map((group) => <EventGroup key={group.event.case_id} {...group} supplementary onAskAssistant={onAskAssistant} />)}</section>}
      {planned.length > 0 && <section className="sector-upcoming"><h3>待发生事项</h3>{planned.map((group) => <EventGroup key={group.event.case_id} {...group} planned onAskAssistant={onAskAssistant} />)}</section>}
      {active.length > 0 && <section className="sector-ongoing"><h3>持续关注 · 较早事项</h3>{active.map(({ event }) => <details key={event.case_id}><summary>{directions[event.direction]} · {event.title}</summary><EventGroup event={event} progress={[progressFor(event).slice(-1)[0]]} onAskAssistant={onAskAssistant} /></details>)}</section>}
      {withdrawn.length > 0 && <details className="sector-event-legacy"><summary>核证撤回记录 · {withdrawn.length}</summary>{withdrawn.map((event) => <EventGroup key={event.case_id} event={event} progress={progressFor(event)} />)}</details>}
    </section>}
  </section>
}
