import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { LoadingNotice } from '../../../../../packages/ui/src/NoticeToast'
import PriceRiskSettings from '../../../../../packages/ui/src/PriceRiskSettings'
import type { RiskWorkspace } from '../../../../../packages/ui/src/instrumentRisk'
import { fetchJson } from '../lib/api'
import { riskUnavailableReason } from '../lib/instrumentRiskMessages'
import { useCanWriteTeam } from './AccountBoundary'

type Metric = { metric_id: string; label: string; unit: string; value: number | null; status: string; unavailable_reason: string | null; as_of: string | null; observation_count: number; window_start_date: string | null; window_end_date: string | null; definition: Record<string, unknown>; series: Array<{ date: string; value: number | null; status: string; unavailable_reason?: string | null }> }
type Monitoring = { instrument_id: string; metric_snapshot_id: string | null; definition_version: string; input_snapshot_version: string; as_of: string | null; calculated_at: string | null; currency: string | null; return_kind: string | null; frequency: { resolved_frequency?: string; gap_detection_basis?: string } | null; status: string; freshness?: { data_freshness_status?: string } | null; metrics: Metric[] }
const request = <T,>(path: string, init?: RequestInit) => fetchJson<T>(`/api${path}`, init)
const labels: Record<string, [string, string]> = { current_drawdown: ['当前回撤', 'Current drawdown'], max_drawdown: ['区间最大回撤', 'Maximum drawdown'], holding_period_max_drawdown: ['持有期最大回撤', 'Holding-period maximum drawdown'], rolling_volatility: ['滚动年化波动率 · 1M', 'Rolling annualized volatility · 1M'], downside_deviation: ['下行偏差 · 1M', 'Downside deviation · 1M'], var: ['VaR · 95% / 3Y', 'VaR · 95% / 3Y'], expected_shortfall: ['ES · 95% / 3Y', 'ES · 95% / 3Y'] }
const statusLabels: Record<string, [string, string]> = { ready: ['有效', 'Ready'], partial: ['部分有效', 'Partial'], unavailable: ['不可用', 'Unavailable'], not_applicable: ['不适用', 'Not applicable'] }
function MetricTrend({ metric }: { metric: Metric }) {
  const zh = useLanguage().language === 'zh-Hans'
  const points = metric.series
  const valid = points.filter(point => point.value != null && Number.isFinite(point.value))
  if (!valid.length) return null
  const dates = points.map(point => Date.parse(point.date))
  const start = Math.min(...dates), end = Math.max(...dates)
  const low = Math.min(...valid.map(point => point.value!)), high = Math.max(...valid.map(point => point.value!))
  let connected = false
  const path = points.map(point => {
    if (point.value == null || !Number.isFinite(point.value)) { connected = false; return '' }
    const x = 46 + ((Date.parse(point.date) - start) / (end - start || 1)) * 530
    const y = 18 + (1 - (point.value - low) / (high - low || 1)) * 105
    const command = `${connected ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`; connected = true; return command
  }).join(' ')
  return <><svg className="monitoring-trend" viewBox="0 0 600 150" role="img" aria-label={`${labels[metric.metric_id]?.[zh ? 0 : 1] || metric.label} ${zh ? '完整趋势，缺口不连接' : 'full trend, gaps remain disconnected'}`}>
    {[18, 70, 123].map(y => <line key={y} x1="46" x2="576" y1={y} y2={y} stroke="#e6ebef" />)}
    <text x="3" y="23">{high.toFixed(1)}%</text>{valid.length === 1 && <circle cx={46 + ((Date.parse(valid[0].date) - start) / (end - start || 1)) * 530} cy="123" r="3" fill="#315d8a" />}<text x="3" y="127">{low.toFixed(1)}%</text><path d={path} fill="none" stroke="#315d8a" strokeWidth="1.7" />
    <text x="46" y="146">{points[0]?.date}</text><text x="576" y="146" textAnchor="end">{points[points.length - 1]?.date}</text>
  </svg><details><summary>{zh ? '逐期数值与缺口' : 'Observations and gaps'} ({points.length})</summary><div className="monitoring-series-table"><table><thead><tr><th>{zh ? '日期' : 'Date'}</th><th>{zh ? '读数' : 'Value'}</th><th>{zh ? '状态' : 'Status'}</th></tr></thead><tbody>{[...points].reverse().map(point => <tr key={point.date}><td>{point.date}</td><td>{point.value == null ? '—' : `${point.value.toFixed(2)}%`}</td><td>{statusLabels[point.status]?.[zh ? 0 : 1] || point.status}{point.unavailable_reason ? ` · ${riskUnavailableReason(point.unavailable_reason, zh)}` : ''}</td></tr>)}</tbody></table></div></details></>
}

