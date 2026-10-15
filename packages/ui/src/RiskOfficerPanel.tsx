import type { ModelWorkStatus } from './modelWork'
import WorkspaceSkeleton from './WorkspaceSkeleton'
import InfoHint from './InfoHint'
import { useEffect, useRef, useState } from 'react'
import type { RiskRequest, RiskCaseHistory, PriceRuleCounts, PriceRuleSummary } from './instrumentRisk'
import { riskCoverageText } from './instrumentRisk'
import { PriceRuleCountsLine, PriceRuleDetails } from './PriceRiskRules'
import { researchStamp } from './ResearchRunStatus'
import ConfirmDialog from './ConfirmDialog'
import RiskChangeAudit from './RiskChangeAudit'
import RiskGuidanceSettings from './RiskGuidanceSettings'
import RiskNoticeCorrection, { RiskNoticePreferences } from './RiskNoticeCorrection'
import { useLanguage } from './i18n'
import { resolveWorkspaceUrl } from './navigation'
import './risk-officer.css'

type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'paused'
type StartedRun = { run_id: string; status: RunStatus }
export type RiskNotice = { run_id?: string; notice_id: string; notice_version: number | string; status_note?: string; metric_snapshot_id?: string | null; rule_version?: string | null; receipt?: { unread: boolean; muted: boolean; read_at?: string; muted_until?: string }; level: 'high' | 'medium' | 'low' | null; title: string; body: string; as_of: string | null; origin: 'risk_officer' | 'deterministic'; mandatory?: boolean; current_rule_facts?: Array<{ notice_id?: string; notice_version?: number | string; title: string; body: string; as_of: string | null; metric_snapshot_id?: string | null; rule_version?: number | string | null }>; alert_state?: string; instrument_ids: string[]; holding_ids?: string[]; case_ids: string[]; source_ids?: string[] }
export type RiskOfficerReview = {
  notice_items?: RiskNotice[]
  notice_status?: 'assessed' | 'assessment_pending' | 'unavailable'
  coverage_notes?: string[]
  coverage_items?: Array<{ case_id: string; instrument_id: string; name: string; title: string; body: string }>
  available: boolean
  model_work?: ModelWorkStatus
  reason?: string | null
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
    notice_corrections?: Array<{ notice_id: string; at: string; author: string; reason: string; before: { level: string; title: string; body: string }; after: { level: string; title: string; body: string } }>
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

type Props = { canCorrect?: boolean; canWrite?: boolean; canRun?: boolean; request: RiskRequest; scopeQuery: string; refreshToken?: number; onCompleted?: () => void }
const running = (status: RunStatus | undefined) => status === 'queued' || status === 'running'
const settled = (status: RunStatus | undefined) => status === 'completed' || status === 'failed' || status === 'paused'
const frequencyLabels: Record<string, string> = { daily: '日度', weekly: '周度', monthly: '月度' }
const portfolioHref = (path: string | null | undefined) => path?.startsWith('/portfolios/')
  ? `${resolveWorkspaceUrl(import.meta.env.VITE_PORTFOLIO_URL, 'portfolio')}${path}` : undefined

function ScopedRiskOfficer({ canWrite = true, canCorrect = canWrite, canRun = true, request, scopeQuery, refreshToken, onCompleted }: Props) {
  const zh = useLanguage().language === 'zh-Hans'
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
        polling = Boolean(!result.model_work?.paused && result.latest_run && !settled(result.latest_run.status))
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
  const canSubmit = Boolean(canRun && data?.available && !data?.model_work?.paused && !busy && !unknownStatus && !error)
  useEffect(() => { if (!canSubmit) setConfirming(false) }, [canSubmit])
  const completed = data?.latest_completed
  const names = new Map(data?.instruments.map((instrument) => [instrument.instrument_id, instrument.name]))
  const holdings = new Map(data?.holdings?.map((holding) => [holding.holding_id, holding]))
  const limitations = [...new Set([...(data?.limitations || []), ...(completed?.limitations || [])])]
  const coverageGroups = new Map<string, { names: Map<string, string>; titles: Set<string> }>()
  const coverageInstruments = new Set<string>()
  for (const item of data?.coverage_items || []) {
    const group = coverageGroups.get(item.body) || { names: new Map<string, string>(), titles: new Set<string>() }
    group.names.set(item.instrument_id, item.name || names.get(item.instrument_id) || item.instrument_id)
    if (item.title !== item.body) group.titles.add(item.title)
    coverageInstruments.add(item.instrument_id)
    coverageGroups.set(item.body, group)
  }
  for (const note of data?.coverage_notes || []) {
    if (!coverageGroups.has(note)) coverageGroups.set(note, { names: new Map(), titles: new Set() })
  }
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

  return <section className="risk-officer" aria-label={zh ? "风险提示" : "Risk alerts"}>
    <header className="risk-officer-heading">
      <div><div className="risk-officer-title"><h2>{zh ? "风险提示" : "Risk alerts"}</h2>{data && <InfoHint label="风控输入" detail={`研究上报: ${data.counts.research} · 量化触发事项: ${data.counts.quantitative} · 监测受限: ${data.counts.coverage} · 输入截至: ${data.input_as_of?.slice(0, 10) || '尚无输入日期'}`} />}</div>{data && <p translate="no">{data.scope.name}</p>}</div>
      <button type="button" disabled={!canSubmit} onClick={() => data?.scope.kind === 'portfolio' ? void update() : setConfirming(true)}>
        {data?.model_work?.paused ? '研究已暂停' : busy ? '研判进行中…' : '更新研判'}
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
      {!data.available && <p className="risk-officer-warning" role="status">{data.model_work?.message || data.reason || '风险研判服务不可用，暂时无法更新。'}</p>}
      {busy && <p className="risk-officer-muted" role="status">研判进行中，完成后将自动更新。</p>}
      {unknownStatus && <p className="risk-officer-warning" role="status">任务状态暂未确认；保留已有结论，确认状态前不能重复提交。 <button type="button" onClick={() => setRefresh(value => value + 1)}>刷新状态</button></p>}
      {data.latest_run?.status === 'failed' && !busy && <p className="risk-officer-warning" role="status">
        本次研判失败：{data.latest_run.message || '本次未生成有效结论。'}
      </p>}
      {(data.latest_run?.status === 'paused' || data.model_work?.paused) && <p className="risk-officer-muted">{completed
        ? '本次研判已暂停，下方保留最近完成的结论。'
        : '首次研判已暂停，尚未形成可读结论。'}</p>}
      {!completed && !busy && !data.model_work?.paused && data.latest_run?.status !== 'paused' && <p className="risk-officer-muted">尚未研判。点击“更新研判”发起首次研判。</p>}
      {!!data.notice_items?.length && <ol className="risk-notice-list" aria-label={zh ? '当前风险提示' : 'Current risk alerts'}>{data.notice_items.map(notice => <li key={notice.notice_id}>
        <div className="risk-notice-title"><span className={`risk-notice-level risk-notice-level-${notice.level || 'pending'}`}>{notice.level ? ({ high: zh ? '高' : 'High', medium: zh ? '中' : 'Medium', low: zh ? '低' : 'Low' })[notice.level] : (zh ? '等级待评' : 'Priority pending')}</span><h3 translate="no">{notice.title}</h3></div>
        <p translate="no">{notice.body}</p>{notice.status_note && <p className="risk-officer-warning" translate="no">{notice.status_note}</p>}{notice.current_rule_facts?.map((fact, index) => <p key={index} className="risk-notice-rule-fact" translate="no">{fact.body}</p>)}
        {notice.origin !== 'deterministic' && notice.alert_state === 'assessment_pending' && <p className="risk-officer-muted">{zh ? '新进展待复核；保留原判断及其截至日。' : 'New developments await review; the original assessment and its as-of date are retained.'}</p>}
        <details><summary>{zh ? '依据'  : 'Evidence'}{notice.as_of ? ` · ${notice.as_of.slice(0, 10)}` : ''}</summary>
          <p className="risk-officer-muted">{notice.instrument_ids.map(id => names.get(id) || id).join('、') || (data.scope.kind === 'portfolio' ? (zh ? '组合整体' : 'Portfolio') : '')}</p>
          {notice.origin === 'deterministic' && <p>{zh ? '已批准严重规则的事实提示；分析与等级待补，不等待模型运行。' : 'A factual alert from an approved severe rule. Analysis and priority may follow without delaying this alert.'}</p>}
          {notice.source_ids?.map(id => { const source = completed?.evidence_sources?.[id]; const href = portfolioHref(source?.detail_path); return source ? <p key={id}>{href ? <a href={href} translate="no">{source.title}</a> : <span translate="no">{source.title}</span>} · {source.end_date || '—'}</p> : <p key={id}><code translate="no">{id}</code></p> })}
          {notice.metric_snapshot_id && <p>{zh ? '指标快照' : 'Metric snapshot'}: <code translate="no">{notice.metric_snapshot_id}</code> · {zh ? '规则版本' : 'Rule version'}: <code translate="no">{notice.rule_version}</code></p>}
          {!!completed?.notice_corrections?.filter(change => change.notice_id === notice.notice_id).length && <details><summary>{zh ? '提示更正记录' : 'Alert correction history'}</summary>{completed.notice_corrections.filter(change => change.notice_id === notice.notice_id).map((change, index) => <div key={index}><p>{researchStamp(change.at)} · <span translate="no">{change.author}</span></p><p translate="no">{change.reason}</p><p><strong>{zh ? '更正前' : 'Before'}</strong> <span translate="no">{change.before.title} · {change.before.body}</span></p><p><strong>{zh ? '更正后' : 'After'}</strong> <span translate="no">{change.after.title} · {change.after.body}</span></p></div>)}</details>}
          <RiskNoticePreferences notice={notice} request={request} onSaved={() => setRefresh(value => value + 1)} />
          {canCorrect && notice.run_id && notice.origin === 'risk_officer' && <RiskNoticeCorrection key={`${notice.notice_id}:${notice.notice_version}`} notice={notice} request={request} onSaved={() => setRefresh(value => value + 1)} />}
          <p className="risk-officer-muted">{zh ? '提示版本' : 'Notice version'} {notice.notice_version} · {zh ? '事项引用' : 'Case references'}: <span translate="no">{notice.case_ids.join(', ') || '—'}</span></p>
        </details>
      </li>)}</ol>}
      {data.notice_status === 'assessed' && !data.notice_items?.length && <p>{zh ? '当前暂无需要特别提示的风险。' : 'No risks currently require special attention.'}</p>}
      {data.notice_status === 'unavailable' && !coverageGroups.size && <p className="risk-officer-warning">{zh ? '相关风险暂无法完整评估，已知风险仍保留。' : 'Some risks cannot currently be fully assessed; known risks remain visible.'}</p>}
      {data.notice_status === 'assessment_pending' && !data.notice_items?.length && <p className="risk-officer-muted">{zh ? '当前范围尚未完成筛选，不能据此判断没有风险。' : 'Selection is not yet complete for this scope; an empty list does not establish an absence of risk.'}</p>}
      {!!coverageGroups.size && <div className="risk-officer-coverage">
        <p className="risk-officer-warning" role="status">{zh
          ? `监测受限${coverageInstruments.size ? `：${coverageInstruments.size} 只标的` : ''} · ${coverageGroups.size} 类覆盖缺口。风险筛选尚不完整，已知风险仍保留。`
          : `Coverage limited${coverageInstruments.size ? ` for ${coverageInstruments.size} ${coverageInstruments.size === 1 ? 'instrument' : 'instruments'}` : ''} · ${coverageGroups.size} ${coverageGroups.size === 1 ? 'gap category' : 'gap categories'}. Risk selection is incomplete; known risks remain visible.`}</p>
        <details><summary>{zh ? '查看覆盖缺口与涉及标的' : 'View coverage gaps and affected instruments'}</summary>
          {[...coverageGroups].map(([body, group]) => <div key={body} className="risk-officer-coverage-group">
            {!!group.titles.size && <p translate="no"><strong>{[...group.titles].map(title => riskCoverageText(title, zh)).join(' · ')}</strong></p>}
            <p translate="no">{riskCoverageText(body, zh)}</p>
            {!!group.names.size && <p className="risk-officer-muted" translate="no">{[...group.names.values()].join('、')}</p>}
          </div>)}
        </details>
      </div>}
      {completed && <details className="risk-officer-workpapers"><summary>{zh ? '完整研判与历史依据' : 'Review workpapers and historical evidence'}</summary><section className="risk-officer-conclusion" aria-label="最近完成的研判">
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
      </section></details>}
      <RiskGuidanceSettings request={request} scopeQuery={scopeQuery} instrumentId={data.scope.kind === 'instrument' ? data.scope.id : undefined} canWrite={canWrite} />
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
