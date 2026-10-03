import WorkspaceSkeleton from './WorkspaceSkeleton'
import InfoHint from './InfoHint'
import { researchStamp } from './ResearchRunStatus'
import RiskChangeAudit from './RiskChangeAudit'
import { PriceRuleCountsLine, PriceRuleDetails } from './PriceRiskRules'
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from 'react'
import RiskOfficerPanel from './RiskOfficerPanel'
import type { ResearchAssistantReference } from './researchReference'
import {
  type RiskAsset,
  type PriceRiskPeriod,
  type PeriodLossReading,
  type RiskRequest,
  type RiskWorkspace,
  type RiskCase,
  type RiskAssessment,
  riskPendingState,
  percent,
  today,
} from './instrumentRisk'

const statusLabels: Record<string, string> = {
  open: '待查看',
  investigating: '跟进中',
  handled: '已处理',
}
const due = (c: RiskCase) =>
  c.status !== 'handled' &&
  Boolean(c.follow_up_date && c.follow_up_date <= today())
const attention = (c: RiskCase) =>
  c.trigger_active && c.severity === 'attention' && c.status !== 'handled'
  && (Boolean(c.evidence_json.risk_assessment) || c.evidence_json.direction !== 'opportunity')
const pendingState = (c: RiskCase) => riskPendingState(c.evidence_json.risk_assessment, c.evidence_json.event_version_id)
const pendingReview = (c: RiskCase) => pendingState(c) === 'review'
const pendingVerification = (c: RiskCase) => pendingState(c) === 'verification'
const coverage = (c: RiskCase) => c.trigger_active && c.severity === 'coverage'
const priority = (c: RiskCase) =>
  (attention(c) && c.evidence_json.importance === 'high' ? 4 : 0) +
  (due(c) ? 2 : 0) +
  (attention(c) ? 1 : 0)

function evidenceHref(value: unknown) {
  if (typeof value !== 'string') return undefined
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch {
    return undefined
  }
}

