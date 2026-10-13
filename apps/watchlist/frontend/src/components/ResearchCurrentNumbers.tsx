import { useEffect, useState } from 'react'
import { fetchJson, type InstrumentChartResponse, type InstrumentSummaryResponse } from '../lib/api'
import ResearchReadingAside from './ResearchReadingAside'
import FreshnessNote from './FreshnessNote'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import ResearchFundamentalNumbers from './ResearchFundamentalNumbers'
import EstimateHistoryPanel from './EstimateHistoryPanel'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

/** Reads canonical values; opening research never starts a model or recomputes returns. */
export default function ResearchCurrentNumbers({ instrumentId }: { instrumentId: string }) {
  const { language } = useLanguage()
  const [state, setState] = useState<{ instrumentId: string; summary?: InstrumentSummaryResponse; chart?: InstrumentChartResponse; errors: string[] } | null>(null)
  const [months, setMonths] = useState(12)
  const [preferredSeries, setPreferredSeries] = useState<'price' | 'return' | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    const base = `/api/instruments/${encodeURIComponent(instrumentId)}`
    void Promise.allSettled([
      fetchJson<InstrumentSummaryResponse>(`${base}/summary`, { signal: controller.signal }),
      fetchJson<InstrumentChartResponse>(`${base}/chart`, { signal: controller.signal }),
    ]).then(([summary, chart]) => {
      if (!controller.signal.aborted) setState({ instrumentId, summary: summary.status === 'fulfilled' ? summary.value : undefined, chart: chart.status === 'fulfilled' ? chart.value : undefined, errors: [summary, chart].flatMap(result => result.status === 'rejected' ? [result.reason instanceof Error ? result.reason.message : '数据读取失败'] : []) })
    })
    return () => controller.abort()
  }, [instrumentId])
  const current = state?.instrumentId === instrumentId ? state : null
  const summary = current?.summary
  const chart = current?.chart
  const showingReturn = (preferredSeries || (chart?.return_series?.wealth_points.length ? 'return' : 'price')) === 'return'
  const series = showingReturn ? { points: chart?.return_series?.wealth_points || [], currency: chart?.return_series?.currency, quote_basis: chart?.return_series?.metadata.quote_basis } : chart?.price_series
  const priceLabel = showingReturn ? `收益指数 · ${chart?.return_series?.metadata.label || '已保存收益口径'}` : ({ close: '收盘价', unadjusted_close: '未复权收盘价', adjusted_close: '复权收盘价', unit_nav: '单位净值', spot: '即期价格' } as Record<string, string>)[series?.quote_basis || ''] || series?.quote_basis || '价格'
  const allPoints = (series?.points || []).filter(point => Number.isFinite(point.value) && Number.isFinite(Date.parse(point.date)))
  const last = allPoints[allPoints.length - 1]
  const cutoff = last ? new Date(`${last.date}T00:00:00Z`) : null
  if (cutoff) cutoff.setUTCMonth(cutoff.getUTCMonth() - months)
  const points = allPoints.filter(point => !months || !cutoff || Date.parse(point.date) >= cutoff.getTime())
  const first = points[0]
  const lo = Math.min(...points.map(point => point.value)); const hi = Math.max(...points.map(point => point.value))
  const x = (date: string) => 100 + (Date.parse(date) - Date.parse(first!.date)) / (Date.parse(points[points.length - 1]!.date) - Date.parse(first!.date) || 1) * 640
  const y = (value: number) => 215 - (value - lo) / (hi - lo || 1) * 185
  const paths: string[] = []
  points.forEach((point, index) => { const previous = points[index - 1]; if (!previous || point.segment_id !== previous.segment_id) paths.push(`M ${x(point.date)} ${y(point.value)}`); else paths[paths.length - 1] += ` L ${x(point.date)} ${y(point.value)}` })
  const number = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 4 })
  const axisNumber = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: Math.min(6, Math.max(0, 1 - Math.floor(Math.log10((hi - lo) / 2 || 1)))) })
  return <div className="research-current-numbers">
    {!current && <p className="sector-research-note" role="status">正在读取量化数据…</p>}
    {current?.errors.map((error, index) => <p role="alert" key={index}>{error}</p>)}
    {summary?.key_stats?.length ? <dl className="research-quant-metrics">{summary.key_stats.map(stat => <div key={stat.label}><dt>{stat.label}</dt><dd>{stat.value ?? '—'}</dd></div>)}</dl> : null}
    {summary?.freshness && (summary.freshness.staleness_reason || (summary.freshness.data_freshness_status === 'fresh' && Boolean(summary.freshness.awaiting_publication_date) && !summary.freshness.staleness_reason)) && <p className={(summary.freshness.data_freshness_status === 'fresh' && Boolean(summary.freshness.awaiting_publication_date) && !summary.freshness.staleness_reason) ? 'sector-research-note' : 'sector-research-limitation'}><FreshnessNote status={summary.freshness.data_freshness_status} awaitingPublicationDate={summary.freshness.awaiting_publication_date} reason={summary.freshness.staleness_reason} observationDate={summary.freshness.latest_observation_date} /></p>}
    <div className="research-dossier-section-heading"><h3>价格与收益历史</h3><div className="research-event-filters" aria-label="价格历史范围">{[[3, '3个月'], [6, '6个月'], [12, '1年'], [0, '全部']] .map(([value, label]) => <button type="button" key={value} aria-pressed={months === value} onClick={() => setMonths(Number(value))}>{label}</button>)}</div></div>
    <div className="research-event-filters" aria-label="历史数值口径"><button type="button" aria-pressed={!showingReturn} onClick={() => setPreferredSeries("price")}>原始价格</button><button type="button" disabled={!chart?.return_series?.wealth_points.length} aria-pressed={showingReturn} onClick={() => setPreferredSeries("return")}>收益路径</button></div>
    {!showingReturn && last && <p className="sector-research-note">原始价格可能包含拆股、分红等机械变化，不能直接据此判断投资回报。</p>}
    {last && <p className="sector-research-note">{priceLabel}{!showingReturn && <> · {series?.currency || chart?.currency}</>} · <time dateTime={last.date}>{last.date}</time> · {number(last.value)}</p>}
    {points.length > 1 ? <div className="research-quant-plot" role="region" aria-label="价格与收益历史图" tabIndex={0}><svg className="research-price-chart" viewBox="0 0 780 260" role="img" aria-label={`${showingReturn ? "收益历史" : "价格历史"} ${first!.date} 至 ${points[points.length - 1]!.date}`}>
      {[0, 0.5, 1].map(fraction => { const value = lo + (hi - lo) * fraction; return <g key={fraction}><line x1="100" x2="740" y1={y(value)} y2={y(value)} stroke="var(--line)" /><text x="92" y={y(value) + 4} textAnchor="end">{axisNumber(value)}</text></g> })}
      {paths.map((path, index) => <path key={index} d={path} fill="none" stroke="var(--studio-chart-primary)" strokeWidth="1.8" />)}
      <text x="100" y="245">{first!.date}</text><text x="740" y="245" textAnchor="end">{points[points.length - 1]!.date}</text>
    </svg></div> : current && <p className="sector-research-note">尚无足够的价格历史。缺失数据不作为零值。</p>}
    {points.length > 0 && <div className="research-quant-history-links"><ResearchReadingAside label={showingReturn ? "收益历史数据" : "价格历史数据"}><HorizontalTableScroll><table><thead><tr><th>日期</th><th>{showingReturn ? "收益指数" : `价格（${series?.currency || chart?.currency}）`}</th></tr></thead><tbody>{[...points].reverse().map(point => <tr key={point.date}><td>{point.date}</td><td>{number(point.value)}</td></tr>)}</tbody></table></HorizontalTableScroll></ResearchReadingAside></div>}
    <div className="research-quant-history-links"><ResearchReadingAside label="基本面与成份历史" title="基本面与成份历史"><ResearchFundamentalNumbers instrumentId={instrumentId} /></ResearchReadingAside>
    <ResearchReadingAside label="分析师预期变化与历史" title="分析师预期变化与历史"><EstimateHistoryPanel instrumentId={instrumentId} language={language} /></ResearchReadingAside></div>
  </div>
}
