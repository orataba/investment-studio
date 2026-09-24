import { useStudioAccount } from './AccountBoundary'
import { useEffect, useState } from 'react'
import { fetchJson } from '../lib/api'
import ResearchDossierPanel from './ResearchDossierPanel'
import type { AskResearchAssistant, EventSource, InvestmentView } from '../lib/researchDossierApi'
export type { EventSource } from '../lib/researchDossierApi'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
import './sector-research.css'

type ResearchReflection = { status: 'reviewed' | 'insufficient_evidence'; summary: string; reviewed_update_ids: string[] }
type Review = { reflection?: ResearchReflection | null; run_id: string; status: string; checked_at: string | null; view_updated_at?: string | null; view_run_id?: string | null; change_kind?: 'none' | 'knowledge' | 'investment'; summary: string; coverage: string[]; current_research?: { investment_view?: InvestmentView | null } | null }
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
const statusLabels: Record<string, string> = { queued: '等待更新', running: '研究更新中', completed: '研究已更新', limited: '已更新，覆盖受限', failed: '研究更新未完成' }
function completedReview(sector: SectorResearch) {
  const review = sector.last_completed_review || sector.latest_review
  return review && ['completed', 'limited'].includes(review.status) ? review : null
}
const timeFormatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short' })
const time = (value: string | null | undefined) => !value ? '待核实' : value.length === 10 ? value : timeFormatter.format(new Date(value))
export default function SectorResearchPanel({ instrumentId, variant = 'timeline', onOpenEvents, onAskAssistant }: {
  instrumentId: string; variant?: 'timeline' | 'summary' | 'status'
  onOpenEvents?: () => void; onAskAssistant?: AskResearchAssistant
}) {
  const query = `instrument_id=${encodeURIComponent(instrumentId)}`
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const [snapshot, setSnapshot] = useState<{ query: string; data: ResearchResponse } | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [submitted, setSubmitted] = useState<{ query: string; runId: string } | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [pollingPaused, setPollingPaused] = useState(false)
  const data = snapshot?.query === query ? snapshot.data : null
  const submittedRun = submitted?.query === query ? submitted.runId : null
  const hasRunningReview = data?.sectors.some((sector) => running(sector.latest_review?.status))
  const awaitingSubmittedRun = Boolean(submittedRun) && !data?.sectors.some((sector) => sector.latest_review?.run_id === submittedRun)
  const busy = starting || hasRunningReview || awaitingSubmittedRun

  useEffect(() => {
    const updated = (event: Event) => {
      if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) setRefresh(value => value + 1)
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
        method: 'POST', body: JSON.stringify({ instrument_ids: [instrumentId] }),
      })
      if (running(result.status)) setSubmitted({ query, runId: result.run_id })
      else setRefresh((value) => value + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '无法启动研究更新') }
    finally { setStarting(false) }
  }

  const sectors = data?.sectors || []
  const statusSummary = busy ? '研究更新中'
    : sectors.some(sector => sector.latest_review?.status === 'failed') ? '最新更新未完成'
      : sectors.some(sector => sector.latest_review?.status === 'limited') ? '已更新，覆盖受限'
        : !sectors.length || sectors.some(sector => !completedReview(sector)) ? '尚未完成研究'
          : sectors.every(sector => sector.latest_review?.change_kind === 'none') ? '已检查，无新增投资变化'
            : sectors.every(sector => sector.latest_review?.change_kind !== 'investment' && sector.latest_review?.change_kind !== undefined) ? '研究资料已更新' : '研究已更新'
  const coverage = [...new Set(sectors.flatMap(sector => sector.latest_review?.coverage || []))]
  const review = sectors[0]?.latest_review
  const title = variant === 'summary' ? '投资研究摘要' : variant === 'status' ? '每日研究更新' : '投资研究'
  return <section className={`sector-research-panel sector-research-${variant}${variant === 'timeline' ? ' investment-research-reading' : ''}`} aria-label={title}>
    <header className="sector-research-heading">
      <div className="sector-research-title"><h2>{title}</h2></div>
      <div className="sector-research-actions">
        {variant === 'summary' ? onOpenEvents && <button onClick={onOpenEvents}>阅读完整研究</button> : <>
          {pollingPaused && <button onClick={() => setRefresh(value => value + 1)} disabled={starting}>刷新状态</button>}
          <button onClick={() => void start()} disabled={!canWrite || busy || !data?.available || data.research_enabled === false}>{busy ? '研究更新中…' : '更新研究'}</button>
        </>}
      </div>
    </header>
    {data && (!data.available || data.research_enabled === false) && <p className="sector-research-limitation">{data.message || '研究所需来源暂不可用，已保存的研究仍可查看。'}</p>}
    {error && <p role="alert">研究更新状态暂时无法读取：{error}</p>}
    {pollingPaused && <p role="status">研究仍在更新，已暂停自动刷新，可手动刷新查看进展。</p>}
    {sectors.filter(sector => sector.latest_review?.status === 'failed' && sector.latest_review.summary).map(sector => <p key={sector.instrument_id} className="sector-research-limitation" role="status">本轮未完成原因：{sector.latest_review!.summary}</p>)}
    {variant !== 'status' && <ResearchDossierPanel key={instrumentId} instrumentId={instrumentId} reviewRunId={review?.run_id} reviewStatus={review?.status} onAskAssistant={onAskAssistant} variant={variant === 'summary' ? 'summary' : 'full'} />}
    {sectors.length > 0 && <details className="sector-check-coverage"><summary>研究运行记录<span>{statusSummary}{review?.checked_at && <> · 最近检查 {time(review.checked_at)}</>}{coverage.length ? ` · ${coverage.length} 项限制` : ''}</span></summary>
      {sectors.map(sector => <div key={sector.instrument_id}><p>{sector.latest_review ? statusLabels[sector.latest_review.status] || '状态待核实' : '尚未完成研究'} · {time(sector.latest_review?.checked_at)}</p>
        {sector.latest_review?.reflection && ['completed', 'limited'].includes(sector.latest_review.status) && <p translate="no">{sector.latest_review.reflection.status === 'reviewed' ? '已复核既有判断' : '复核证据不足'} · {sector.latest_review.reflection.summary}</p>}
      </div>)}
      {coverage.length > 0 && <ul className="sector-research-limitation">{coverage.map(gap => <li key={gap}>{gap}</li>)}</ul>}
    </details>}
  </section>
}
