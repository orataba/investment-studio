import WorkspaceSkeleton from './WorkspaceSkeleton'
import InfoHint from './InfoHint'
import { useEffect, useRef, useState } from 'react'
import type { RiskRequest, RiskCaseHistory, PriceRuleCounts, PriceRuleSummary } from './instrumentRisk'
import { PriceRuleCountsLine, PriceRuleDetails } from './PriceRiskRules'
import { researchStamp } from './ResearchRunStatus'
import ConfirmDialog from './ConfirmDialog'
import RiskChangeAudit from './RiskChangeAudit'
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
    case_changes?: RiskCaseHistory[]
    review_note?: string
    prepared_at?: string
    price_rule_summary?: { contract_version: number; counts: PriceRuleCounts;
      instruments: Array<PriceRuleSummary & { instrument_id: string; name: string }> } | null
    priorities: Array<{ title: string; analysis: string; instrument_ids: string[]; holding_ids?: string[]; case_ids: string[]; source_ids?: string[]; next_watch: string }>
    evidence_sources?: Record<string, { title: string; detail_path?: string | null; start_date?: string | null; end_date?: string | null; currency?: string | null; frequency?: string | null }>
    limitations: string[]
    stale: boolean | null
  }
  latest_run: null | (StartedRun & { created_at: string; completed_at: string | null; message: string })
}

type Props = { canRun?: boolean; request: RiskRequest; scopeQuery: string; refreshToken?: number; onCompleted?: () => void }
const running = (status: RunStatus | undefined) => status === 'queued' || status === 'running'
const settled = (status: RunStatus | undefined) => status === 'completed' || status === 'failed'
const frequencyLabels: Record<string, string> = { daily: '日度', weekly: '周度', monthly: '月度' }
const portfolioHref = (path: string | null | undefined) => path?.startsWith('/portfolios/')
  ? `${resolveWorkspaceUrl(import.meta.env.VITE_PORTFOLIO_URL, 'portfolio')}${path}` : undefined

function ScopedRiskOfficer({ canRun = true, request, scopeQuery, refreshToken, onCompleted }: Props) {
  const [data, setData] = useState<RiskOfficerReview | null>(null)
  const [error, setError] = useState('')
  const [posting, setPosting] = useState(false)
  const [confirming, setConfirming] = useState(false)
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
        polling = Boolean(result.latest_run && !settled(result.latest_run.status))
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
    if (!canSubmit) return
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
  const unknownStatus = Boolean(latest && !running(latest.status) && !settled(latest.status))
  const busy = posting || running(latest?.status)
  const canSubmit = Boolean(canRun && data?.available && !busy && !unknownStatus && !error)
  useEffect(() => { if (!canSubmit) setConfirming(false) }, [canSubmit])
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
      <div><div className="risk-officer-title"><h2>风险研判</h2>{data && <InfoHint label="风控输入" detail={`研究上报: ${data.counts.research} · 量化触发事项: ${data.counts.quantitative} · 监测受限: ${data.counts.coverage} · 输入截至: ${data.input_as_of?.slice(0, 10) || '尚无输入日期'}`} />}</div>{data && <p translate="no">{data.scope.name}</p>}</div>
      <button type="button" disabled={!canSubmit} onClick={() => data?.scope.kind === 'portfolio' ? void update() : setConfirming(true)}>
        {busy ? '研判进行中…' : '更新研判'}
      </button>
    </header>
    {confirming && data && <ConfirmDialog open title="更新共享风险研判" confirmLabel="确认更新研判" confirmTone="primary" busy={posting}
      onCancel={() => setConfirming(false)} onConfirm={() => { setConfirming(false); void update() }} description={<>
      <p>本次将总结所选范围，并可能更新以下标的的共享研究风险记录：待复核、已研判待核证、有效、解除或不成立。其他列表与组合读取同一份风险记录。</p>
      <ul>{data.instruments.map((instrument) => <li key={instrument.instrument_id} translate="no">{instrument.name}</li>)}</ul>
      <p>列表名称包含 QA 或测试不代表数据隔离；本次不修改实际持仓或交易。</p>
    </>} />}
    {!data && !error && <WorkspaceSkeleton />}
    {data && <>
      {!data.available && <p className="risk-officer-warning" role="status">风险研判服务不可用，暂时无法更新。</p>}
      {busy && <p className="risk-officer-muted" role="status">研判进行中，完成后将自动更新。</p>}
      {unknownStatus && <p className="risk-officer-warning" role="status">任务状态暂未确认；保留已有结论，确认状态前不能重复提交。 <button type="button" onClick={() => setRefresh(value => value + 1)}>刷新状态</button></p>}
      {data.latest_run?.status === 'failed' && !busy && <p className="risk-officer-warning" role="status">
        本次研判失败：{data.latest_run.message || '本次未生成有效结论。'}
      </p>}
      {!completed && !busy && <p className="risk-officer-muted">尚未研判。点击“更新研判”发起首次研判。</p>}
      {completed && <section className="risk-officer-conclusion" aria-label="最近完成的研判">
        <div className="risk-officer-conclusion-heading"><h3>最近完成的研判</h3>
          <time title={completed.completed_at} dateTime={completed.completed_at}>{researchStamp(completed.completed_at)}</time>
        </div>
        {completed.stale && <p className="risk-officer-warning">输入版本已有变化，请更新研判。</p>}
        {completed.stale === null && <p className="risk-officer-warning">尚未核对已有结论的当前输入，请更新研判。原结论与依据仍保留。</p>}
        <p className="risk-officer-summary" translate="no">{completed.summary}</p>
        <details><summary>本轮价格复核规则</summary>
          <p><span>任务编号</span> <code translate="no">{completed.run_id}</code></p>
          {completed.prepared_at && <p><span>输入冻结时间</span> {researchStamp(completed.prepared_at)}</p>}
          {completed.price_rule_summary ? <>
            <PriceRuleCountsLine counts={completed.price_rule_summary.counts} />
            <p>规则数与量化触发事项数不同；多条规则可以共用一条事项。未触发不代表没有风险。</p>
            <p>以下为本轮冻结的规则、参数与观察日期，不随当前设置变化。</p>
            {completed.price_rule_summary.instruments.map(item => <details key={item.instrument_id}>
              <summary translate="no">{item.name}</summary><PriceRuleCountsLine counts={item.counts} /><PriceRuleDetails summary={item} />
            </details>)}
          </> : <p>旧报告未留存规则统计与版本摘要；保留原结论，不使用当前设置反推当时规则。</p>}
        </details>
        {completed.case_changes && <details><summary>本次共享风险变更</summary>
          <p><span>任务编号</span> <code translate="no">{completed.run_id}</code></p>
          {completed.case_changes.length ? <ul>{completed.case_changes.map((change, index) => <li key={index}>
            <span translate="no">{change.title || change.case_id}</span>
            <RiskChangeAudit change={change} />
          </li>)}</ul> : <p>本次未改变共享风险记录。</p>}
        </details>}
        {completed.review_note && <InfoHint label="研判复核说明" detail={completed.review_note} />}
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
    {error && <div className="risk-officer-error" role="alert"><p>{error}</p><p>状态暂时无法读取，尚未确认任务停止。刷新状态不会发起新研判。</p><button type="button" onClick={() => setRefresh(value => value + 1)}>刷新状态</button></div>}
  </section>
}

export default function RiskOfficerPanel(props: Props) {
  return <ScopedRiskOfficer key={props.scopeQuery} {...props} />
}
