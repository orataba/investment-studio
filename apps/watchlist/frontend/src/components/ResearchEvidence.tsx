import { useEffect, useState } from 'react'
import type { EventSource } from '../lib/researchDossierApi'
import { API_BASE_URL } from '../lib/api'
import { getSavedResearchSource, type NotebookSource, type SavedComparison, type SavedResearchSource } from '../lib/researchDossierApi'

export const hasTimeZone = (value: string) => /(Z|[+-]\d{2}:\d{2})$/i.test(value)

export function dateLabel(value?: string | null) {
  if (!value) return '时间未知'
  if (value.length === 10 || Number.isNaN(Date.parse(value))) return value
  if (!hasTimeZone(value)) return `${value}（时区未披露）`
  return new Date(value).toLocaleString('zh-CN', { hour12: false, timeZoneName: 'short' })
}

export function sourceUrl(value?: string) {
  if (value?.startsWith('/api/')) return `${API_BASE_URL || ''}${value}`
  try { const url = new URL(value || ''); return ['http:', 'https:'].includes(url.protocol) ? url.href : null } catch { return null }
}

const evidenceNumber = (value: number | null | undefined, unit = '') => typeof value === 'number' && Number.isFinite(value)
  ? `${value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })}${unit}` : '未取得'
const comparisonRoles: Record<string, string> = { configured_benchmark: '已配置基准', taxonomy_peer: '已登记分类同类', configured_peer: '指定对照' }
const evidenceLabels: Record<string, string> = {
  daily: '日度', weekly: '周度', monthly: '月度', total_return: '总收益', price_return: '价格收益', unit_nav_return: '单位净值收益',
  nav: '单位净值', nav_with_dividend: '复权累计净值', close: '收盘价', adjusted_close: '复权收盘价', last: '最新价',
}
const evidenceLabel = (value?: string) => value ? evidenceLabels[value] || value : '未披露'

