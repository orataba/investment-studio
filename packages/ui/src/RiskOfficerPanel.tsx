import { useEffect, useRef, useState } from 'react'
import type { RiskRequest } from './instrumentRisk'
import { resolveWorkspaceUrl } from './navigation'
import './risk-officer.css'

type RunStatus = 'queued' | 'running' | 'completed' | 'failed'
type StartedRun = { run_id: string; status: RunStatus }
export type RiskOfficerReview = {
  available: boolean
  scope: { kind: 'watchlist' | 'instrument' | 'portfolio'; id: string; name: string }
  input_as_of: string | null
  counts: { research: number; quantitative: number; coverage: number }
  instruments: Array<{ instrument_id: string; name: string; as_of_date: string | null }>
  holdings?: Array<{ holding_id: string; name: string; detail_path?: string | null }>
  limitations: string[]
  latest_completed: null | {
    run_id: string
    status: 'completed'
    completed_at: string
    input_as_of: string | null
    summary: string
    review_note?: string
    priorities: Array<{ title: string; analysis: string; instrument_ids: string[]; holding_ids?: string[]; case_ids: string[]; source_ids?: string[]; next_watch: string }>
    evidence_sources?: Record<string, { title: string; detail_path?: string | null; start_date?: string | null; end_date?: string | null; currency?: string | null; frequency?: string | null }>
    limitations: string[]
    stale: boolean
  }
  latest_run: null | (StartedRun & { created_at: string; completed_at: string | null; message: string })
}

type Props = { request: RiskRequest; scopeQuery: string; refreshToken?: number; onCompleted?: () => void }
const running = (status: RunStatus | undefined) => status === 'queued' || status === 'running'
const completedTime = (value: string) => new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
const frequencyLabels: Record<string, string> = { daily: '日度', weekly: '周度', monthly: '月度' }
const portfolioHref = (path: string | null | undefined) => path?.startsWith('/portfolios/')
  ? `${resolveWorkspaceUrl(import.meta.env.VITE_PORTFOLIO_URL, 'portfolio')}${path}` : undefined