export default function InstrumentMonitoringMetrics({ instrumentId }: { instrumentId: string }) {
  const zh = useLanguage().language === 'zh-Hans'
  const canWrite = useCanWriteTeam()
  const [data, setData] = useState<Monitoring | null>(null)
  const [workspace, setWorkspace] = useState<RiskWorkspace | null>(null)
  const [error, setError] = useState('')
  const [ruleError, setRuleError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    const controller = new AbortController(); setData(null); setWorkspace(null); setError(''); setRuleError('')
    request<Monitoring>(`/instruments/${encodeURIComponent(instrumentId)}/monitoring`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setData(value) }, failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : String(failure)) })
    request<RiskWorkspace>(`/risk?instrument_id=${encodeURIComponent(instrumentId)}&summary=true`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setWorkspace(value) }, failure => { if (!controller.signal.aborted) setRuleError(failure instanceof Error ? failure.message : String(failure)) })
    return () => controller.abort()
  }, [instrumentId, reload])
  return <>
    <section className="monitoring-metrics" aria-label={zh ? '持续指标监测' : 'Continuous metric monitoring'}><h2>{zh ? '持续指标监测' : 'Continuous metric monitoring'}</h2>
      <p className="instrument-tail-risk-sample">{zh ? '后台按数据更新计算；打开页面不会触发模型研判。缺数据或未配置规则不代表没有风险。' : 'Computed in the background when data updates. Opening this page does not run model analysis. Missing data or unconfigured rules do not establish an absence of risk.'}</p>
      <LoadingNotice active={!data && !error} message={zh ? '正在读取监测快照…' : 'Loading monitoring snapshot…'} />
      {error && <p role="alert">{error} <button onClick={() => setReload(value => value + 1)}>{zh ? '重试监测' : 'Retry monitoring'}</button></p>}
      {data && <><p className="instrument-tail-risk-sample">{zh ? '指标截至' : 'As of'} {data.as_of || '—'} · {data.currency || '—'} · {data.return_kind === 'total_return' ? (zh ? '分红再投收益' : 'Total returns') : data.return_kind === 'price_return' ? (zh ? '价格收益' : 'Price returns') : '—'} · {({ daily: zh ? '日频' : 'Daily', weekly: zh ? '周频' : 'Weekly', monthly: zh ? '月频' : 'Monthly', event_driven: zh ? '不定期' : 'Event driven' } as Record<string, string>)[data.frequency?.resolved_frequency || ''] || '—'}</p>
        {(data.status === 'unavailable' || ['stale', 'pending_recalc'].includes(data.freshness?.data_freshness_status || '')) && <p className="instrument-tail-risk-warning" role="status">{zh ? '本轮监测尚不可用或尚未更新到最新输入；已保留的数值仅代表其原截至日。' : 'Monitoring is unavailable or has not caught up with current inputs. Retained values represent their original as-of date.'}</p>}
        <div className="monitoring-metric-grid">{data.metrics.map(metric => <section key={metric.metric_id} className="monitoring-metric"><header><h3>{labels[metric.metric_id]?.[zh ? 0 : 1] || metric.label}</h3><span>{statusLabels[metric.status]?.[zh ? 0 : 1] || metric.status}</span></header><strong className="monitoring-metric-value">{metric.value == null ? '—' : `${metric.value.toFixed(2)}%`}</strong>
          {metric.unavailable_reason && <p className="instrument-tail-risk-warning">{riskUnavailableReason(metric.unavailable_reason, zh)}</p>}
          <p className="instrument-tail-risk-sample">{metric.window_start_date || '—'} → {metric.window_end_date || metric.as_of || '—'} · {metric.observation_count} {zh ? '个有效观察' : 'valid observations'}</p>
          <MetricTrend metric={metric} />{!metric.series.length && ['var', 'expected_shortfall'].includes(metric.metric_id) && <p className="instrument-tail-risk-sample">{zh ? '尚无可比历史快照，后续有效计算将持续留存；不回填假设历史。' : 'Comparable historical snapshots have not accumulated yet. Future valid calculations are retained; hypothetical history is not backfilled.'}</p>}
          <details><summary>{zh ? '计算口径' : 'Calculation definition'}</summary><pre>{JSON.stringify(metric.definition, null, 2)}</pre></details>
        </section>)}</div>
        <details className="monitoring-contract"><summary>{zh ? '输入与计算版本' : 'Input and calculation versions'}</summary><dl><dt>{zh ? '指标快照' : 'Metric snapshot'}</dt><dd translate="no">{data.metric_snapshot_id || '—'}</dd><dt>{zh ? '定义版本' : 'Definition version'}</dt><dd translate="no">{data.definition_version}</dd><dt>{zh ? '输入版本' : 'Input version'}</dt><dd translate="no">{data.input_snapshot_version}</dd><dt>{zh ? '计算时间' : 'Calculated at'}</dt><dd>{data.calculated_at || '—'}</dd></dl></details></>}
    </section>
    <section className="monitoring-rules" aria-label={zh ? '指标规则配置' : 'Metric rule settings'}><h2>{zh ? '指标规则配置' : 'Metric rule settings'}</h2>{ruleError && <p role="alert">{ruleError}</p>}{workspace?.instruments.map(asset => <PriceRiskSettings key={asset.instrument_id} asset={asset} request={request} canWrite={canWrite} onSaved={async () => { const [updated, rules] = await Promise.all([request<Monitoring>(`/instruments/${encodeURIComponent(instrumentId)}/monitoring`), request<RiskWorkspace>(`/risk?instrument_id=${encodeURIComponent(instrumentId)}&summary=true`)]); setData(updated); setWorkspace(rules) }} />)}</section>
  </>
}
