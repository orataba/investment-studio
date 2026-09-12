import { useStudioAccount } from './AccountBoundary'
import { useEffect, useState } from 'react'
import { fetchJson } from '../lib/api'
import ResearchDossierPanel from './ResearchDossierPanel'
import type { AskResearchAssistant, EventSource } from '../lib/researchDossierApi'
export type { EventSource } from '../lib/researchDossierApi'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
import './sector-research.css'
import InfoHint from '../../../../../packages/ui/src/InfoHint'

type ResearchReflection = { status: 'reviewed' | 'insufficient_evidence'; summary: string; reviewed_update_ids: string[] }
type Review = { reflection?: ResearchReflection | null; run_id: string; status: string; checked_at: string | null; view_updated_at?: string | null; view_run_id?: string | null; change_kind?: 'none' | 'knowledge' | 'investment'; summary: string; coverage: string[] }
export type SectorResearch = { instrument_id: string; ticker: string; sector_name: string; latest_review: Review | null; last_completed_review?: Review | null }
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
const running = (status?: string) => status === 'queued' || status === 'running'
const directions = { risk: '风险', opportunity: '机会', uncertain: '重大不确定性' }
const statusLabels: Record<string, string> = { queued: '等待更新', running: '研究更新中', completed: '研究已更新', limited: '已更新，覆盖受限', failed: '研究更新未完成' }
function completedReview(sector: SectorResearch) {
  const review = sector.last_completed_review || sector.latest_review
  return review && ['completed', 'limited'].includes(review.status) ? review : null
}
const timeFormatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short' })
const time = (value: string | null | undefined) => !value ? '待核实' : value.length === 10 ? value : timeFormatter.format(new Date(value))
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
        {sector.latest_review?.reflection && ['completed', 'limited'].includes(sector.latest_review.status) && <div className="sector-reflection">
          <p><strong>本轮复核</strong> · <span className={sector.latest_review.reflection.status === 'insufficient_evidence' ? 'sector-research-limitation' : undefined}>{sector.latest_review.reflection.status === 'reviewed' ? '已复核既有判断' : '复核证据不足'}</span></p>
          {sector.latest_review.reflection.summary && <p translate="no">{sector.latest_review.reflection.summary}</p>}
        </div>}
        {sector.latest_review?.status === 'failed' && sector.latest_review.summary && <p>本轮未完成原因：{sector.latest_review.summary}</p>}</div>)}
      {coverage.length > 0 && <ul className="sector-research-limitation">{coverage.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
      {[...completedRuns].map(([runId, group]) => <ResearchRunSources key={runId} {...group} expanded={expandedSourceScope === query} />)}
    </details>

    {variant === 'summary' && currentRisks.length > 0 && <section className="sector-current-risks" aria-label="当前风险与机会">
      <h3>风险与机会</h3>
      {visibleRisks.map((event) => <article className="sector-current-risk" key={event.case_id}>
        <div className="sector-current-risk-heading"><strong>{directions[event.direction]}</strong><h4 translate="no">{event.title}</h4>
          {event.information_type === 'rumor' && <span className="sector-research-limitation">传闻 · 待证实</span>}
          {event.information_type === 'opinion' && <span>观点</span>}
        </div>

      </article>)}
      {variant === 'summary' && currentRisks.length > visibleRisks.length && <p className="sector-research-note">另有 {currentRisks.length - visibleRisks.length} 项持续关注，可在研究追踪中查看。</p>}
    </section>}


  </section>
}