function CaseRow({
  record,
  canWrite,
  asset,
  refresh,
  request,
  assistantHref,
  instrumentHref,
  onAskAssistant,
}: {
  record: RiskCase
  canWrite: boolean
  asset: RiskAsset
  refresh: () => Promise<void>
  request: RiskRequest
  assistantHref?: (id: string, question: string, reference?: ResearchAssistantReference) => string
  instrumentHref: (id: string, signal?: string) => string
  onAskAssistant?: (id: string, question: string, reference?: ResearchAssistantReference) => void
}) {
  const [note, setNote] = useState('')
  const [followUp, setFollowUp] = useState(record.follow_up_date || '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [detail, setDetail] = useState<RiskCase | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailRetry, setDetailRetry] = useState(0)
  useEffect(() => {
    setDetail(null)
    setDetailError('')
    setDetailLoading(false)
    if (!detailsOpen || !record.detail_available) return
    let cancelled = false
    setDetailLoading(true)
    const query = new URLSearchParams({ updated_at: record.updated_at })
    request<RiskCase>(`/risk/cases/${encodeURIComponent(record.case_id)}?${query}`)
      .then((value) => { if (!cancelled) setDetail(value) })
      .catch((failure) => { if (!cancelled) setDetailError(failure instanceof Error ? failure.message : '证据读取失败') })
      .finally(() => { if (!cancelled) setDetailLoading(false) })
    return () => { cancelled = true }
  }, [detailsOpen, record.case_id, record.updated_at, record.detail_available, request, detailRetry])
  const evidence = (detail || record).evidence_json
  const pending = riskPendingState(evidence.risk_assessment, evidence.event_version_id)
  const assessmentReason = pending === 'verification' ? (evidence.risk_assessment as RiskAssessment).reason : null
  const history = (detail || record).history_json || []
  const detailStatus = <>
    {detailLoading && <WorkspaceSkeleton />}
    {detailError && <div role="alert">{detailError} <button type="button" onClick={() => setDetailRetry((value) => value + 1)}>重试读取</button>
      <button type="button" onClick={() => void refresh().catch((failure) => setDetailError(failure instanceof Error ? failure.message : '刷新失败'))}>刷新列表</button></div>}
  </>
  const sectorEvent = record.signal.startsWith('sector:')
  const reference: ResearchAssistantReference = {
    instrument_id: record.instrument_id,
    ...(sectorEvent && typeof evidence.event_version_id === 'string' && evidence.event_version_id
      ? { event_case_id: record.case_id, event_version_id: evidence.event_version_id }
      : { risk_case_id: record.case_id, risk_case_updated_at: record.updated_at }),
  }
  const direction = ({ risk: '风险', opportunity: '机会', uncertain: '重大不确定性' } as Record<string, string>)[String(evidence.direction)]
  const confidence = ({ confirmed: '已确认', reported: '报道线索', unverified: '待证实' } as Record<string, string>)[String(evidence.confidence)]
  const sources = sectorEvent && Array.isArray(evidence.sources) ? evidence.sources as Array<{ url: string; title: string; published_at: string | null }> : []
  const question = `分析 ${asset.name} 的${sectorEvent ? direction || '风险与机会' : '风险'}事项：${record.title}。${record.body} 请核查原因、可能的组合影响和下一步需要补充的证据。`
  const nextStep = String(
    sectorEvent && evidence.next_watch
      ? evidence.next_watch
      : ['drawdown_limit', 'period_loss'].includes(record.signal)
        ? '复核价格或净值跌幅、策略是否偏离，以及原有投资依据是否仍成立。'
        : record.severity === 'coverage'
          ? '核对最新披露或行情来源，再判断风险是否变化。'
          : '核实价格影响与实际敞口，记录判断和下次跟进时间。',
  )
  async function update(status: string, clear = false) {
    if (!canWrite) return
    setBusy(true)
    setError('')
    try {
      await request(`/risk/cases/${record.case_id}`, {
        method: 'PUT',
        body: JSON.stringify({
          status,
          note,
          follow_up_date: followUp || null,
          clear_manual_trigger: clear,
        }),
      })
      setNote('')
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : '更新失败')
    } finally {
      setBusy(false)
    }
  }
  if (evidence.withdrawn === true) return <article className="risk-case">
    <div className="risk-case-meta"><strong>核证撤回</strong>{typeof evidence.withdrawn_at === 'string' && <span>撤回时间 {evidence.withdrawn_at}</span>}</div>
    <h3><a href={instrumentHref(record.instrument_id, record.signal)} translate="no">{record.title}</a></h3>
    <p>{String(evidence.withdrawal_reason || '原有判断未通过核证，已撤回。')}</p>
    <details className="risk-follow-up" onToggle={(event) => setDetailsOpen(event.currentTarget.open)}><summary>撤回前记录与来源</summary>
      {detailStatus}
      <p className="research-muted">以下为已撤回的旧稿，不再作为当前风险判断。</p>
      <p translate="no">{record.body}</p>
      <ul className="risk-event-sources">{sources.map((source, index) => {
        const href = evidenceHref(source.url)
        return <li key={`${source.url}:${index}`}>
          {href ? <a href={href} target="_blank" rel="noopener noreferrer" translate="no">{source.title || source.url}</a> : <span>{source.title || '原始来源'}（链接不可用）</span>}
          <small>{source.published_at ? `发布时间 ${source.published_at}` : '发布时间待核实'}</small>
        </li>
      })}</ul>
      <ul className="risk-history">{history.map((item, index) => <li key={index}><time title={item.at} dateTime={item.at}>{researchStamp(item.at)}</time> · {item.detail}
              {(item.run_id || item.risk_assessment?.run_id) && <details><summary>变更来源</summary><RiskChangeAudit change={item} /></details>}</li>)}</ul>
    </details>
  </article>
  return (
    <article className={`risk-case risk-case-${record.severity}`}>
      <div className="risk-case-meta">
        <span
          className={
            attention(record) && evidence.importance === 'high'
              ? 'risk-priority-high'
              : ''
          }
        >
          {pending === 'verification' ? (record.trigger_active ? '风险仍有效 · 部分事项待核实' : '风险线索待核实')
            : pending === 'review' ? (record.trigger_active ? '风险仍有效 · 新进展待复核' : '风险线索待复核') : sectorEvent && direction
            ? evidence.risk_assessment && record.trigger_active ? '风险' : direction
            : record.severity === 'coverage'
              ? '监测受限'
              : record.severity === 'observation'
                ? '一般记录'
                : evidence.importance === 'high'
                  ? '优先核查'
                  : '需要复核'}
        </span>
        <span>
          {pending === 'verification' ? '已评估／待核实' : pending === 'review' && !record.trigger_active ? '已提交／待复核' : record.trigger_active ? statusLabels[record.status] : '当前未触发'}
        </span>
      </div>
      <h3><a href={instrumentHref(record.instrument_id, record.signal)} translate="no">{record.title}</a></h3>
      <p translate="no">{record.body}</p>
      {assessmentReason && <p className="risk-next-step"><strong>风控评估</strong> <span translate="no">{assessmentReason}</span></p>}
      {record.trigger_active && record.severity !== 'observation' && (
        <p className="risk-next-step">
          <strong>下一步</strong> {nextStep}
        </p>
      )}
      {['drawdown_limit', 'period_loss'].includes(record.signal) &&
        (coverageFor(asset) ||
          (record.signal === 'period_loss' &&
            asset.period_readings?.some((row) => row.limitation))) && (
          <p className="risk-due">
            最新数据不足，保留此前触发状态，尚不能确认恢复。
          </p>
        )}
      <div className="risk-case-meta">
        <span>
          {record.observed_on
            ? `依据日期 ${record.observed_on}`
            : '依据日期待核对'}
          {record.follow_up_date && record.status !== 'handled' && (
            <span className={due(record) ? 'risk-due' : ''}>
              {' '}
              · {due(record) ? '到期待跟进' : '下次跟进'}{' '}
              {record.follow_up_date}
            </span>
          )}
        </span>
        {onAskAssistant ? (
          <button
            className="risk-text-button"
            onClick={() => onAskAssistant(record.instrument_id, question, reference)}
          >
            问助手
          </button>
        ) : assistantHref ? (
          <a href={assistantHref(record.instrument_id, question, reference)}>问助手</a>
        ) : null}
      </div>
      <details className="risk-follow-up" onToggle={(event) => setDetailsOpen(event.currentTarget.open)}>
        <summary>跟进与证据</summary>
        {detailStatus}
        {typeof evidence.run_id === 'string' && <p className="risk-source"><span>来源任务</span> <code translate="no">{evidence.run_id}</code></p>}
        {sectorEvent && confidence && <p className="risk-source">证据状态：{confidence}</p>}
        {sources.length > 0 && <ul className="risk-event-sources">
          {sources.map((source, index) => {
            const href = evidenceHref(source.url)
            const title = source.title || source.url || '原始来源'
            return <li key={`${source.url}:${index}`}>
              {href ? <a href={href} target="_blank" rel="noopener noreferrer">{title}</a> : <span>{title}（链接不可用）</span>}
              <small>{source.published_at ? `发布时间 ${source.published_at}` : '发布时间待核实'}</small>
            </li>
          })}
        </ul>}
        {evidence.source ? (
          <p className="risk-source">来源：{String(evidence.source)}</p>
        ) : null}
        {evidence.recorded_on ? (
          <p className="research-muted">
            研究记录日期 {String(evidence.recorded_on)}
          </p>
        ) : null}
        {record.signal === 'drawdown_limit' && (!record.detail_available || detail) && (
          <p className="research-muted">
            触发读数 {percent(Number(evidence.current_drawdown))} · 回撤复核线{' '}
            {percent(Number(evidence.review_line))}
          </p>
        )}
        {record.signal === 'period_loss' && (
          <p className="research-muted">
            {((evidence.periods as PeriodLossReading[]) || []).map((row) => (
              <span className="risk-period-evidence" key={row.period}>
                {row.label}：{row.start_date} → {row.end_date} ·{' '}
                {percent(row.return_pct)} · 复核线 −{row.limit_pct}%
              </span>
            ))}
          </p>
        )}
        {canWrite && <>
        <label>
          处理记录
          <textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <label>
          下次跟进
          <input
            type="date"
            value={followUp}
            onChange={(e) => setFollowUp(e.target.value)}
          />
        </label>
        {sectorEvent && !record.follow_up_date && <p className="research-muted">尚未设置跟进日期；正文中的验证日期不会自动计入到期提醒。请核对事件是否已发生并设置下一次跟进。</p>}
        <div className="risk-actions">
          <button disabled={busy} onClick={() => void update('investigating')}>
            保存跟进
          </button>
          <button
            disabled={busy}
            onClick={() =>
              void update(record.status === 'handled' ? 'open' : 'handled')
            }
          >
            {record.status === 'handled' ? '重新打开' : '标记已处理'}
          </button>
          {record.signal === 'manual' && record.trigger_active && (
            <button
              disabled={busy}
              onClick={() => void update('handled', true)}
            >
              确认风险已解除
            </button>
          )}
        </div>
        <p className="research-muted">
          {sectorEvent ? '已处理表示完成本次跟进；事项仍需关注时，提醒会保留。' : '已处理表示完成本次跟进；风险条件仍存在时，提醒会保留。'}
        </p>
        </>}
        {error && <p role="alert">{error}</p>}
        <ul className="risk-history">
          {history.map((item, i) => (
            <li key={i}>
              <time title={item.at} dateTime={item.at}>{researchStamp(item.at)}</time> · {item.detail}
              {(item.run_id || item.risk_assessment?.run_id) && <details><summary>变更来源</summary><RiskChangeAudit change={item} /></details>}
            </li>
          ))}
        </ul>
      </details>
    </article>
  )
}

