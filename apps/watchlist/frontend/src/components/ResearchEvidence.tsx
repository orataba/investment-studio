import { useEffect, useState } from 'react'
import type { EventSource } from '../lib/researchDossierApi'
import { API_BASE_URL } from '../lib/api'
import { getSavedResearchSource, type NotebookSource, type SavedResearchSource } from '../lib/researchDossierApi'

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

function ComputedEvidence({ source }: { source: SavedResearchSource }) {
  const data = source.data
  const method = typeof source.methodology === 'object' ? source.methodology : null
  const number = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
  const change = (value: number) => `${value > 0 ? '+' : ''}${number(value)} 个百分点`
  return <div>
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
    {Boolean(data?.limitations?.length) && <ul className="research-dossier-list">{data!.limitations!.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul>}
  </div>
}

function SavedEvidence({ source, instrumentId }: { source: NotebookSource; instrumentId: string }) {
  const [expanded, setExpanded] = useState(false)
  const [saved, setSaved] = useState<SavedResearchSource | null>(null)
  const [error, setError] = useState('')
  const computed = source.source_type === 'computed_metric'
  useEffect(() => {
    if (!expanded || saved !== null) return
    const controller = new AbortController()
    setError('')
    void getSavedResearchSource(instrumentId, source.source_id, controller.signal).then(value => {
      if (!controller.signal.aborted) setSaved(value)
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '保存的依据读取失败') })
    return () => controller.abort()
  }, [expanded, instrumentId, source.source_id, saved])
  const body = saved?.text || saved?.body
  return <details onToggle={event => setExpanded(event.currentTarget.open)}><summary>{computed ? '查看已保存的计算依据' : '查看已保存的原文'}</summary>
    <p className="sector-research-note">{computed ? '对应此项判断当时引用的计算结果，未用后来数据重新计算。' : '对应此项研究所引用的原文版本；外部链接可能已发生修改。'}</p>
    {error ? <p role="alert">{error}</p> : saved === null ? <p role="status">正在读取依据…</p> : computed ? <ComputedEvidence source={saved} /> : body ? <p className="research-dossier-text" translate="no">{body}</p> : <p className="sector-research-note">此来源没有可读正文。</p>}
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

export function SourceList({ sources, instrumentId }: { sources: Array<NotebookSource & EventSource>; instrumentId?: string }) {
  return <ul className="sector-event-sources research-notebook-sources">{sources.map((source) => {
    const href = sourceUrl(source.url || source.source)
    const publishedAt = source.published_at || source.metadata?.published_at
    const retrievedAt = source.retrieved_at || source.recorded_at
    return <li key={source.source_id}>{href ? <a href={href} target="_blank" rel="noopener noreferrer" translate="no">{source.title || '原始资料'}</a> : <span translate="no">{source.title || source.source || '已保存的来源记录'}</span>}
      {source.source_type === 'computed_metric' ? <small>计算截至 <time dateTime={source.as_of || undefined}>{dateLabel(source.as_of)}</time></small>
        : ['instrument_snapshot', 'sector_snapshot', 'company_snapshot'].includes(source.source_type || '') ? <small>研究快照截至 <time dateTime={source.run_cutoff || undefined}>{dateLabel(source.run_cutoff)}</time></small>
        : source.source_type === 'analyst_estimate_changes' ? null : <small>发布 <time dateTime={publishedAt || undefined}>{dateLabel(publishedAt)}</time> · 取得 <time dateTime={retrievedAt || undefined}>{dateLabel(retrievedAt)}</time></small>}
      <SourceMetrics source={source} />
      {(source.source_type === 'computed_metric' || (source.document_id && source.version_id)) && (instrumentId || source.instrument_id) && <SavedEvidence source={source} instrumentId={(instrumentId || source.instrument_id)!} />}
    </li>
  })}</ul>
}