function ScopedRiskOfficer({ request, scopeQuery, refreshToken, onCompleted }: Props) {
  const [data, setData] = useState<RiskOfficerReview | null>(null)
  const [error, setError] = useState('')
  const [posting, setPosting] = useState(false)
  const [pending, setPending] = useState<StartedRun | null>(null)
  const [refresh, setRefresh] = useState(0)
  const observedRun = useRef<string | null>(null)
  const completedCallback = useRef(onCompleted)
  const mounted = useRef(true)
  const postController = useRef<AbortController | null>(null)
  completedCallback.current = onCompleted

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      postController.current?.abort()
    }
  }, [])

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let polling = running(pending?.status)
    const controller = new AbortController()
    async function load() {
      try {
        const result = await request<RiskOfficerReview>(`/risk/review?${scopeQuery}`, { signal: controller.signal })
        if (!active) return
        setData(result)
        setPending(null)
        setError('')
        polling = running(result.latest_run?.status)
        if (polling && result.latest_run) {
          observedRun.current = result.latest_run.run_id
        } else if (result.latest_run && observedRun.current === result.latest_run.run_id) {
          observedRun.current = null
          completedCallback.current?.()
        }
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : '风险研判读取失败。')
      } finally {
        if (active && polling) timer = setTimeout(() => void load(), 4000)
      }
    }
    void load()
    return () => {
      active = false
      controller.abort()
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [request, scopeQuery, refresh, refreshToken])

  async function update() {
    setPosting(true)
    setError('')
    const controller = new AbortController()
    postController.current = controller
    try {
      const query = new URLSearchParams(scopeQuery)
      const keys = ['watchlist_id', 'instrument_id', 'portfolio_id'].filter((key) => query.get(key))
      if (keys.length !== 1) throw new Error('请指定一个研判范围。')
      const result = await request<StartedRun>('/risk/review/runs', {
        method: 'POST',
        body: JSON.stringify({ [keys[0]]: query.get(keys[0]) }),
        signal: controller.signal,
      })
      if (!mounted.current) return
      observedRun.current = result.run_id
      setPending(result)
      setRefresh((value) => value + 1)
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : '未能发起风险研判。')
    } finally {
      if (mounted.current) setPosting(false)
    }
  }

  const latest = pending || data?.latest_run
  const busy = posting || running(latest?.status)
  const completed = data?.latest_completed
  const names = new Map(data?.instruments.map((instrument) => [instrument.instrument_id, instrument.name]))
  const holdings = new Map(data?.holdings?.map((holding) => [holding.holding_id, holding]))
  const limitations = [...new Set([...(data?.limitations || []), ...(completed?.limitations || [])])]
  const renderPriority = (priority: NonNullable<RiskOfficerReview['latest_completed']>['priorities'][number], index: number) => <li key={`${index}:${priority.title}`}>
    <h4 translate="no">{priority.title}</h4>
    {priority.instrument_ids.length > 0 && <p className="risk-officer-muted">
      {priority.instrument_ids.map((id) => names.get(id) || id).join('、')}
    </p>}
    {Boolean(priority.holding_ids?.length) && <p className="risk-officer-muted">{priority.holding_ids?.map((id, index) => {
      const holding = holdings.get(id)
      const href = portfolioHref(holding?.detail_path)
      return <span key={id}>{index > 0 && '、'}{href ? <a href={href}>{holding?.name || id}</a> : holding?.name || id}</span>
    })}</p>}
    {data?.scope.kind === 'portfolio' && !priority.instrument_ids.length && !priority.holding_ids?.length && <p className="risk-officer-muted">组合整体</p>}
    <details><summary>分析与下一步</summary>
      <p translate="no">{priority.analysis}</p>
      <p><strong>后续观察：</strong><span translate="no">{priority.next_watch}</span></p>
      {priority.source_ids?.map((sourceId) => {
        const source = completed?.evidence_sources?.[sourceId]
        const href = portfolioHref(source?.detail_path)
        return source ? <p className="risk-officer-muted" key={sourceId}>依据：{href ? <a href={href} translate="no">{source.title}</a> : <span translate="no">{source.title}</span>} · {source.start_date ? `${source.start_date} 至 ` : ''}{source.end_date || '日期未知'}{source.currency ? ` · ${source.currency}` : ''}{source.frequency ? ` · ${frequencyLabels[source.frequency] || source.frequency}` : ''}</p> : null
      })}
    </details>
  </li>

  return <section className="risk-officer" aria-label="风险研判">
    <header className="risk-officer-heading">
      <div><h2>风险研判</h2>{data && <p translate="no">{data.scope.name}</p>}</div>
      <button type="button" disabled={!data?.available || busy} onClick={() => void update()}>
        {busy ? '研判进行中…' : '更新研判'}
      </button>
    </header>
    {!data && !error && <p className="risk-officer-muted" role="status">正在读取已保存的研判…</p>}
    {data && <>
      <dl className="risk-officer-counts">
        <div><dt>研究上报</dt><dd>{data.counts.research}</dd></div>
        <div><dt>量化触发</dt><dd>{data.counts.quantitative}</dd></div>
        <div><dt>监测受限</dt><dd>{data.counts.coverage}</dd></div>
      </dl>
      <p className="risk-officer-muted">最近输入日期：{data.input_as_of
        ? <time dateTime={data.input_as_of}>{data.input_as_of.slice(0, 10)}</time> : '尚无输入日期'}</p>
      {!data.available && <p className="risk-officer-warning" role="status">风险研判服务不可用，暂时无法更新。</p>}
      {busy && <p className="risk-officer-muted" role="status">研判进行中，完成后将自动更新。</p>}
      {data.latest_run?.status === 'failed' && !busy && <p className="risk-officer-warning" role="status">
        本次研判失败：{data.latest_run.message || '本次未生成有效结论。'}
      </p>}
      {!completed && !busy && <p className="risk-officer-muted">尚未研判。点击“更新研判”发起首次研判。</p>}
      {completed && <section className="risk-officer-conclusion" aria-label="最近完成的研判">
        <div className="risk-officer-conclusion-heading"><h3>最近完成的研判</h3>
          <time dateTime={completed.completed_at}>{completedTime(completed.completed_at)}</time>
        </div>
        {completed.stale && <p className="risk-officer-warning">已有结论已过期，输入发生变化，请更新研判。</p>}
        <p className="risk-officer-summary" translate="no">{completed.summary}</p>
        {completed.review_note && <p className="risk-officer-muted" translate="no">{completed.review_note}</p>}
        {completed.priorities.length > 0 && <>
          <ol className="risk-officer-priorities" aria-label="优先事项">{completed.priorities.slice(0, 3).map(renderPriority)}</ol>
          {completed.priorities.length > 3 && <details><summary>其余研判 · {completed.priorities.length - 3} 项</summary>
            <ol className="risk-officer-priorities" start={4}>{completed.priorities.slice(3).map((priority, index) => renderPriority(priority, index + 3))}</ol>
          </details>}
        </>}
      </section>}
      {limitations.length > 0 && <details className="risk-officer-limitations"><summary>证据与覆盖限制</summary>
        <ul>{limitations.map((limitation) => <li key={limitation} translate="no">{limitation}</li>)}</ul>
      </details>}
    </>}
    {error && <p className="risk-officer-error" role="alert">{error}</p>}
  </section>
}

export default function RiskOfficerPanel(props: Props) {
  return <ScopedRiskOfficer key={props.scopeQuery} {...props} />
}