function ComparisonEvidence({ comparison }: { comparison: SavedComparison }) {
  return <div className="research-computed-comparison">
    <p>共同样本 {comparison.sample_start || '未取得'} 至 {comparison.sample_end || '未取得'} · 实际观察数 {comparison.observations ?? '未取得'}{comparison.currency && ` · ${comparison.currency}`}</p>
    {Boolean(comparison.rows?.length) && <table><thead><tr><th>标的</th><th>区间收益</th><th>最大回撤</th><th>与目标相关性</th><th>较基准超额收益</th></tr></thead><tbody>
      {comparison.rows!.map(row => <tr key={row.instrument_id}><td><span translate="no">{row.name || row.instrument_id}</span><small>{evidenceLabel(row.return_kind)} · {evidenceLabel(row.quote_basis)}</small></td><td>{evidenceNumber(row.return_pct, '%')}</td><td>{evidenceNumber(row.max_drawdown_pct, '%')}</td><td>{evidenceNumber(row.correlation_to_target)}</td><td>{evidenceNumber(row.excess_return_pp, ' 个百分点')}</td></tr>)}
    </tbody></table>}
    {comparison.method && <p className="sector-research-note" translate="no">{comparison.method}</p>}
    {Boolean(comparison.limitations?.length) && <ul className="research-dossier-list">{comparison.limitations!.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul>}
  </div>
}

export function ComputedEvidence({ source }: { source: SavedResearchSource }) {
  const [showOriginal, setShowOriginal] = useState(false)
  const data = source.data
  const method = typeof source.methodology === 'object' ? source.methodology : null
  const number = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
  const change = (value: number) => `${value > 0 ? '+' : ''}${number(value)} 个百分点`
  return <div className="research-computed-evidence">
    {typeof source.methodology === 'string' && <p className="sector-research-note" translate="no">{source.methodology}</p>}
    {data?.available === false && <p>本次计算未取得可用结果。</p>}
    {data && 'sample_return_pct' in data && <>
      <p>实际样本 {data.sample_start || '未取得'} 至 {data.sample_end || '未取得'} · 实际观察数 {data.observations ?? '未取得'}</p>
      <p>区间收益 {evidenceNumber(data.sample_return_pct, '%')}{data.currency && ` · ${data.currency}`}</p>
      <p className="sector-research-note">频率 <span>{evidenceLabel(data.frequency)}</span> · 收益口径 <span>{evidenceLabel(data.return_kind)}</span> · 报价口径 <span>{evidenceLabel(data.quote_basis)}</span></p>
    </>}
    {data?.rows && <ComparisonEvidence comparison={data} />}
    {data?.comparisons?.map(row => <section key={row.instrument_id}>
      <h4 translate="no">{row.name || row.instrument_id}</h4>
      <p className="sector-research-note">{comparisonRoles[row.role] || '对照'}</p>
      <ComparisonEvidence comparison={row.comparison} />
      {row.note && <p className="sector-research-note" translate="no">{row.note}</p>}
    </section>)}
    {data?.current && <>
      <p>观测日期 <time dateTime={data.current.date}>{data.current.date}</time> · 当前波动率 {number(data.current.volatility_pct)}%</p>
      {data.previous && <p>前次 {number(data.previous.volatility_pct)}% · {data.previous.date}</p>}
      {data.change_pp != null && <p>较前次 {change(data.change_pp)}</p>}
      {data.five_session_change_pp != null && <p>近 5 个交易观察 {change(data.five_session_change_pp)}</p>}
      {method?.half_life_sessions != null && <p>EWMA 半衰期 {method.half_life_sessions} 个交易观察</p>}
      {method?.annualization != null && <p>年化观察数 {method.annualization}</p>}
    </>}
    {data?.series?.map(series => <div key={series.series_id}><h4 translate="no">{series.series_id}</h4>
      {series.first && series.latest ? <>
        <p>{series.first.date}：{number(series.first.value)} → {series.latest.date}：{number(series.latest.value)} · {series.unit || '单位未披露'}{series.currency && ` · ${series.currency}`}</p>
        {series.change != null && <p>区间变化 {number(series.change)} {series.change_unit === 'percentage_points' ? '个百分点' : series.change_unit || '单位未披露'}</p>}
      </> : <p>该序列没有可用观察。</p>}
      <p className="sector-research-note">实际观察数 {series.observations}</p>
    </div>)}
    {!data?.rows && Boolean(data?.limitations?.length) && <ul className="research-dossier-list">{data!.limitations!.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul>}
    <details onToggle={event => setShowOriginal(event.currentTarget.open)}><summary>完整计算记录与输入依据</summary>
      {showOriginal && <pre className="research-dossier-text" translate="no">{JSON.stringify(source, null, 2)}</pre>}
    </details>
  </div>
}

function FinancialEvidence({ company }: { company: Record<string, unknown> }) {
  type Row = Record<string, unknown>
  const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((row): row is Row => Boolean(row) && typeof row === 'object') : []
  const financials = rows(company.financials)
  const statements = rows(company.statements)
  const value = (item: unknown) => item === null || item === undefined || item === '' ? '未披露' : typeof item === 'object' ? JSON.stringify(item) : String(item)
  const groups = new Map<string, { statement: Row; facts: Row[] }>()
  statements.forEach((statement, index) => groups.set(String(statement.statement_content_sha256 || `statement:${index}`), { statement, facts: [] }))
  financials.forEach((fact, index) => {
    const key = String(fact.statement_content_sha256 || `fact:${index}`)
    if (!groups.has(key)) groups.set(key, { statement: fact, facts: [] })
    groups.get(key)!.facts.push(fact)
  })
  const directory = company.page_kind === 'statements'
  return <div className="research-financial-evidence">
    <p><strong>{directory ? '已保存的财报目录' : '已保存的财报科目'}</strong> · <span translate="no">{value(company.symbol)}</span></p>
    <p className="sector-research-note">{directory ? '本页是报表目录，不代表已读取各项财务数据。' : '以下仅包含此来源保存的这一页数据，未补入后来资料。'}</p>
    <p className="sector-research-note">本页 {directory ? statements.length : financials.length} 条{typeof company.total_rows === 'number' && <> · 已知共 {company.total_rows} 条</>}{company.next_offset !== null && company.next_offset !== undefined && <> · 后续页未包含在本条来源</>}</p>
    {[...groups].map(([key, group]) => <section key={key}>
      <h4 translate="no">{value(group.statement.statement_type)} · {value(group.statement.period_end)}</h4>
      <p className="sector-research-note"><span>报告期间 </span><span translate="no">{value(group.statement.fiscal_year)} {value(group.statement.fiscal_period)}</span> · <span>报表币种 </span><span translate="no">{value(group.statement.reported_currency)}</span></p>
      <p className="sector-research-note">可用时间 {dateLabel(typeof group.statement.available_at === 'string' ? group.statement.available_at : undefined)} · 取得时间 {dateLabel(typeof group.statement.observed_at === 'string' ? group.statement.observed_at : undefined)}</p>
      {Boolean(group.statement.accepted_at || group.statement.filing_date) && <p className="sector-research-note">披露时间 {dateLabel(typeof group.statement.accepted_at === 'string' ? group.statement.accepted_at : typeof group.statement.filing_date === 'string' ? group.statement.filing_date : undefined)}</p>}
      {group.facts.length > 0 && <table><thead><tr><th>原始科目</th><th>原始数值</th><th>单位</th></tr></thead><tbody>{group.facts.map((fact, index) => <tr key={index}><td translate="no">{value(fact.line_item)}</td><td translate="no">{value(fact.value)}</td><td translate="no">{fact.unit ? value(fact.unit) : '单位未明确'}</td></tr>)}</tbody></table>}
    </section>)}
    {!directory && <p className="sector-research-note">报表币种不等于每个科目的单位；每股指标、比率和股数需分别核实。</p>}
    {typeof company.pagination_note === 'string' && <p className="sector-research-note" translate="no">{company.pagination_note}</p>}
  </div>
}

function SavedEvidence({ source, instrumentId, versionId }: { source: NotebookSource; instrumentId: string; versionId?: string }) {
  const [expanded, setExpanded] = useState(false)
  const [saved, setSaved] = useState<SavedResearchSource | null>(null)
  const [error, setError] = useState('')
  const computed = source.source_type === 'computed_metric'
  useEffect(() => {
    if (!expanded || saved !== null) return
    const controller = new AbortController()
    setError('')
    void getSavedResearchSource(instrumentId, source.source_id, controller.signal, versionId).then(value => {
      if (!controller.signal.aborted) setSaved(value)
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '保存的依据读取失败') })
    return () => controller.abort()
  }, [expanded, instrumentId, source.source_id, saved, versionId])
  const body = saved?.text || saved?.body
  const snapshot = saved?.snapshot || saved?.company
  const financialPage = saved?.company && ['statements', 'facts'].includes(String(saved.company.page_kind))
  return <details onToggle={event => setExpanded(event.currentTarget.open)}><summary>{computed ? '查看已保存的计算依据' : '查看已保存的原文'}</summary>
    <p className="sector-research-note">{computed ? '对应此项判断当时引用的计算结果，未用后来数据重新计算。' : '对应此项研究所引用的原文版本；外部链接可能已发生修改。'}</p>
    {error ? <p role="alert">{error}</p> : saved === null ? <p role="status">正在读取依据…</p> : computed ? <ComputedEvidence source={saved} /> : financialPage ? <FinancialEvidence company={saved.company!} /> : body ? <p className="research-dossier-text" translate="no">{body}</p> : snapshot ? <pre className="research-dossier-text" translate="no">{JSON.stringify(snapshot, null, 2)}</pre> : <p className="sector-research-note">此来源没有可读正文。</p>}
  </details>
}

function SourceMetrics({ source }: { source: EventSource }) {
  const measure = source.measurement
  const current = measure?.current
  const historical = measure?.historical_reference
  const number = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
  const change = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)} 个百分点`
  if (source.source_type === 'computed_metric') return current && <>
    <small>观测日期 <time dateTime={current.date}>{current.date}</time> · 当前波动率 {current.volatility_pct.toFixed(2)}%</small>
    {measure?.previous && <small>前次 {measure.previous.volatility_pct.toFixed(2)}% · {measure.previous.date}</small>}
    {measure?.change_pp != null && <small>较前次 {change(measure.change_pp)}</small>}
    {measure?.five_session_change_pp != null && <small>近 5 个交易观察 {change(measure.five_session_change_pp)}</small>}
    {(source.methodology || historical) && <details><summary>计算口径与历史参考</summary>
      {source.methodology?.half_life_sessions != null && <small>EWMA 半衰期 {source.methodology.half_life_sessions} 个交易观察</small>}
      {source.methodology?.annualization != null && <small>年化观察数 {source.methodology.annualization}</small>}
      {historical && <small>历史参考 {historical.start_date} 至 {historical.end_date} · 中位数 {historical.median_pct.toFixed(2)}% · 范围 {historical.minimum_pct.toFixed(2)}%–{historical.maximum_pct.toFixed(2)}%</small>}
    </details>}
  </>
  if (source.source_type !== 'analyst_estimate_changes') return null
  return <>
    {source.current_snapshot?.collected_at
      ? <small>快照采集：前次 {dateLabel(source.previous_snapshot?.collected_at)} · 本次 {dateLabel(source.current_snapshot.collected_at)}</small>
      : <small>快照读取：前次 {dateLabel(source.previous_snapshot?.read_at)} · 本次 {dateLabel(source.current_snapshot?.read_at)}</small>}
    <small>以下为两次源数据采集之间的观测变化，不能确定精确调整或发布日期；财期是预测对象。</small>
    {source.changes?.some(change => change.current_currency_status === 'inferred_from_reporting_currency') && <small>部分预期币种按公司财报币种推定，来源已留存；预期接口本身未直接披露币种。</small>}
    <details><summary>同财期预期变动 · {source.changes?.length || 0} 项</summary><ul>{source.changes?.map(change => <li key={`${change.symbol}:${change.frequency}:${change.target_period_end}:${change.metric}`}>
      <span>{change.symbol}{change.name ? ` · ${change.name}` : ''} · {change.frequency === 'annual' ? '年度' : change.frequency === 'quarter' ? '季度' : change.frequency}财期截至 {change.target_period_end}</span>
      <small>{change.metric === 'revenue_avg' ? '平均营收预期' : change.metric === 'eps_avg' ? '平均每股收益预期' : change.metric}：{number(change.previous_value)} → {number(change.current_value)} {change.currency || '币种待核实'}{change.metric === 'eps_avg' ? '/股' : ''}{change.delta_pct !== null ? ` · ${change.delta_pct > 0 ? '+' : ''}${change.delta_pct.toFixed(2)}%` : ''}</small>
      <small>源数据采集区间：<time dateTime={change.previous_collected_at || undefined}>{dateLabel(change.previous_collected_at)}</time> → <time dateTime={change.current_collected_at || undefined}>{dateLabel(change.current_collected_at)}</time></small>
      {change.analyst_count_changed && <small>分析师样本数量发生变化，共识变化不等于每位分析师均调整预测。</small>}
    </li>)}</ul></details>
  </>
}

export function SourceList({ sources, instrumentId, versionId }: { sources: Array<NotebookSource & EventSource>; instrumentId?: string; versionId?: string }) {
  return <ul className="sector-event-sources research-notebook-sources">{sources.map((source) => {
    const href = sourceUrl(source.url || source.source)
    const publishedAt = source.published_at || source.metadata?.published_at
    const retrievedAt = source.retrieved_at || source.recorded_at
    return <li key={`${source.source_id}:${source.version_id || ''}:${versionId || ''}`}>{href ? <a href={href} target="_blank" rel="noopener noreferrer" translate="no">{source.title || '原始资料'}</a> : <span translate="no">{source.title || source.source || '已保存的来源记录'}</span>}
      {source.source_type === 'computed_metric' ? <small>计算截至 <time dateTime={source.as_of || undefined}>{dateLabel(source.as_of)}</time></small>
        : ['instrument_snapshot', 'sector_snapshot', 'company_snapshot'].includes(source.source_type || '') ? <small>研究快照截至 <time dateTime={source.run_cutoff || undefined}>{dateLabel(source.run_cutoff)}</time></small>
        : source.source_type === 'analyst_estimate_changes' ? null : <small>发布 <time dateTime={publishedAt || undefined}>{dateLabel(publishedAt)}</time> · 取得 <time dateTime={retrievedAt || undefined}>{dateLabel(retrievedAt)}</time></small>}
      <SourceMetrics source={source} />
      {source.pm_binding_note && <small translate="no">{source.pm_binding_note}</small>}
      {(versionId || ['computed_metric', 'company_snapshot', 'instrument_snapshot', 'sector_snapshot'].includes(source.source_type || '') || (source.document_id && source.version_id)) && (instrumentId || source.instrument_id) && <SavedEvidence source={source} instrumentId={(instrumentId || source.instrument_id)!} versionId={versionId} />}
    </li>
  })}</ul>
}