function coverageFor(asset: RiskAsset) {
  return (
    asset.risk?.data_quality?.status !== 'ready' || asset.freshness !== 'fresh'
  )
}

function Readings({ asset }: { asset: RiskAsset }) {
  const labels: Record<string, string> = {
    'Rolling Ann. Vol': '滚动年化波动率',
    'Current Drawdown': '当前回撤',
    'Latest Monthly Drawdown': '最新月度回撤',
    'Recent Return Pressure': '近期收益压力',
  }
  const local = (value: unknown) =>
    String(value ?? '—')
      .replace('Median month ', '月度中位数 ')
      .replace('Median ', '中位数 ')
      .replace('Worst ', '最深 ')
      .replace('% of worst', '%（占历史最深幅度）')
      .replace(' trailing down month(s)', ' 个连续下跌月')
  return (
    <>
      <div className="risk-reading-summary">
        <span>
          当前回撤 <strong>{percent(asset.risk?.current_drawdown)}</strong>
        </span>
        <span>
          样本最大回撤{' '}
          <strong>{percent(asset.risk?.drawdown_summary?.maximum)}</strong>
        </span>
        <span title={asset.previous_observation_date || undefined}>
          较上次观察{' '}
          {asset.drawdown_change_pp == null
            ? '—'
            : `${asset.drawdown_change_pp < 0 ? '加深' : asset.drawdown_change_pp > 0 ? '收窄' : '持平'} ${Math.abs(asset.drawdown_change_pp).toFixed(2)} 个百分点`}
        </span>
      </div>
      {coverageFor(asset) && (
        <p className="research-muted">
          监测受限：需核对最新数据及样本完整性，当前读数不能确认风险是否恢复。
        </p>
      )}
      {Boolean(asset.period_readings?.length) && (
        <>
          <div className="research-table-scroll risk-period-table">
            <table aria-label={`${asset.name} 区间跌幅监测`}>
              <thead>
                <tr>
                  <th>滚动区间</th>
                  <th>区间涨跌</th>
                  <th>下跌复核线</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {asset.period_readings!.map((row) => (
                  <tr
                    key={row.period}
                    className={
                      row.breached && !coverageFor(asset)
                        ? 'risk-period-breached'
                        : ''
                    }
                    title={
                      row.limitation ||
                      `${row.start_date} → ${row.end_date} · ${row.observations} 个交易观察值`
                    }
                  >
                    <td>
                      {row.label}
                      <small>{row.observations} 个交易观察值</small>
                    </td>
                    <td>{percent(row.return_pct)}</td>
                    <td>
                      {row.limit_pct == null ? '未设置' : `−${row.limit_pct}%`}
                    </td>
                    <td>
                      {row.limitation || coverageFor(asset)
                        ? '监测受限'
                        : row.limit_pct == null
                          ? '仅展示读数'
                          : row.breached
                            ? '需复核'
                            : '未触发'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="research-muted">
            {asset.return_kind === 'price_return'
              ? '价格收益'
              : asset.return_kind === 'total_return'
                ? '总收益（含分红）'
                : '净值收益'}{' '}
            · 滚动区间，非本周／本月累计；截至{' '}
            {asset.period_readings?.[0]?.end_date || '—'}。
            {asset.price_risk_note && ` ${asset.price_risk_note}。`}
          </p>
        </>
      )}
      {Boolean(asset.risk?.risk_change_monitor?.rows?.length) && (
        <details className="risk-history-readings">
          <summary>其他风险读数</summary>
          <div className="research-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>相对自身历史</th>
                  <th>当前</th>
                  <th>历史参照</th>
                </tr>
              </thead>
              <tbody>
                {asset.risk!.risk_change_monitor!.rows!.map((row, index) => (
                  <tr key={index}>
                    <td>{labels[String(row.signal)] || String(row.signal)}</td>
                    <td>{local(row.current)}</td>
                    <td>{local(row.baseline)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </>
  )
}

const pricePeriods: Array<[PriceRiskPeriod, string]> = [
  ['day', '日跌幅'],
  ['week', '周跌幅'],
  ['month', '月跌幅'],
  ['quarter', '季度跌幅'],
]

function PriceRiskSettings({
  canWrite,
  asset,
  request,
  onSaved,
}: {
  asset: RiskAsset
  request: RiskRequest
  onSaved: () => Promise<void>
  canWrite: boolean
}) {
  const initial = () => ({
    drawdown: asset.drawdown_limit?.toString() || '',
    ...Object.fromEntries(
      pricePeriods.map(([key]) => [
        key,
        asset.period_limits?.[key]?.toString() || '',
      ]),
    ),
  })
  const [values, setValues] = useState<Record<string, string>>(initial)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    setValues(initial())
  }, [asset.drawdown_limit, asset.period_limits])
  const calibration = asset.price_risk_calibration
  return (
    <details className="risk-price-settings">
      <summary>提醒设置</summary>
      {asset.price_rule_summary && <>
        <PriceRuleCountsLine counts={asset.price_rule_summary.counts} />
        <details><summary>当前规则与观察值</summary><PriceRuleDetails summary={asset.price_rule_summary} /></details>
      </>}
      <p className="research-muted">
        下跌幅度达到复核线才生成提醒；填正数，留空关闭。设置对该标的生效，在不同观察列表和组合中共用。
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault()
          if (!canWrite) return
          setBusy(true)
          setMessage('')
          setFailed(false)
          try {
            await request(`/risk/rules/${asset.instrument_id}`, {
              method: 'PUT',
              body: JSON.stringify({
                drawdown_limit: values.drawdown
                  ? Number(values.drawdown)
                  : null,
                period_limits: Object.fromEntries(
                  pricePeriods.map(([key]) => [
                    key,
                    values[key] ? Number(values[key]) : null,
                  ]),
                ),
              }),
            })
            await onSaved()
            setMessage('提醒设置已保存')
          } catch (error) {
            setFailed(true)
            setMessage(error instanceof Error ? error.message : '保存失败')
          } finally {
            setBusy(false)
          }
        }}
      >
        <div className="risk-price-inputs">
          {[...pricePeriods, ['drawdown', '高点回撤']].map(([key, label]) => (
            <label key={key}>
              {label}（%）
              <input
                disabled={!canWrite}
                aria-label={`${asset.name} ${label}复核线`}
                type="number"
                min="0.01"
                max="100"
                step="any"
                placeholder="未设置"
                value={values[key] || ''}
                onChange={(event) =>
                  setValues({ ...values, [key]: event.target.value })
                }
              />
            </label>
          ))}
        </div>
        <div className="risk-settings-save">
          {canWrite && <button disabled={busy}>{busy ? '保存中…' : '保存提醒设置'}</button>}
          <span role={failed ? 'alert' : 'status'}>{message}</span>
        </div>
      </form>
      <details className="risk-initial-method">
        <summary>初值依据</summary>
        <p className="research-muted">
          {calibration?.sample_end
            ? <><span>校准样本</span> {calibration.sample_start} → {calibration.sample_end} · {calibration.observations} <span>个日收益观察值</span> · <span>日波动率</span> {percent(calibration.daily_volatility_pct)}。{calibration.manually_edited && <span>当前复核线已手动调整。</span>}</>
            : '日度样本完整且足够时，才按自身历史波动生成初值。'}
        </p>
        <p className="research-muted">常规市场使用最近最多 252、至少 63 个日对数收益；24/7 市场最多 365、至少 90 个。</p>
        <p className="research-muted">日、周、月、季度分别取 3／2.5／2／1.5 倍区间波动，最低跌幅为 0.5%／1%／2%／3%，向上取整到 0.5 个百分点。</p>
        <p className="research-muted">保存后固定，不随波动升高自动放宽。这是初始复核容忍度，不是亏损概率或买卖指令；高点回撤线另行设置。</p>
      </details>
    </details>
  )
}

function ScopedInstrumentRiskPanel({
  canWrite = true,
  canRun = canWrite,
  portfolioId,
  instrumentId,
  focusInstrumentId,
  query = '',
  request,
  instrumentHref,
  assistantHref,
  onAskAssistant,
  onChanged,
  attentionLabel = '风险关注',
  scopeLabel = '当前标的',
  scopeNote,
  instrumentContext,
  heading = '近期风险与跟进',
}: {
  canWrite?: boolean
  canRun?: boolean
  portfolioId?: string
  instrumentId?: string
  focusInstrumentId?: string
  query?: string
  request: RiskRequest
  instrumentHref: (id: string, signal?: string) => string
  assistantHref?: (id: string, question: string, reference?: ResearchAssistantReference) => string
  onAskAssistant?: (id: string, question: string, reference?: ResearchAssistantReference) => void
  onChanged?: () => void
  attentionLabel?: string
  scopeLabel?: string
  scopeNote?: ReactNode
  instrumentContext?: (id: string) => ReactNode
  heading?: string | null
}) {
  const [data, setData] = useState<RiskWorkspace | null>(null)
  const readSequence = useRef(0)
  const mounted = useRef(false)
  const [officerRefresh, setOfficerRefresh] = useState(0)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('attention')
  const [manual, setManual] = useState(false)
  const [eventInstrument, setEventInstrument] = useState(
    instrumentId || focusInstrumentId || '',
  )
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [importance, setImportance] = useState('medium')
  const [source, setSource] = useState('')
  const [followUp, setFollowUp] = useState('')
  const [busy, setBusy] = useState(false)
  const params = new URLSearchParams(query)
  if (instrumentId) params.set('instrument_id', instrumentId)
  params.set('summary', 'true')
  const path = `/risk?${params}`
  const officerScope = portfolioId
    ? new URLSearchParams({ portfolio_id: portfolioId }).toString()
    : params.has('watchlist_id')
      ? new URLSearchParams({ watchlist_id: params.get('watchlist_id')! }).toString()
      : params.has('instrument_id')
        ? new URLSearchParams({ instrument_id: params.get('instrument_id')! }).toString()
        : null
  function receive(response: RiskWorkspace) {
    setData(response)
    const relevant = response.cases.filter(record => !focusInstrumentId || record.instrument_id === focusInstrumentId)
    setFilter(current => {
      if (current === 'pending' && !relevant.some(pendingReview)) return relevant.some(pendingVerification) ? 'verification' : 'attention'
      if (current === 'verification' && !relevant.some(pendingVerification)) return relevant.some(pendingReview) ? 'pending' : 'attention'
      return current
    })
  }
  async function load() {
    const sequence = ++readSequence.current
    const response = await request<RiskWorkspace>(path)
    if (!mounted.current || sequence !== readSequence.current) return false
    receive(response)
    return true
  }
  useEffect(() => {
    let cancelled = false
    mounted.current = true
    const sequence = ++readSequence.current
    setData(null)
    setError('')
    setManual(false)
    setFilter('attention')
    request<RiskWorkspace>(path)
      .then((response) => {
        if (cancelled || sequence !== readSequence.current) return
        receive(response)
        const relevant = response.cases.filter((record) => !focusInstrumentId || record.instrument_id === focusInstrumentId)
        if (!relevant.some(attention)) {
          if (relevant.some(pendingReview)) setFilter('pending')
          else if (relevant.some(pendingVerification)) setFilter('verification')
        }
      })
      .catch((e) => {
        if (!cancelled && sequence === readSequence.current) setError(e.message)
      })
    return () => {
      cancelled = true
      mounted.current = false
      ++readSequence.current
    }
  }, [path, request, focusInstrumentId])
  async function refresh(refreshOfficer = true) {
    if (!mounted.current || !await load()) return
    if (refreshOfficer) setOfficerRefresh((value) => value + 1)
    onChanged?.()
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await action()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  const cases = data?.cases || []
  const shown = cases
    .filter(
      (c) =>
        filter === 'all' ||
        (filter === 'attention'
          ? attention(c)
          : filter === 'pending'
            ? pendingReview(c)
          : filter === 'verification'
            ? pendingVerification(c)
          : filter === 'coverage'
            ? coverage(c)
            : due(c)),
    )
    .sort(
      (a, b) =>
        priority(b) - priority(a) || b.updated_at.localeCompare(a.updated_at),
    )
  const groups = (data?.instruments || [])
    .map((asset) => ({
      asset,
      cases: shown.filter((c) => c.instrument_id === asset.instrument_id),
    }))
    .filter(
      (group) =>
        group.cases.length || group.asset.instrument_id === focusInstrumentId,
    )
    .sort(
      (a, b) =>
        Number(b.asset.instrument_id === focusInstrumentId) -
          Number(a.asset.instrument_id === focusInstrumentId) ||
        (b.cases.length ? priority(b.cases[0]) : 0) -
          (a.cases.length ? priority(a.cases[0]) : 0) ||
        (b.cases[0]?.updated_at || '').localeCompare(a.cases[0]?.updated_at || ''),
    )
  const tabs = [
    {
      key: 'attention',
      label: attentionLabel,
      count: cases.filter(attention).length,
    },
    ...(cases.some(pendingReview) ? [{ key: 'pending', label: '待风控复核', count: cases.filter(pendingReview).length }] : []),
    ...(cases.some(pendingVerification) ? [{ key: 'verification', label: '待核实', count: cases.filter(pendingVerification).length }] : []),
    { key: 'due', label: '到期待跟进', count: cases.filter(due).length },
    {
      key: 'coverage',
      label: '监测受限',
      count: cases.filter(coverage).length,
    },
    { key: 'all', label: '全部记录', count: cases.length },
  ]
  return (
    <section className="research-workbench risk-workbench">
      {officerScope && <RiskOfficerPanel canRun={canRun} request={request} scopeQuery={officerScope} refreshToken={officerRefresh}
        onCompleted={() => { void refresh(false).catch((failure) => setError(failure instanceof Error ? failure.message : '风险事项读取失败')) }} />}
      <div className="research-page-heading">
        <div>
          {heading && <h2>{heading}</h2>}
          <p className="risk-scope-label">
            <span translate="no">{scopeLabel}</span>
            {data ? ` · ${data.instruments.length} 个标的` : ''}
            {data && <InfoHint label="价格监测范围" detail={`已设复核线 ${data.instruments.filter((item) => item.drawdown_limit != null || Object.values(item.period_limits || {}).some((value) => value != null)).length}/${data.instruments.length}；可计算 ${data.instruments.filter((item) => !coverageFor(item)).length}/${data.instruments.length}。未触发提醒不代表风险低。`} />}
          </p>
        </div>
        {canWrite && <button
          className="risk-text-button"
          onClick={() => {
            setEventInstrument(
              data?.instruments.find(
                (i) => i.instrument_id === (instrumentId || focusInstrumentId),
              )?.instrument_id || '',
            )
            setManual(!manual)
          }}
        >
          {manual ? '取消记录' : '补充记录'}
        </button>}
      </div>
      {scopeNote && <div className="risk-scope-note">{scopeNote}</div>}
      {error && (
        <div className="error-state" role="alert">
          {error}
        </div>
      )}
      {!data && !error && <WorkspaceSkeleton />}
      {data && (
        <>
          <nav className="risk-sections" aria-label="风险事项">
            <div>
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  aria-pressed={filter === tab.key}
                  onClick={() => setFilter(tab.key)}
                >
                  {tab.label} <span>{tab.count}</span>
                </button>
              ))}
            </div>
          </nav>
          {manual && canWrite && (
            <form
              className="risk-event-form"
              onSubmit={(e) => {
                e.preventDefault()
                void perform(async () => {
                  await request('/risk/cases', {
                    method: 'POST',
                    body: JSON.stringify({
                      instrument_id: eventInstrument,
                      title: title.trim(),
                      body,
                      importance,
                      source,
                      follow_up_date: followUp || null,
                    }),
                  })
                  setTitle('')
                  setBody('')
                  setSource('')
                  setFollowUp('')
                  setManual(false)
                  setFilter(importance === 'low' ? 'all' : 'attention')
                })
              }}
            >
              <h3>记录风险事件</h3>
              {!instrumentId && (
                <label>
                  事件涉及的标的
                  <select
                    required
                    value={eventInstrument}
                    onChange={(e) => setEventInstrument(e.target.value)}
                  >
                    <option value="">从当前范围选择</option>
                    {data.instruments.map((i) => (
                      <option key={i.instrument_id} value={i.instrument_id}>
                        {i.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label>
                关注程度
                <select
                  value={importance}
                  onChange={(e) => setImportance(e.target.value)}
                >
                  <option value="high">优先核查</option>
                  <option value="medium">需要复核</option>
                  <option value="low">一般记录，不提醒</option>
                </select>
              </label>
              <label>
                事件标题
                <input
                  required
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label>
                已知事实与可能影响
                <textarea
                  rows={2}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              </label>
              <label>
                来源与日期
                <input
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  placeholder="公告、新闻或研究资料的出处和日期"
                />
              </label>
              <label>
                下次跟进
                <input
                  type="date"
                  value={followUp}
                  onChange={(e) => setFollowUp(e.target.value)}
                />
              </label>
              <button disabled={busy || !eventInstrument || !title.trim()}>
                保存风险事件
              </button>
            </form>
          )}
          <div className="risk-case-list">
            {groups.map(({ asset, cases: records }) => (
              <section
                key={asset.instrument_id}
                className={`risk-instrument-group${asset.instrument_id === focusInstrumentId ? ' risk-instrument-focused' : ''}`}
              >
                <header>
                  <div>
                    <a
                      translate="no"
                      href={instrumentHref(asset.instrument_id)}
                    >
                      {asset.name}
                    </a>
                    {asset.instrument_id === focusInstrumentId && (
                      <small>已定位</small>
                    )}
                  </div>
                  <small>
                    {asset.as_of_date
                      ? `数据 ${asset.as_of_date}`
                      : '数据日期待核对'}
                  </small>
                </header>
                {instrumentContext && (
                  <div className="risk-instrument-context">
                    {instrumentContext(asset.instrument_id)}
                  </div>
                )}
                {records.map((c) => (
                  <CaseRow
                    key={c.case_id}
                    canWrite={canWrite}
                    record={c}
                    asset={asset}
                    refresh={refresh}
                    instrumentHref={instrumentHref}
                    request={request}
                    assistantHref={assistantHref}
                    onAskAssistant={onAskAssistant}
                  />
                ))}
                {!records.length && (
                  <p className="research-muted">该标的在此分类下暂无事项。</p>
                )}
              </section>
            ))}
            {!shown.length && (
              <div className="risk-empty">
                {filter === 'attention'
                  ? '当前范围没有触发中的重点风险事项。'
                  : filter === 'due'
                    ? '当前没有到期待跟进事项。'
                    : filter === 'coverage'
                      ? '当前范围未发现监测受限事项。'
                      : '当前范围尚无风险记录。'}
                {filter === 'attention' && (
                  <small>
                    普通价格波动与样本新低保留在读数中；未触发提醒不代表风险低。
                  </small>
                )}
              </div>
            )}
          </div>
          <details
            className="research-section risk-readings"
          >
            <summary>价格风险与提醒设置</summary>
            {data.instruments.map((asset) => (
              <details
                className="risk-reading-asset"
                key={asset.instrument_id}
                open={
                  Boolean(instrumentId) ||
                  asset.instrument_id === focusInstrumentId
                }
              >
                <summary>
                  <span translate="no">{asset.name}</span>
                  <small>
                    回撤 {percent(asset.risk?.current_drawdown)} ·{' '}
                    {asset.as_of_date || '日期待核对'}
                  </small>
                </summary>
                <Readings asset={asset} />
                <PriceRiskSettings
                  canWrite={canWrite}
                  asset={asset}
                  request={request}
                  onSaved={refresh}
                />
              </details>
            ))}
          </details>
          <details className="risk-coverage-note">
            <summary>当前监测范围</summary>
            <p>
              自动关注日、周、月、季度跌幅及已设置的高点回撤线；事件与研究风险记录按重要程度呈现。数据缺失或滞后单列，不能用来确认风险解除。
            </p>
            <p>
              自动价格提醒依据行情计算；事件分析的来源覆盖与限制请查看相应检查记录。
            </p>
          </details>
        </>
      )}
    </section>
  )
}

export default function InstrumentRiskPanel(props: ComponentProps<typeof ScopedInstrumentRiskPanel>) {
  return <ScopedInstrumentRiskPanel key={`${props.portfolioId || ''}:${props.instrumentId || ''}:${props.query || ''}`} {...props} />
}
