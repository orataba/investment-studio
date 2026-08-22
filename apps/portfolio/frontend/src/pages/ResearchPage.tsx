import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router'

import BenchmarkSearchBox, { benchmarkInstrumentLabel } from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import {
  beginRequest,
  invalidateRequests,
  isRequestCurrent,
} from '../../../../../packages/ui/src/requestIdentity'
import { SerialTaskQueue } from '../../../../../packages/ui/src/serialTaskQueue'
import {
  createPortfolioResearchRun,
  getPortfolioResearchBacktestBenchmarkComparison,
  getPortfolioInstruments,
  getPortfolioRiskPolicy,
  getPortfolioTaxonomyCatalog,
  getPortfolioResearchRun,
  getPortfolioResearchWorkbench,
  updatePortfolioResearchInstrumentEligibility,
  updatePortfolioResearchSettings,
  type PortfolioResearchBacktestBenchmarkComparisonResponse,
  type PortfolioResearchBacktestBenchmarkRecord,
  type PortfolioResearchBacktestMetricsRecord,
  type PortfolioResearchBacktestPointRecord,
  type PortfolioResearchBacktestRebalanceFrequency,
  type PortfolioResearchBacktestRobustnessScenarioRecord,
  type PortfolioResearchBacktestRelativeMetricsRecord,
  type PortfolioResearchBacktestSleevePointRecord,
  type PortfolioResearchAsOfMode,
  type PortfolioResearchCapitalMode,
  type PortfolioResearchPlanningScopeOption,
  type PortfolioResearchRunRecord,
  type PortfolioResearchTopSleeveWeightBoundRecord,
  type PortfolioResearchWorkbenchResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTaxonomyRecord,
  type SharedInstrumentRecord,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatPercentInput,
} from '../lib/format'
import { resolveResearchAsOfDraft, serializeResearchAsOf } from '../lib/researchAsOf'

const CAPITAL_MODE_OPTIONS = [
  { value: 'unit_notional', label: 'Unit' },
  { value: 'fixed_gross', label: 'Fixed Gross' },
  { value: 'target_volatility', label: 'Vol Target' },
  { value: 'volatility_cap', label: 'Vol Cap' },
] as const

const REBALANCE_OPTIONS: Array<{ value: PortfolioResearchBacktestRebalanceFrequency; label: string }> = [
  { value: '1w', label: '1W' },
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
]

type ResearchRunSetupDraft = {
  asOfMode: PortfolioResearchAsOfMode
  asOfDate: string
  notes: string | null
  capitalMode: PortfolioResearchCapitalMode
  grossExposure: string
  targetVolatilityPct: string
  maxGrossExposure: string
  frozenNodeIds: string[]
  topSleeveBounds: ResearchTopSleeveBoundDraft[]
  backtestRebalanceFrequency: PortfolioResearchBacktestRebalanceFrequency
  benchmarkInstrumentId: string
  cashYieldPct: string
  commissionBps: string
  taxBps: string
  slippageBps: string
  implementationDelayDays: string
  robustnessScenarios: PortfolioResearchBacktestRobustnessScenarioRecord[]
  walkForwardTrainingMonths: string
  walkForwardTestMonths: string
}

type ResearchTopSleeveBoundDraft = {
  taxonomyNodeId: string
  minWeightPct: string
  maxWeightPct: string
}

type ChartSeries = {
  key: string
  label: string
  color: string
}

const CHART_COLORS = ['#2563eb', '#16a34a', '#dc2626', '#7c3aed', '#ea580c', '#0891b2', '#4b5563', '#be123c']

function TableStatusRow({
  colSpan,
  label,
  tone = 'neutral',
  detail,
}: {
  colSpan: number
  label: string
  tone?: 'neutral' | 'error'
  detail?: string | null
}) {
  return (
    <tr className="table-status-row">
      <td
        colSpan={colSpan}
        className={`empty-state-cell ${tone === 'error' ? 'table-status-cell-error' : ''}`}
        title={detail ?? undefined}
        aria-label={detail ? `${label}. ${detail}` : undefined}
        tabIndex={detail ? 0 : undefined}
      >
        {label}
      </td>
    </tr>
  )
}

function extractErrorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message && error.message !== '[object Object]' ? error.message : 'Request failed.'
  }
  if (typeof error === 'object' && error && 'message' in error && typeof error.message === 'string') {
    return error.message && error.message !== '[object Object]' ? error.message : 'Request failed.'
  }
  return 'Request failed.'
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  return value.replace('T', ' ').replace('Z', ' UTC')
}

function researchLifecycleLabel(value: 'held' | 'observed' | 'former') {
  return {
    held: 'Held',
    observed: 'Observed',
    former: 'Former',
  }[value]
}

function researchEligibilityLabel(value: 'eligible' | 'pm_review_required') {
  return value === 'eligible' ? 'Eligible' : 'PM review required'
}

function resolveStatusLabel(status: string) {
  if (status === 'completed') {
    return 'Completed'
  }
  if (status === 'failed') {
    return 'Failed'
  }
  if (status === 'running') {
    return 'Running'
  }
  return formatLabel(status)
}

function formatMaybePercent(value: number | null | undefined, digits = 2) {
  return value == null ? '-' : formatPercent(value, digits)
}

function formatMaybeNumber(value: number | null | undefined, digits = 2) {
  return value == null ? '-' : formatNumber(value, digits)
}

function formatMaybeDays(value: number | null | undefined) {
  return value == null ? '-' : `${formatNumber(value, 0)}D`
}

function formatSolvedBounds(minWeight: number | null | undefined, maxWeight: number | null | undefined) {
  if (minWeight == null && maxWeight == null) {
    return '-'
  }
  const minLabel = minWeight == null ? '-' : formatPercent(minWeight, 2)
  const maxLabel = maxWeight == null ? '-' : formatPercent(maxWeight, 2)
  return `${minLabel} / ${maxLabel}`
}

function formatBoundStatus(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  if (value === 'min') {
    return 'Min'
  }
  if (value === 'max') {
    return 'Max'
  }
  if (value === 'within') {
    return 'Within'
  }
  if (value === 'violated') {
    return 'Violated'
  }
  return formatLabel(value)
}

function isVolatilityCapitalMode(value: PortfolioResearchCapitalMode) {
  return value === 'target_volatility' || value === 'volatility_cap'
}

function sortTaxonomyNodes(nodes: PortfolioTaxonomyNodeRecord[]) {
  return [...nodes].sort((left, right) => {
    if (left.sort_order !== right.sort_order) {
      return left.sort_order - right.sort_order
    }
    return left.node_name.localeCompare(right.node_name) || left.taxonomy_node_id.localeCompare(right.taxonomy_node_id)
  })
}

function buildPlanningScopeOptions(
  taxonomy: PortfolioTaxonomyRecord,
  nodes: PortfolioTaxonomyNodeRecord[],
): PortfolioResearchPlanningScopeOption[] {
  const activeNodes = sortTaxonomyNodes(
    nodes.filter((node) => node.taxonomy_id === taxonomy.taxonomy_id && node.status === 'active'),
  )
  const childrenByParent = new Map<string | null, PortfolioTaxonomyNodeRecord[]>()
  activeNodes.forEach((node) => {
    const parentKey = node.parent_taxonomy_node_id ?? null
    const currentChildren = childrenByParent.get(parentKey) ?? []
    currentChildren.push(node)
    childrenByParent.set(parentKey, currentChildren)
  })

  const options: PortfolioResearchPlanningScopeOption[] = [
    {
      taxonomy_node_id: null,
      label: 'Top Level',
      path: 'Top Level',
      depth: 0,
      default_target_dimension: taxonomy.root_default_target_dimension ?? 'weight',
      has_children: Boolean(childrenByParent.get(null)?.length),
    },
  ]
  const visited = new Set<string>()

  function appendNode(node: PortfolioTaxonomyNodeRecord, parentPath: string, depth: number) {
    if (visited.has(node.taxonomy_node_id)) {
      return
    }
    visited.add(node.taxonomy_node_id)
    const path = `${parentPath} / ${node.node_name}`
    options.push({
      taxonomy_node_id: node.taxonomy_node_id,
      label: node.node_name,
      path,
      depth,
      default_target_dimension: node.default_target_dimension ?? 'weight',
      has_children: Boolean(childrenByParent.get(node.taxonomy_node_id)?.length),
    })
    ;(childrenByParent.get(node.taxonomy_node_id) ?? []).forEach((child) => appendNode(child, path, depth + 1))
  }

  ;(childrenByParent.get(null) ?? []).forEach((node) => appendNode(node, 'Top Level', 1))
  activeNodes.forEach((node) => appendNode(node, 'Top Level', 1))
  return options
}

function chartCoordinate(
  point: { date: string; value?: number | null },
  args: {
    minTime: number
    maxTime: number
    minValue: number
    maxValue: number
    width: number
    height: number
    padding: number
    left?: number
    right?: number
    top?: number
    bottom?: number
  },
) {
  const time = new Date(point.date).getTime()
  const value = point.value ?? 0
  const xRange = Math.max(args.maxTime - args.minTime, 1)
  const yRange = Math.max(args.maxValue - args.minValue, 0.000001)
  const left = args.left ?? args.padding
  const right = args.right ?? args.width - args.padding
  const top = args.top ?? args.padding
  const bottom = args.bottom ?? args.height - args.padding
  return {
    x: left + ((time - args.minTime) / xRange) * (right - left),
    y: bottom - ((value - args.minValue) / yRange) * (bottom - top),
  }
}

type ChartCoordinateArgs = Parameters<typeof chartCoordinate>[1]

function buildPath(points: Array<{ date: string; value?: number | null }>, args: Parameters<typeof chartCoordinate>[1]) {
  const coordinates = points
    .filter((point) => point.value != null)
    .map((point) => chartCoordinate(point, args))
  if (!coordinates.length) {
    return ''
  }
  return coordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ')
}

function drawdownPoints(points: Array<{ date: string; value?: number | null }>) {
  let highValue = -Infinity
  return points
    .filter((point) => point.value != null)
    .map((point) => {
      const value = point.value ?? 0
      highValue = Math.max(highValue, value)
      return {
        date: point.date,
        value: highValue > 0 ? value / highValue - 1 : 0,
      }
    })
}

function currentDrawdownFromPoints(points: Array<{ date: string; value?: number | null }>) {
  const visiblePoints = points.filter((point) => point.value != null)
  if (visiblePoints.length < 2) {
    return null
  }
  let highValue = -Infinity
  let currentValue: number | null = null
  visiblePoints.forEach((point) => {
    const value = point.value ?? 0
    highValue = Math.max(highValue, value)
    currentValue = value
  })
  return highValue > 0 && currentValue != null ? currentValue / highValue - 1 : null
}

function returnMapByDate(points: Array<{ date: string; value?: number | null }>) {
  const visiblePoints = points.filter((point) => point.value != null)
  const returns = new Map<string, number>()
  for (let index = 1; index < visiblePoints.length; index += 1) {
    const previousValue = visiblePoints[index - 1].value ?? 0
    const currentValue = visiblePoints[index].value ?? 0
    if (previousValue > 0) {
      returns.set(visiblePoints[index].date, currentValue / previousValue - 1)
    }
  }
  return returns
}

function activeCurrentDrawdown(
  portfolioPoints: Array<{ date: string; value?: number | null }>,
  benchmarkPoints: Array<{ date: string; value?: number | null }>,
) {
  const portfolioReturns = returnMapByDate(portfolioPoints)
  const benchmarkReturns = returnMapByDate(benchmarkPoints)
  const commonDates = [...portfolioReturns.keys()]
    .filter((dateKey) => benchmarkReturns.has(dateKey))
    .sort()
  if (!commonDates.length) {
    return null
  }
  let navValue = 1.0
  let highValue = 1.0
  let currentDrawdown = 0.0
  commonDates.forEach((dateKey) => {
    navValue *= 1.0 + (portfolioReturns.get(dateKey) ?? 0) - (benchmarkReturns.get(dateKey) ?? 0)
    highValue = Math.max(highValue, navValue)
    currentDrawdown = highValue > 0 ? navValue / highValue - 1 : 0.0
  })
  return currentDrawdown
}

function dateTicks(minTime: number, maxTime: number, count = 4) {
  if (!Number.isFinite(minTime) || !Number.isFinite(maxTime)) {
    return []
  }
  if (Math.abs(maxTime - minTime) < 1) {
    return [minTime]
  }
  return Array.from({ length: count }, (_item, index) => minTime + ((maxTime - minTime) * index) / (count - 1))
}

function formatDateTick(time: number) {
  const tickDate = new Date(time)
  const month = String(tickDate.getUTCMonth() + 1).padStart(2, '0')
  const day = String(tickDate.getUTCDate()).padStart(2, '0')
  return `${month}/${day}`
}

function renderTimeAxis(args: ChartCoordinateArgs) {
  const left = args.left ?? args.padding
  const right = args.right ?? args.width - args.padding
  const bottom = args.bottom ?? args.height - args.padding
  return (
    <g className="research-chart-time-axis">
      <line x1={left} x2={right} y1={bottom} y2={bottom} className="research-chart-axis" />
      {dateTicks(args.minTime, args.maxTime).map((time) => {
        const point = chartCoordinate({ date: new Date(time).toISOString(), value: args.minValue }, args)
        return (
          <g key={time}>
            <line x1={point.x} x2={point.x} y1={bottom} y2={bottom + 4} className="research-chart-axis" />
            <text x={point.x} y={bottom + 18} textAnchor="middle" className="research-chart-axis-label">
              {formatDateTick(time)}
            </text>
          </g>
        )
      })}
    </g>
  )
}

function ResearchLineChart({
  points,
  benchmarkPoints = [],
  benchmarkLabel = null,
}: {
  points: PortfolioResearchBacktestPointRecord[]
  benchmarkPoints?: PortfolioResearchBacktestPointRecord[]
  benchmarkLabel?: string | null
}) {
  const visiblePoints = points.filter((point) => point.value != null)
  const visibleBenchmark = benchmarkPoints.filter((point) => point.value != null)
  const allPoints = [...visiblePoints, ...visibleBenchmark]
  if (visiblePoints.length < 2) {
    return <div className="empty-state">No backtest series.</div>
  }
  const width = 720
  const height = 330
  const padding = 36
  const left = 44
  const right = width - 24
  const times = allPoints.map((point) => new Date(point.date).getTime())
  const values = allPoints.map((point) => point.value ?? 0)
  const minTime = Math.min(...times)
  const maxTime = Math.max(...times)
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const yPad = Math.max((maxValue - minValue) * 0.08, 0.01)
  const args = {
    minTime,
    maxTime,
    minValue: minValue - yPad,
    maxValue: maxValue + yPad,
    width,
    height,
    padding,
    left,
    right,
    top: 24,
    bottom: 180,
  }
  const drawdowns = drawdownPoints(visiblePoints)
  const drawdownMin = Math.min(...drawdowns.map((point) => point.value ?? 0), -0.01)
  const drawdownArgs = {
    minTime,
    maxTime,
    minValue: drawdownMin,
    maxValue: 0,
    width,
    height,
    padding,
    left,
    right,
    top: 218,
    bottom: height - padding,
  }
  return (
    <div className="research-chart">
      <div className="research-chart-legend">
        <span><i style={{ background: CHART_COLORS[0] }} />Solved</span>
        {visibleBenchmark.length > 1 ? <span><i style={{ background: CHART_COLORS[2] }} />{benchmarkLabel ?? 'Benchmark'}</span> : null}
        <span><i style={{ background: '#64748b' }} />Drawdown</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="research-chart-svg" role="img" aria-label="Backtest curve">
        <line x1={left} x2={right} y1={args.bottom} y2={args.bottom} className="research-chart-axis" />
        <path d={buildPath(visiblePoints, args)} className="research-chart-line" style={{ stroke: CHART_COLORS[0] }} />
        {visibleBenchmark.length > 1 ? (
          <path d={buildPath(visibleBenchmark, args)} className="research-chart-line research-chart-line-muted" style={{ stroke: CHART_COLORS[2] }} />
        ) : null}
        <text x={left} y={208} className="research-chart-panel-label">Drawdown</text>
        <line x1={left} x2={right} y1={drawdownArgs.top} y2={drawdownArgs.top} className="research-chart-axis research-chart-axis-muted" />
        <path d={buildPath(drawdowns, drawdownArgs)} className="research-chart-line research-chart-line-drawdown" />
        {renderTimeAxis(drawdownArgs)}
      </svg>
    </div>
  )
}

function sleeveSeries(points: PortfolioResearchBacktestSleevePointRecord[]) {
  const labels = new Map<string, ChartSeries>()
  points.forEach((point) => {
    point.sleeves.forEach((sleeve) => {
      const key = sleeve.top_sleeve_id ?? sleeve.top_sleeve_label
      if (!labels.has(key)) {
        labels.set(key, {
          key,
          label: sleeve.top_sleeve_label,
          color: CHART_COLORS[labels.size % CHART_COLORS.length],
        })
      }
    })
  })
  return [...labels.values()]
}

function ResearchSleeveLineChart({
  points,
  ariaLabel,
}: {
  points: PortfolioResearchBacktestSleevePointRecord[]
  ariaLabel: string
}) {
  const series = sleeveSeries(points)
  if (points.length < 2 || !series.length) {
    return <div className="empty-state">No chart data.</div>
  }
  const width = 720
  const height = 250
  const padding = 36
  const pointByDate = points.map((point) => {
    const valueByKey = new Map(point.sleeves.map((sleeve) => [sleeve.top_sleeve_id ?? sleeve.top_sleeve_label, sleeve.value ?? 0]))
    return { date: point.date, valueByKey }
  })
  const allValues = pointByDate.flatMap((point) => series.map((item) => point.valueByKey.get(item.key) ?? 0))
  const times = points.map((point) => new Date(point.date).getTime())
  const minTime = Math.min(...times)
  const maxTime = Math.max(...times)
  const minValue = Math.min(...allValues, 0)
  const maxValue = Math.max(...allValues, 0.01)
  const yPad = Math.max((maxValue - minValue) * 0.08, 0.01)
  const args = { minTime, maxTime, minValue: minValue - yPad, maxValue: maxValue + yPad, width, height, padding }
  return (
    <div className="research-chart">
      <div className="research-chart-legend">
        {series.slice(0, 6).map((item) => (
          <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="research-chart-svg" role="img" aria-label={ariaLabel}>
        {renderTimeAxis(args)}
        {series.map((item) => {
          const linePoints = pointByDate.map((point) => ({
            date: point.date,
            value: point.valueByKey.get(item.key) ?? 0,
          }))
          return <path key={item.key} d={buildPath(linePoints, args)} className="research-chart-line" style={{ stroke: item.color }} />
        })}
      </svg>
    </div>
  )
}

function ResearchSleeveStackedAreaChart({
  points,
}: {
  points: PortfolioResearchBacktestSleevePointRecord[]
}) {
  const series = sleeveSeries(points)
  if (points.length < 2 || !series.length) {
    return <div className="empty-state">No chart data.</div>
  }
  const width = 720
  const height = 250
  const padding = 36
  const times = points.map((point) => new Date(point.date).getTime())
  const minTime = Math.min(...times)
  const maxTime = Math.max(...times)
  const totals = points.map((point) => point.sleeves.reduce((total, sleeve) => total + Math.max(sleeve.value ?? 0, 0), 0))
  const maxValue = Math.max(...totals, 1)
  const args = { minTime, maxTime, minValue: 0, maxValue, width, height, padding }

  const valueByDate = points.map((point) => {
    const valueByKey = new Map(point.sleeves.map((sleeve) => [sleeve.top_sleeve_id ?? sleeve.top_sleeve_label, Math.max(sleeve.value ?? 0, 0)]))
    return { date: point.date, valueByKey }
  })

  return (
    <div className="research-chart">
      <div className="research-chart-legend">
        {series.slice(0, 6).map((item) => (
          <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="research-chart-svg" role="img" aria-label="Top sleeve weights">
        {renderTimeAxis(args)}
        {series.map((item, seriesIndex) => {
          const upper = valueByDate.map((point) => ({
            date: point.date,
            value: series.slice(0, seriesIndex + 1).reduce((total, current) => total + (point.valueByKey.get(current.key) ?? 0), 0),
          }))
          const lower = valueByDate.map((point) => ({
            date: point.date,
            value: series.slice(0, seriesIndex).reduce((total, current) => total + (point.valueByKey.get(current.key) ?? 0), 0),
          }))
          const upperCoordinates = upper.map((point) => chartCoordinate(point, args))
          const lowerCoordinates = lower.map((point) => chartCoordinate(point, args)).reverse()
          const path = [
            ...upperCoordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`),
            ...lowerCoordinates.map((point) => `L ${point.x.toFixed(2)} ${point.y.toFixed(2)}`),
            'Z',
          ].join(' ')
          return <path key={item.key} d={path} className="research-chart-area" style={{ fill: item.color }} />
        })}
      </svg>
    </div>
  )
}

function MetricTable({
  run,
  benchmark,
  relativeMetrics,
}: {
  run: PortfolioResearchRunRecord
  benchmark: PortfolioResearchBacktestBenchmarkRecord | null
  relativeMetrics: PortfolioResearchBacktestRelativeMetricsRecord | null
}) {
  const metrics = run.detail?.backtest?.metrics ?? null
  const benchmarkMetrics = benchmark?.metrics ?? null
  const relative = relativeMetrics
  const backtestPoints = run.detail?.backtest?.points ?? []
  const benchmarkPoints = benchmark?.points ?? []
  const portfolioCurrentDrawdown = metrics?.current_drawdown ?? currentDrawdownFromPoints(backtestPoints)
  const benchmarkCurrentDrawdown = benchmarkMetrics?.current_drawdown ?? currentDrawdownFromPoints(benchmarkPoints)
  const relativeCurrentDrawdown = relative?.current_drawdown ?? activeCurrentDrawdown(backtestPoints, benchmarkPoints)

  function periodLabel(record: PortfolioResearchBacktestMetricsRecord | PortfolioResearchBacktestRelativeMetricsRecord | null) {
    return record?.start_date && record?.end_date ? `${record.start_date} to ${record.end_date}` : '-'
  }

  function metricDifference(
    left: number | null | undefined,
    right: number | null | undefined,
  ) {
    return left == null || right == null ? null : left - right
  }

  function excessReturnMetric(key: 'period_return' | 'ytd_return' | 'annualized_return') {
    return metricDifference(metrics?.[key], benchmarkMetrics?.[key]) ?? relative?.[key] ?? (
      key === 'period_return' ? relative?.excess_return : null
    )
  }

  function formatMetricValue(
    value: number | string | null | undefined,
    kind: 'percent' | 'number' | 'days' | 'text',
  ) {
    if (kind === 'text') {
      return value == null || value === '' ? '-' : String(value)
    }
    if (typeof value !== 'number') {
      return '-'
    }
    if (kind === 'percent') {
      return formatMaybePercent(value)
    }
    if (kind === 'days') {
      return formatMaybeDays(value)
    }
    return formatMaybeNumber(value)
  }

  const rows: Array<{
    label: string
    kind: 'percent' | 'number' | 'days' | 'text'
    portfolio: number | string | null | undefined
    benchmark: number | string | null | undefined
    excess: number | string | null | undefined
  }> = [
    {
      label: 'Period Return',
      kind: 'percent',
      portfolio: metrics?.period_return,
      benchmark: benchmarkMetrics?.period_return,
      excess: excessReturnMetric('period_return'),
    },
    {
      label: 'YTD',
      kind: 'percent',
      portfolio: metrics?.ytd_return,
      benchmark: benchmarkMetrics?.ytd_return,
      excess: excessReturnMetric('ytd_return'),
    },
    {
      label: 'Annual Return',
      kind: 'percent',
      portfolio: metrics?.annualized_return,
      benchmark: benchmarkMetrics?.annualized_return,
      excess: excessReturnMetric('annualized_return'),
    },
    {
      label: 'Annual Volatility',
      kind: 'percent',
      portfolio: metrics?.annualized_volatility,
      benchmark: benchmarkMetrics?.annualized_volatility,
      excess: relative?.annualized_volatility ?? relative?.tracking_error,
    },
    {
      label: 'Max Drawdown',
      kind: 'percent',
      portfolio: metrics?.max_drawdown,
      benchmark: benchmarkMetrics?.max_drawdown,
      excess: relative?.max_drawdown,
    },
    {
      label: 'Current DD',
      kind: 'percent',
      portfolio: portfolioCurrentDrawdown,
      benchmark: benchmarkCurrentDrawdown,
      excess: relativeCurrentDrawdown,
    },
    {
      label: 'MDD Duration',
      kind: 'days',
      portfolio: metrics?.max_drawdown_days,
      benchmark: benchmarkMetrics?.max_drawdown_days,
      excess: relative?.max_drawdown_days,
    },
    {
      label: 'MDD Recovery',
      kind: 'days',
      portfolio: metrics?.max_drawdown_recovery_days,
      benchmark: benchmarkMetrics?.max_drawdown_recovery_days,
      excess: relative?.max_drawdown_recovery_days,
    },
    {
      label: 'Sharpe',
      kind: 'number',
      portfolio: metrics?.sharpe_ratio,
      benchmark: benchmarkMetrics?.sharpe_ratio,
      excess: relative?.sharpe_ratio ?? relative?.information_ratio,
    },
    {
      label: 'Calmar',
      kind: 'number',
      portfolio: metrics?.calmar_ratio,
      benchmark: benchmarkMetrics?.calmar_ratio,
      excess: relative?.calmar_ratio,
    },
  ]

  return (
    <table className="performance-summary-table research-metric-table">
      <thead>
        <tr>
          <th>Metric</th>
          <th>Portfolio</th>
          <th>Benchmark</th>
          <th>Relative</th>
        </tr>
      </thead>
      <tbody>
        <tr className="research-metric-period-row">
          <th>Period</th>
          <td>{periodLabel(metrics)}</td>
          <td>{periodLabel(benchmarkMetrics)}</td>
          <td>{periodLabel(relative)}</td>
        </tr>
        {rows.map((row) => (
          <tr key={row.label}>
            <th>{row.label}</th>
            <td>{formatMetricValue(row.portfolio, row.kind)}</td>
            <td>{formatMetricValue(row.benchmark, row.kind)}</td>
            <td>{formatMetricValue(row.excess, row.kind)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function ResearchPage() {
  const { portfolioId = '' } = useParams()
  const [workbench, setWorkbench] = useState<PortfolioResearchWorkbenchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [runDetailLoading, setRunDetailLoading] = useState(false)
  const [runDetailError, setRunDetailError] = useState<string | null>(null)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState<'run' | null>(null)
  const [eligibilityPendingId, setEligibilityPendingId] = useState<string | null>(null)
  const [frozenMenuOpen, setFrozenMenuOpen] = useState(false)
  const [boundsMenuOpen, setBoundsMenuOpen] = useState(false)
  const [dynamicScopeOptions, setDynamicScopeOptions] = useState<PortfolioResearchPlanningScopeOption[] | null>(null)
  const [scopeOptionsLoading, setScopeOptionsLoading] = useState(false)
  const [scopeOptionsError, setScopeOptionsError] = useState<string | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkComparison, setBenchmarkComparison] =
    useState<PortfolioResearchBacktestBenchmarkComparisonResponse | null>(null)
  const [benchmarkComparisonLoading, setBenchmarkComparisonLoading] = useState(false)
  const [benchmarkComparisonError, setBenchmarkComparisonError] = useState<string | null>(null)
  const frozenMenuRef = useRef<HTMLDivElement | null>(null)
  const boundsMenuRef = useRef<HTMLDivElement | null>(null)
  const autoSaveTimeoutRef = useRef<number | null>(null)
  const autoSaveQueueRef = useRef(new SerialTaskQueue())
  const workbenchRequestSequenceRef = useRef(0)
  const runRequestSequenceRef = useRef(0)
  const currentPortfolioIdRef = useRef(portfolioId)
  const draftPortfolioIdRef = useRef('')
  const runSetupDraftRef = useRef<ResearchRunSetupDraft>({
    asOfMode: 'dynamic',
    asOfDate: '',
    notes: null,
    capitalMode: 'unit_notional',
    grossExposure: '',
    targetVolatilityPct: '',
    maxGrossExposure: '',
    frozenNodeIds: [],
    topSleeveBounds: [],
    backtestRebalanceFrequency: '1m',
    benchmarkInstrumentId: '',
    cashYieldPct: '2',
    commissionBps: '2',
    taxBps: '10',
    slippageBps: '5',
    implementationDelayDays: '1',
    robustnessScenarios: [],
    walkForwardTrainingMonths: '24',
    walkForwardTestMonths: '6',
  })

  const [planningTaxonomyId, setPlanningTaxonomyId] = useState('')
  const [asOfMode, setAsOfMode] = useState<PortfolioResearchAsOfMode>('dynamic')
  const [asOfDate, setAsOfDate] = useState('')
  const [capitalMode, setCapitalMode] = useState<PortfolioResearchCapitalMode>('unit_notional')
  const [grossExposure, setGrossExposure] = useState('')
  const [targetVolatilityPct, setTargetVolatilityPct] = useState('')
  const [maxGrossExposure, setMaxGrossExposure] = useState('')
  const [frozenNodeIds, setFrozenNodeIds] = useState<string[]>([])
  const [topSleeveBounds, setTopSleeveBounds] = useState<ResearchTopSleeveBoundDraft[]>([])
  const [backtestRebalanceFrequency, setBacktestRebalanceFrequency] = useState<PortfolioResearchBacktestRebalanceFrequency>('1m')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [cashYieldPct, setCashYieldPct] = useState('2')
  const [commissionBps, setCommissionBps] = useState('2')
  const [taxBps, setTaxBps] = useState('10')
  const [slippageBps, setSlippageBps] = useState('5')
  const [implementationDelayDays, setImplementationDelayDays] = useState('1')
  const [robustnessScenarios, setRobustnessScenarios] = useState<PortfolioResearchBacktestRobustnessScenarioRecord[]>([])
  const [walkForwardTrainingMonths, setWalkForwardTrainingMonths] = useState('24')
  const [walkForwardTestMonths, setWalkForwardTestMonths] = useState('6')

  currentPortfolioIdRef.current = portfolioId

  async function reloadWorkbench(targetPortfolioId = portfolioId) {
    if (targetPortfolioId && currentPortfolioIdRef.current !== targetPortfolioId) {
      return
    }
    const request = beginRequest(workbenchRequestSequenceRef, targetPortfolioId)
    if (!targetPortfolioId) {
      setWorkbench(null)
      setLoading(false)
      setWorkspaceError(null)
      return
    }
    setLoading(true)
    try {
      const response = await getPortfolioResearchWorkbench(targetPortfolioId)
      if (
        !isRequestCurrent(
          workbenchRequestSequenceRef,
          request,
          currentPortfolioIdRef.current,
          response.portfolio_id,
        )
      ) {
        return
      }
      setWorkbench(response)
      setWorkspaceError(null)
    } catch (error) {
      if (!isRequestCurrent(workbenchRequestSequenceRef, request, currentPortfolioIdRef.current)) {
        return
      }
      setWorkbench(null)
      setWorkspaceError(extractErrorMessage(error))
    } finally {
      if (isRequestCurrent(workbenchRequestSequenceRef, request, currentPortfolioIdRef.current)) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    if (autoSaveTimeoutRef.current != null) {
      window.clearTimeout(autoSaveTimeoutRef.current)
      autoSaveTimeoutRef.current = null
    }
    invalidateRequests(workbenchRequestSequenceRef)
    invalidateRequests(runRequestSequenceRef)
    draftPortfolioIdRef.current = ''
    setWorkbench(null)
    setRunDetailLoading(false)
    setRunDetailError(null)
    setWorkspaceError(null)
    setActionPending(null)
    setEligibilityPendingId(null)
    setActionError(null)
    setNotice(null)
    setFrozenMenuOpen(false)
    setBoundsMenuOpen(false)
    void reloadWorkbench(portfolioId)
    return () => {
      invalidateRequests(workbenchRequestSequenceRef)
      invalidateRequests(runRequestSequenceRef)
      if (autoSaveTimeoutRef.current != null) {
        window.clearTimeout(autoSaveTimeoutRef.current)
        autoSaveTimeoutRef.current = null
      }
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setBenchmarkInstruments([])
      return
    }
    let cancelled = false
    getPortfolioInstruments(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setBenchmarkInstruments(response.instruments)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBenchmarkInstruments([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    function handleRiskPolicyUpdated(event: Event) {
      const detail = (event as CustomEvent<{ portfolioId?: string }>).detail
      if (detail?.portfolioId === portfolioId) {
        void reloadWorkbench()
      }
    }
    window.addEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
    return () => window.removeEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
  }, [portfolioId])

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [notice])

    useEffect(() => {
      function handleClick(event: MouseEvent) {
        const target = event.target as Node | null
        if (frozenMenuOpen && frozenMenuRef.current && target && !frozenMenuRef.current.contains(target)) {
          setFrozenMenuOpen(false)
        }
        if (boundsMenuOpen && boundsMenuRef.current && target && !boundsMenuRef.current.contains(target)) {
          setBoundsMenuOpen(false)
        }
      }
      document.addEventListener('mousedown', handleClick)
      return () => document.removeEventListener('mousedown', handleClick)
    }, [boundsMenuOpen, frozenMenuOpen])

  useLayoutEffect(() => {
    if (!workbench || workbench.portfolio_id !== portfolioId) {
      return
    }
    draftPortfolioIdRef.current = portfolioId
    const nextAsOf = resolveResearchAsOfDraft(workbench.settings, workbench.as_of_date)
    const nextBenchmarkInstrumentId = workbench.settings.backtest_benchmark_instrument_id ?? ''
    const nextCapitalMode = workbench.settings.capital_mode
    const nextTopSleeveBounds = (workbench.settings.top_sleeve_weight_bounds ?? []).map((item) => ({
      taxonomyNodeId: item.taxonomy_node_id,
      minWeightPct: formatPercentInput(item.min_weight),
      maxWeightPct: formatPercentInput(item.max_weight),
    }))
    const nextDraft: ResearchRunSetupDraft = {
      ...nextAsOf,
      notes: workbench.settings.notes ?? null,
      capitalMode: nextCapitalMode,
      grossExposure: workbench.settings.gross_exposure != null ? String(workbench.settings.gross_exposure) : '',
      targetVolatilityPct:
        formatPercentInput(workbench.settings.target_volatility),
      maxGrossExposure:
        workbench.settings.max_gross_exposure != null
          ? String(workbench.settings.max_gross_exposure)
          : nextCapitalMode === 'target_volatility'
            ? '1'
            : '',
      frozenNodeIds: workbench.settings.frozen_taxonomy_node_ids ?? [],
      topSleeveBounds: nextTopSleeveBounds,
      backtestRebalanceFrequency: workbench.settings.backtest_rebalance_frequency ?? '1m',
      benchmarkInstrumentId: nextBenchmarkInstrumentId,
      cashYieldPct: String(workbench.settings.backtest_cash_yield_annual * 100),
      commissionBps: String(workbench.settings.backtest_commission_bps),
      taxBps: String(workbench.settings.backtest_tax_bps),
      slippageBps: String(workbench.settings.backtest_slippage_bps),
      implementationDelayDays: String(workbench.settings.backtest_implementation_delay_days),
      robustnessScenarios: workbench.settings.backtest_robustness_scenarios,
      walkForwardTrainingMonths: String(workbench.settings.backtest_walk_forward_training_months),
      walkForwardTestMonths: String(workbench.settings.backtest_walk_forward_test_months),
    }
    setPlanningTaxonomyId(workbench.default_planning_taxonomy_id ?? workbench.settings.planning_taxonomy_id ?? '')
    setAsOfMode(nextAsOf.asOfMode)
    setAsOfDate(nextAsOf.asOfDate)
    setCapitalMode(nextDraft.capitalMode)
    setGrossExposure(nextDraft.grossExposure)
    setTargetVolatilityPct(nextDraft.targetVolatilityPct)
    setMaxGrossExposure(nextDraft.maxGrossExposure)
    setFrozenNodeIds(nextDraft.frozenNodeIds)
    setTopSleeveBounds(nextTopSleeveBounds)
    setBacktestRebalanceFrequency(nextDraft.backtestRebalanceFrequency)
    setBenchmarkInstrumentId(nextBenchmarkInstrumentId)
    setCashYieldPct(nextDraft.cashYieldPct)
    setCommissionBps(nextDraft.commissionBps)
    setTaxBps(nextDraft.taxBps)
    setSlippageBps(nextDraft.slippageBps)
    setImplementationDelayDays(nextDraft.implementationDelayDays)
    setRobustnessScenarios(nextDraft.robustnessScenarios)
    setWalkForwardTrainingMonths(nextDraft.walkForwardTrainingMonths)
    setWalkForwardTestMonths(nextDraft.walkForwardTestMonths)
    runSetupDraftRef.current = nextDraft
  }, [workbench])

  useEffect(() => {
    const selectedBenchmark = benchmarkInstruments.find((instrument) => instrument.instrument_id === benchmarkInstrumentId)
    if (selectedBenchmark) {
      setBenchmarkSearch(benchmarkInstrumentLabel(selectedBenchmark))
    } else if (!benchmarkInstrumentId) {
      setBenchmarkSearch('')
    }
  }, [benchmarkInstrumentId, benchmarkInstruments])

  useEffect(() => {
    if (!workbench) {
      return
    }
    if (planningTaxonomyId !== (workbench.settings.planning_taxonomy_id ?? '')) {
        runSetupDraftRef.current = {
          ...runSetupDraftRef.current,
          frozenNodeIds: [],
          topSleeveBounds: [],
        }
        setFrozenNodeIds([])
        setTopSleeveBounds([])
      }
    }, [planningTaxonomyId, workbench])

  useEffect(() => {
    const savedPlanningTaxonomyId = workbench?.settings.planning_taxonomy_id ?? ''
    if (!portfolioId || !workbench || !planningTaxonomyId || planningTaxonomyId === savedPlanningTaxonomyId) {
      setDynamicScopeOptions(null)
      setScopeOptionsLoading(false)
      setScopeOptionsError(null)
      return
    }
    let cancelled = false
    setScopeOptionsLoading(true)
    setScopeOptionsError(null)
    getPortfolioTaxonomyCatalog(portfolioId)
      .then((catalog) => {
        if (cancelled) {
          return
        }
        const taxonomy = catalog.taxonomies.find(
          (item) => item.taxonomy_id === planningTaxonomyId && item.planning_enabled,
        )
        if (!taxonomy) {
          throw new Error('Selected planning taxonomy is unavailable.')
        }
        setDynamicScopeOptions(buildPlanningScopeOptions(taxonomy, catalog.taxonomy_nodes))
      })
      .catch((error) => {
        if (!cancelled) {
          setDynamicScopeOptions(null)
          setScopeOptionsError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setScopeOptionsLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [planningTaxonomyId, portfolioId, workbench])

  const latestRun = workbench?.selected_run ?? workbench?.runs[0] ?? null

  useEffect(() => {
    const researchRunId = latestRun?.research_run_id ?? ''
    if (
      !portfolioId
      || !researchRunId
      || latestRun?.status !== 'completed'
      || latestRun.detail != null
    ) {
      setRunDetailLoading(false)
      setRunDetailError(null)
      return undefined
    }

    let cancelled = false
    setRunDetailLoading(true)
    setRunDetailError(null)
    getPortfolioResearchRun(portfolioId, researchRunId)
      .then((detailedRun) => {
        if (cancelled || currentPortfolioIdRef.current !== portfolioId) {
          return
        }
        setWorkbench((current) => {
          if (!current || current.portfolio_id !== portfolioId) {
            return current
          }
          const compactRun = (
            current.selected_run?.research_run_id === researchRunId
              ? current.selected_run
              : current.runs.find((run) => run.research_run_id === researchRunId)
          )
          if (!compactRun) {
            return current
          }
          return {
            ...current,
            detail_level: 'selected_run',
            selected_run: {
              ...detailedRun,
              reliability_state: compactRun.reliability_state,
              is_current: compactRun.is_current,
              reliability_reasons: compactRun.reliability_reasons,
            },
          }
        })
      })
      .catch((error) => {
        if (!cancelled) {
          setRunDetailError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRunDetailLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [latestRun?.detail, latestRun?.research_run_id, latestRun?.status, portfolioId])

  useEffect(() => {
    const researchRunId = latestRun?.research_run_id ?? ''
    const selectedBenchmarkId = benchmarkInstrumentId.trim()
    const storedBenchmarkId = latestRun?.detail?.backtest_benchmark?.instrument_id ?? ''
    if (
      !portfolioId
      || !researchRunId
      || latestRun?.status !== 'completed'
      || latestRun.detail == null
      || !selectedBenchmarkId
    ) {
      setBenchmarkComparison(null)
      setBenchmarkComparisonLoading(false)
      setBenchmarkComparisonError(null)
      return undefined
    }
    if (selectedBenchmarkId === storedBenchmarkId && latestRun.detail?.backtest_benchmark) {
      setBenchmarkComparison(null)
      setBenchmarkComparisonLoading(false)
      setBenchmarkComparisonError(null)
      return undefined
    }

    let cancelled = false
    setBenchmarkComparison(null)
    setBenchmarkComparisonLoading(true)
    setBenchmarkComparisonError(null)
    getPortfolioResearchBacktestBenchmarkComparison(portfolioId, researchRunId, selectedBenchmarkId)
      .then((response) => {
        if (!cancelled) {
          setBenchmarkComparison(response)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setBenchmarkComparison(null)
          setBenchmarkComparisonError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBenchmarkComparisonLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [
    benchmarkInstrumentId,
    latestRun?.detail?.backtest_benchmark,
    latestRun?.research_run_id,
    latestRun?.status,
    portfolioId,
  ])

  const selectedScopeOptions = useMemo<PortfolioResearchPlanningScopeOption[]>(() => {
    if (!workbench || !planningTaxonomyId) {
      return []
    }
    if (planningTaxonomyId === (workbench.settings.planning_taxonomy_id ?? '')) {
      return workbench.planning_scope_options
    }
    return dynamicScopeOptions ?? []
  }, [dynamicScopeOptions, planningTaxonomyId, workbench])
  const savedPlanningTaxonomyId = workbench?.settings.planning_taxonomy_id ?? ''
  const scopeOptionsPending = Boolean(
    planningTaxonomyId && planningTaxonomyId !== savedPlanningTaxonomyId && !dynamicScopeOptions && !scopeOptionsError,
  )
  const scopeActionBlocked = scopeOptionsLoading || scopeOptionsPending || Boolean(scopeOptionsError)
    const selectableFrozenScopes = useMemo(
      () => selectedScopeOptions.filter((option) => Boolean(option.taxonomy_node_id)),
      [selectedScopeOptions],
    )
    const topSleeveOptions = useMemo(
      () => selectedScopeOptions.filter((option) => option.depth === 1 && Boolean(option.taxonomy_node_id)),
      [selectedScopeOptions],
    )
    const selectedFrozenLabels = useMemo(() => {
      const labelByNodeId = new Map(selectableFrozenScopes.map((option) => [option.taxonomy_node_id ?? '', option.label]))
      return frozenNodeIds.map((nodeId) => labelByNodeId.get(nodeId)).filter(Boolean) as string[]
    }, [frozenNodeIds, selectableFrozenScopes])
    const frozenMenuLabel = selectedFrozenLabels.length ? `${selectedFrozenLabels.length} selected` : 'None'
    const configuredBoundCount = topSleeveBounds.filter(
      (item) => item.minWeightPct.trim() || item.maxWeightPct.trim(),
    ).length
    const boundsMenuLabel = configuredBoundCount ? `${configuredBoundCount} set` : 'None'
    const topSleeveBoundByNodeId = useMemo(
      () => new Map(topSleeveBounds.map((item) => [item.taxonomyNodeId, item])),
      [topSleeveBounds],
    )

  function scheduleAutoSave(nextDraft: ResearchRunSetupDraft) {
    const targetPortfolioId = draftPortfolioIdRef.current
    if (!targetPortfolioId || targetPortfolioId !== portfolioId) {
      return
    }
    runSetupDraftRef.current = nextDraft
    if (autoSaveTimeoutRef.current != null) {
      window.clearTimeout(autoSaveTimeoutRef.current)
    }
    autoSaveTimeoutRef.current = window.setTimeout(() => {
      autoSaveTimeoutRef.current = null
      if (
        currentPortfolioIdRef.current !== targetPortfolioId ||
        draftPortfolioIdRef.current !== targetPortfolioId
      ) {
        return
      }
      void autoSaveQueueRef.current
        .enqueue(() => {
          if (
            currentPortfolioIdRef.current !== targetPortfolioId ||
            draftPortfolioIdRef.current !== targetPortfolioId
          ) {
            return null
          }
          return persistSettings({
            draft: nextDraft,
            targetPortfolioId,
          })
        })
        .catch(() => undefined)
    }, 450)
  }

  function updateRunSetupDraft(updates: Partial<ResearchRunSetupDraft>) {
    const nextDraft = {
      ...runSetupDraftRef.current,
      ...updates,
    }
    setActionError(null)
    scheduleAutoSave(nextDraft)
    return nextDraft
  }

  function updateRobustnessScenario(
    index: number,
    field: keyof PortfolioResearchBacktestRobustnessScenarioRecord,
    value: string,
  ) {
    setRobustnessScenarios((current) => {
      const next = current.map((scenario, scenarioIndex) => {
        if (scenarioIndex !== index) {
          return scenario
        }
        if (field === 'scenario_id' || field === 'label') {
          return { ...scenario, [field]: value }
        }
        return { ...scenario, [field]: Number(value) }
      })
      updateRunSetupDraft({ robustnessScenarios: next })
      return next
    })
  }

  function addRobustnessScenario() {
    const existingIds = new Set(robustnessScenarios.map((scenario) => scenario.scenario_id))
    let sequence = robustnessScenarios.length + 1
    while (existingIds.has(`stress_${sequence}`)) {
      sequence += 1
    }
    const next = [
      ...robustnessScenarios,
      {
        scenario_id: `stress_${sequence}`,
        label: `Stress ${sequence}`,
        cash_yield_annual: 0,
        commission_bps: 4,
        tax_bps: 20,
        slippage_bps: 10,
        implementation_delay_days: 2,
      },
    ]
    setRobustnessScenarios(next)
    updateRunSetupDraft({ robustnessScenarios: next })
  }

  function removeRobustnessScenario(index: number) {
    const next = robustnessScenarios.filter((_scenario, scenarioIndex) => scenarioIndex !== index)
    setRobustnessScenarios(next)
    updateRunSetupDraft({ robustnessScenarios: next })
  }

    function toggleFrozenNode(nodeId: string) {
      setFrozenNodeIds((current) => {
      if (current.includes(nodeId)) {
        const nextFrozenNodeIds = current.filter((item) => item !== nodeId)
        updateRunSetupDraft({ frozenNodeIds: nextFrozenNodeIds })
        return nextFrozenNodeIds
      }
      const nextFrozenNodeIds = [...current, nodeId]
      updateRunSetupDraft({ frozenNodeIds: nextFrozenNodeIds })
      return nextFrozenNodeIds
      })
    }

    function updateTopSleeveBound(nodeId: string, field: 'minWeightPct' | 'maxWeightPct', value: string) {
      setTopSleeveBounds((current) => {
        const existing = current.find((item) => item.taxonomyNodeId === nodeId)
        const nextItem: ResearchTopSleeveBoundDraft = {
          taxonomyNodeId: nodeId,
          minWeightPct: existing?.minWeightPct ?? '',
          maxWeightPct: existing?.maxWeightPct ?? '',
          [field]: value,
        }
        const nextBounds = [
          ...current.filter((item) => item.taxonomyNodeId !== nodeId),
          nextItem,
        ].filter((item) => item.minWeightPct.trim() || item.maxWeightPct.trim())
        updateRunSetupDraft({ topSleeveBounds: nextBounds })
        return nextBounds
      })
    }

    function serializeTopSleeveBounds(
      draftBounds: ResearchTopSleeveBoundDraft[],
    ): PortfolioResearchTopSleeveWeightBoundRecord[] {
      const validTopSleeveIds = new Set(topSleeveOptions.map((item) => item.taxonomy_node_id ?? ''))
      return draftBounds
        .filter((item) => validTopSleeveIds.has(item.taxonomyNodeId))
        .map((item) => {
          const minWeight = item.minWeightPct.trim() ? Number(item.minWeightPct) / 100 : null
          const maxWeight = item.maxWeightPct.trim() ? Number(item.maxWeightPct) / 100 : null
          return {
            taxonomy_node_id: item.taxonomyNodeId,
            min_weight: minWeight,
            max_weight: maxWeight,
          }
        })
        .filter((item) => item.min_weight != null || item.max_weight != null)
    }

  async function persistSettings({
    draft = runSetupDraftRef.current,
    targetPortfolioId = portfolioId,
  }: {
    draft?: ResearchRunSetupDraft
    targetPortfolioId?: string
  } = {}) {
    if (!targetPortfolioId || currentPortfolioIdRef.current !== targetPortfolioId) {
      return null
    }
    const parsedGrossExposure = draft.grossExposure.trim() ? Number(draft.grossExposure) : null
    const parsedTargetVolatility = draft.targetVolatilityPct.trim() ? Number(draft.targetVolatilityPct) / 100 : null
    const parsedMaxGrossExposure = draft.maxGrossExposure.trim() ? Number(draft.maxGrossExposure) : null
    const parsedCashYield = Number(draft.cashYieldPct) / 100
    const parsedCommissionBps = Number(draft.commissionBps)
    const parsedTaxBps = Number(draft.taxBps)
    const parsedSlippageBps = Number(draft.slippageBps)
    const parsedImplementationDelayDays = Number(draft.implementationDelayDays)
    const parsedWalkForwardTrainingMonths = Number(draft.walkForwardTrainingMonths)
    const parsedWalkForwardTestMonths = Number(draft.walkForwardTestMonths)
    const volatilityMode = isVolatilityCapitalMode(draft.capitalMode)
    const riskPolicy = await getPortfolioRiskPolicy(targetPortfolioId)
    if (currentPortfolioIdRef.current !== targetPortfolioId) {
      return null
    }
    const validFrozenNodeIds = new Set(selectableFrozenScopes.map((item) => item.taxonomy_node_id ?? ''))
    const frozenTaxonomyNodeIds = draft.frozenNodeIds.filter((nodeId) => validFrozenNodeIds.has(nodeId))
    const topSleeveWeightBounds = serializeTopSleeveBounds(draft.topSleeveBounds)
    const researchAsOf = serializeResearchAsOf(draft)
    if (researchAsOf.as_of_mode === 'pinned' && !researchAsOf.as_of_date) {
      throw new Error('Select a pinned analysis date.')
    }
    return updatePortfolioResearchSettings(targetPortfolioId, {
      planning_taxonomy_id: planningTaxonomyId || null,
      comparator_taxonomy_node_id: null,
      ...researchAsOf,
      lookback_days: riskPolicy.lookback_days,
      calculation_frequency: riskPolicy.calculation_frequency,
      missing_return_policy: riskPolicy.missing_return_policy,
      covariance_model_id: riskPolicy.covariance_model_id,
      contribution_mode: riskPolicy.contribution_mode,
      target_dimension: 'scope_default',
      capital_mode: draft.capitalMode,
      gross_exposure: draft.capitalMode === 'fixed_gross' ? parsedGrossExposure : null,
      target_volatility: volatilityMode ? parsedTargetVolatility : null,
      max_gross_exposure:
        draft.capitalMode === 'target_volatility'
          ? parsedMaxGrossExposure ?? 1
          : null,
      frozen_taxonomy_node_ids: frozenTaxonomyNodeIds,
      top_sleeve_weight_bounds: topSleeveWeightBounds,
      backtest_rebalance_frequency: draft.backtestRebalanceFrequency,
      backtest_benchmark_instrument_id: draft.benchmarkInstrumentId || null,
      backtest_cash_yield_annual: parsedCashYield,
      backtest_commission_bps: parsedCommissionBps,
      backtest_tax_bps: parsedTaxBps,
      backtest_slippage_bps: parsedSlippageBps,
      backtest_implementation_delay_days: parsedImplementationDelayDays,
      backtest_robustness_scenarios: draft.robustnessScenarios,
      backtest_walk_forward_training_months: parsedWalkForwardTrainingMonths,
      backtest_walk_forward_test_months: parsedWalkForwardTestMonths,
      notes: draft.notes,
    })
  }

  async function handleRunResearch() {
    const targetPortfolioId = portfolioId
    if (!targetPortfolioId) {
      return
    }
    const runRequest = beginRequest(runRequestSequenceRef, targetPortfolioId)
    const runDraft = runSetupDraftRef.current
    if (autoSaveTimeoutRef.current != null) {
      window.clearTimeout(autoSaveTimeoutRef.current)
      autoSaveTimeoutRef.current = null
    }
    setActionPending('run')
    setActionError(null)
    setNotice(null)
    setFrozenMenuOpen(false)
    setBoundsMenuOpen(false)
    try {
      await autoSaveQueueRef.current.enqueue(async () => {
        await persistSettings({
          draft: runDraft,
          targetPortfolioId,
        })
        if (!isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) {
          return
        }
        await createPortfolioResearchRun(targetPortfolioId, { requested_by: 'workspace-ui' })
      })
      if (!isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) {
        return
      }
      setNotice('Research run completed.')
      await reloadWorkbench(targetPortfolioId)
    } catch (error) {
      if (!isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) {
        return
      }
      setActionError(extractErrorMessage(error))
      try {
        await reloadWorkbench(targetPortfolioId)
      } catch {
        // Keep the original run error visible if the follow-up refresh also fails.
      }
    } finally {
      if (isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) {
        setActionPending(null)
      }
    }
  }

  async function handleResearchEligibilityApproval(instrumentId: string, pmApproved: boolean) {
    const targetPortfolioId = portfolioId
    if (!targetPortfolioId || eligibilityPendingId) {
      return
    }
    setEligibilityPendingId(instrumentId)
    setActionError(null)
    try {
      const updated = await updatePortfolioResearchInstrumentEligibility(
        targetPortfolioId,
        instrumentId,
        { pm_approved: pmApproved },
      )
      if (currentPortfolioIdRef.current !== targetPortfolioId) {
        return
      }
      setWorkbench((current) => (
        current?.portfolio_id === targetPortfolioId
          ? {
              ...current,
              instrument_universe: (current.instrument_universe ?? []).map((item) => (
                item.instrument_id === updated.instrument_id ? updated : item
              )),
            }
          : current
      ))
      setNotice(
        pmApproved
          ? 'PM approval saved. Run Research again to refresh execution readiness.'
          : 'PM approval removed. Former-instrument targets require review again.',
      )
    } catch (error) {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setActionError(extractErrorMessage(error))
      }
    } finally {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setEligibilityPendingId(null)
      }
    }
  }

  const solvedGroups = latestRun?.detail?.solved_result_groups ?? []
  const backtest = latestRun?.detail?.backtest ?? null
  const pointInTimeCoverage = backtest?.point_in_time_coverage ?? null
  const skippedRebalances = pointInTimeCoverage?.skipped_rebalances ?? []
  const configurationVersionsUsed = pointInTimeCoverage?.configuration_versions_used ?? []
  const executionRecords = backtest?.execution_records ?? []
  const robustnessResults = backtest?.robustness_results ?? []
  const walkForward = backtest?.walk_forward ?? null
  const contributionReconciliation = backtest?.contribution_reconciliation_points ?? []
  const latestContributionReconciliation = contributionReconciliation.length
    ? contributionReconciliation[contributionReconciliation.length - 1]
    : null
  const staleRun = latestRun?.status === 'completed' && latestRun.reliability_state === 'stale'
  const solveEvents = (latestRun?.detail?.scope_solve_events ?? []).length
    ? latestRun?.detail?.scope_solve_events ?? []
    : latestRun?.detail?.solve_event
      ? [latestRun.detail.solve_event]
      : []
  const nonExecutionReadySolveEvents = solveEvents.filter((event) => event.execution_ready === false)
  const rebalanceGaps = (latestRun?.detail?.target_weight_gaps ?? []).filter(
    (row) => Math.abs(row.gap ?? 0) > 0.0001 || row.execution_status === 'manual_review_required',
  )
  const staleRunDetail = [
    'Run Research again before using these weights for allocation or orders.',
    ...(latestRun?.reliability_reasons ?? []),
  ].join(' ')
  const constrainedSolveDetail = nonExecutionReadySolveEvents
    .map(
      (event) =>
        `${event.scope_label}: ${
          event.solver_message ?? 'Review target shares and weight bounds before using these weights.'
        }`,
    )
    .join(' ')
  const skippedRebalanceDetail = skippedRebalances
    .map((item) => `${item.date}: ${item.reason}`)
    .join(' ')
  const storedBenchmark = latestRun?.detail?.backtest_benchmark ?? null
  const selectedBenchmarkId = benchmarkInstrumentId.trim()
  const displayBenchmark = selectedBenchmarkId
    ? benchmarkComparison?.backtest_benchmark ??
      (selectedBenchmarkId === (storedBenchmark?.instrument_id ?? '') ? storedBenchmark : null)
    : null
  const displayRelativeMetrics = selectedBenchmarkId
    ? benchmarkComparison?.backtest_relative_metrics ??
      (selectedBenchmarkId === (storedBenchmark?.instrument_id ?? '')
        ? latestRun?.detail?.backtest_relative_metrics ?? null
        : null)
    : null

  return (
    <PortfolioWorkspaceLayout
      activeSection="Research"
      busy={loading || runDetailLoading}
    >
      {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
      {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
      {actionError ? <div className="inline-notice inline-notice-error">{actionError}</div> : null}
      <QualityWarningsNotice warnings={workbench?.current_context.quality_warnings} />
      {loading && !workbench ? <CalculationStatus /> : null}

      {!loading && !workbench && !workspaceError ? <div className="empty-state">No data.</div> : null}

      {workbench ? (
        <>
          <section className="panel">
            <form
              className="transaction-form taxonomy-form-compact research-run-form"
              aria-busy={actionPending === 'run'}
              onSubmit={(event) => event.preventDefault()}
            >
              <fieldset className="research-settings-fieldset" disabled={actionPending === 'run'}>
                <div className="research-settings-bar">
                <div className="taxonomy-form-grid taxonomy-form-grid-wide research-settings-grid">
                  <label>
                    <span>Date Mode</span>
                    <select
                      value={asOfMode}
                      onChange={(event) => {
                        const nextMode = event.target.value as PortfolioResearchAsOfMode
                        const nextDate =
                          nextMode === 'dynamic'
                            ? workbench.as_of_date
                            : asOfDate || workbench.as_of_date
                        setAsOfMode(nextMode)
                        setAsOfDate(nextDate)
                        updateRunSetupDraft({ asOfMode: nextMode, asOfDate: nextDate })
                      }}
                    >
                      <option value="dynamic">Latest available</option>
                      <option value="pinned">Pinned date</option>
                    </select>
                  </label>
                  <label>
                    <span>Analysis Date</span>
                    <input
                      type="date"
                      value={asOfDate}
                      disabled={asOfMode === 'dynamic'}
                      onChange={(event) => {
                        setAsOfDate(event.target.value)
                        updateRunSetupDraft({ asOfDate: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Capital Mode</span>
                    <select
                      value={capitalMode}
                      onChange={(event) => {
                        const nextCapitalMode = event.target.value as PortfolioResearchCapitalMode
                        const nextDraftUpdates: Partial<ResearchRunSetupDraft> = { capitalMode: nextCapitalMode }
                        if (nextCapitalMode === 'target_volatility' && !runSetupDraftRef.current.maxGrossExposure.trim()) {
                          nextDraftUpdates.maxGrossExposure = '1'
                          setMaxGrossExposure('1')
                        }
                        if (nextCapitalMode === 'volatility_cap') {
                          nextDraftUpdates.maxGrossExposure = ''
                          setMaxGrossExposure('')
                        }
                        setCapitalMode(nextCapitalMode)
                        updateRunSetupDraft(nextDraftUpdates)
                      }}
                    >
                      {CAPITAL_MODE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {isVolatilityCapitalMode(capitalMode) ? (
                    <>
                      <label>
                        <span>{capitalMode === 'volatility_cap' ? 'Vol Cap (%)' : 'Target Vol (%)'}</span>
                        <input
                          type="number"
                          step="0.1"
                          min="0"
                          value={targetVolatilityPct}
                          onChange={(event) => {
                            setTargetVolatilityPct(event.target.value)
                            updateRunSetupDraft({ targetVolatilityPct: event.target.value })
                          }}
                          placeholder="7.0"
                        />
                      </label>
                    </>
                  ) : null}
                  {capitalMode === 'target_volatility' ? (
                    <>
                      <label>
                        <span>Max Gross</span>
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          value={maxGrossExposure}
                          onChange={(event) => {
                            setMaxGrossExposure(event.target.value)
                            updateRunSetupDraft({ maxGrossExposure: event.target.value })
                          }}
                          placeholder="1.00"
                        />
                      </label>
                    </>
                  ) : null}
                  {capitalMode === 'fixed_gross' ? (
                    <label>
                      <span>Gross Exposure</span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={grossExposure}
                        onChange={(event) => {
                          setGrossExposure(event.target.value)
                          updateRunSetupDraft({ grossExposure: event.target.value })
                        }}
                        placeholder="1.00"
                      />
                    </label>
                  ) : null}
                  <div className="research-freeze-field" ref={frozenMenuRef}>
                    <span>Frozen Sleeves</span>
                    <button
                      type="button"
                      className="research-freeze-trigger"
                      onClick={() => setFrozenMenuOpen((current) => !current)}
                      disabled={!planningTaxonomyId || !selectableFrozenScopes.length || scopeOptionsLoading}
                      aria-haspopup="menu"
                      aria-expanded={frozenMenuOpen}
                    >
                      <span>{frozenMenuLabel}</span>
                      <span aria-hidden="true">v</span>
                    </button>
                    {frozenMenuOpen ? (
                      <div className="research-freeze-menu" role="menu">
                        {selectableFrozenScopes.map((option) => {
                          const nodeId = option.taxonomy_node_id ?? ''
                          return (
                            <label key={nodeId} className="research-freeze-option">
                              <input
                                type="checkbox"
                                checked={frozenNodeIds.includes(nodeId)}
                                onChange={() => toggleFrozenNode(nodeId)}
                              />
                              <span>{option.label}</span>
                            </label>
                          )
                        })}
                      </div>
                    ) : null}
                    </div>
                    <div className="research-bounds-field" ref={boundsMenuRef}>
                      <span>Bounds</span>
                      <button
                        type="button"
                        className="research-freeze-trigger research-bounds-trigger"
                        onClick={() => setBoundsMenuOpen((current) => !current)}
                        disabled={!planningTaxonomyId || !topSleeveOptions.length || scopeOptionsLoading}
                        aria-haspopup="menu"
                        aria-expanded={boundsMenuOpen}
                      >
                        <span>{boundsMenuLabel}</span>
                        <span aria-hidden="true">v</span>
                      </button>
                      {boundsMenuOpen ? (
                        <div className="research-bounds-menu" role="menu">
                          {topSleeveOptions.map((option) => {
                            const nodeId = option.taxonomy_node_id ?? ''
                            const bound = topSleeveBoundByNodeId.get(nodeId)
                            return (
                              <div key={nodeId} className="research-bounds-row">
                                <span>{option.label}</span>
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0"
                                  max="100"
                                  value={bound?.minWeightPct ?? ''}
                                  onChange={(event) => updateTopSleeveBound(nodeId, 'minWeightPct', event.target.value)}
                                  placeholder="Min %"
                                />
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0"
                                  max="100"
                                  value={bound?.maxWeightPct ?? ''}
                                  onChange={(event) => updateTopSleeveBound(nodeId, 'maxWeightPct', event.target.value)}
                                  placeholder="Max %"
                                />
                              </div>
                            )
                          })}
                        </div>
                      ) : null}
                    </div>
                    <label>
                      <span>Rebalance</span>
                    <select
                      value={backtestRebalanceFrequency}
                      onChange={(event) => {
                        const nextFrequency = event.target.value as PortfolioResearchBacktestRebalanceFrequency
                        setBacktestRebalanceFrequency(nextFrequency)
                        updateRunSetupDraft({ backtestRebalanceFrequency: nextFrequency })
                      }}
                    >
                      {REBALANCE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Cash Yield (%)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="-100"
                      max="100"
                      value={cashYieldPct}
                      onChange={(event) => {
                        setCashYieldPct(event.target.value)
                        updateRunSetupDraft({ cashYieldPct: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Commission (bps)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={commissionBps}
                      onChange={(event) => {
                        setCommissionBps(event.target.value)
                        updateRunSetupDraft({ commissionBps: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Sell Tax (bps)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={taxBps}
                      onChange={(event) => {
                        setTaxBps(event.target.value)
                        updateRunSetupDraft({ taxBps: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Slippage (bps)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={slippageBps}
                      onChange={(event) => {
                        setSlippageBps(event.target.value)
                        updateRunSetupDraft({ slippageBps: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Delay (days)</span>
                    <input
                      type="number"
                      step="1"
                      min="0"
                      max="30"
                      value={implementationDelayDays}
                      onChange={(event) => {
                        setImplementationDelayDays(event.target.value)
                        updateRunSetupDraft({ implementationDelayDays: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>WF Train (months)</span>
                    <input
                      type="number"
                      step="1"
                      min="1"
                      max="120"
                      value={walkForwardTrainingMonths}
                      onChange={(event) => {
                        setWalkForwardTrainingMonths(event.target.value)
                        updateRunSetupDraft({ walkForwardTrainingMonths: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>WF Test (months)</span>
                    <input
                      type="number"
                      step="1"
                      min="1"
                      max="60"
                      value={walkForwardTestMonths}
                      onChange={(event) => {
                        setWalkForwardTestMonths(event.target.value)
                        updateRunSetupDraft({ walkForwardTestMonths: event.target.value })
                      }}
                    />
                  </label>
                  <div className="research-benchmark-field">
                    <span>Benchmark</span>
                    <BenchmarkSearchBox
                      className="research-benchmark-search"
                      instruments={benchmarkInstruments}
                      selectedInstrumentId={benchmarkInstrumentId}
                      searchValue={benchmarkSearch}
                      onSearchChange={setBenchmarkSearch}
                      onSelectInstrument={(instrument) => {
                        setBenchmarkInstrumentId(instrument.instrument_id)
                        setBenchmarkSearch(benchmarkInstrumentLabel(instrument))
                        updateRunSetupDraft({ benchmarkInstrumentId: instrument.instrument_id })
                      }}
                      onClear={() => {
                        setBenchmarkInstrumentId('')
                        setBenchmarkSearch('')
                        updateRunSetupDraft({ benchmarkInstrumentId: '' })
                      }}
                      placeholder="Benchmark..."
                    />
                  </div>
                </div>
                <div className="research-run-action-field">
                  <button
                    type="button"
                    className="toolbar-link button-primary"
                    onClick={() => void handleRunResearch()}
                    disabled={actionPending === 'run' || scopeActionBlocked}
                  >
                    {actionPending === 'run' ? 'Running...' : 'Run'}
                  </button>
                </div>
                </div>
                <div className="research-robustness-editor">
                  <div className="research-robustness-header">
                    <span>Robustness Scenarios</span>
                    <button
                      type="button"
                      className="research-icon-button"
                      onClick={addRobustnessScenario}
                      aria-label="Add robustness scenario"
                      title="Add robustness scenario"
                    >
                      +
                    </button>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table research-robustness-table">
                      <thead>
                        <tr>
                          <th>ID</th>
                          <th>Label</th>
                          <th>Cash %</th>
                          <th>Commission</th>
                          <th>Tax</th>
                          <th>Slippage</th>
                          <th>Delay</th>
                          <th aria-label="Actions" />
                        </tr>
                      </thead>
                      <tbody>
                        {!robustnessScenarios.length ? (
                          <TableStatusRow colSpan={8} label="No robustness scenarios configured." />
                        ) : robustnessScenarios.map((scenario, index) => (
                          <tr key={`${scenario.scenario_id}:${index}`}>
                            <td>
                              <input
                                value={scenario.scenario_id}
                                onChange={(event) => updateRobustnessScenario(index, 'scenario_id', event.target.value)}
                                aria-label={`Scenario ${index + 1} ID`}
                              />
                            </td>
                            <td>
                              <input
                                value={scenario.label}
                                onChange={(event) => updateRobustnessScenario(index, 'label', event.target.value)}
                                aria-label={`Scenario ${index + 1} label`}
                              />
                            </td>
                            <td>
                              <input
                                type="number"
                                step="0.1"
                                min="-100"
                                max="100"
                                value={scenario.cash_yield_annual * 100}
                                onChange={(event) => updateRobustnessScenario(
                                  index,
                                  'cash_yield_annual',
                                  String(Number(event.target.value) / 100),
                                )}
                                aria-label={`Scenario ${index + 1} cash yield percent`}
                              />
                            </td>
                            {(['commission_bps', 'tax_bps', 'slippage_bps'] as const).map((field) => (
                              <td key={field}>
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0"
                                  value={scenario[field]}
                                  onChange={(event) => updateRobustnessScenario(index, field, event.target.value)}
                                  aria-label={`Scenario ${index + 1} ${field.replace(/_/g, ' ')}`}
                                />
                              </td>
                            ))}
                            <td>
                              <input
                                type="number"
                                step="1"
                                min="0"
                                max="30"
                                value={scenario.implementation_delay_days}
                                onChange={(event) => updateRobustnessScenario(
                                  index,
                                  'implementation_delay_days',
                                  event.target.value,
                                )}
                                aria-label={`Scenario ${index + 1} implementation delay days`}
                              />
                            </td>
                            <td>
                              <button
                                type="button"
                                className="research-icon-button"
                                onClick={() => removeRobustnessScenario(index)}
                                aria-label={`Remove ${scenario.label}`}
                                title="Remove scenario"
                              >
                                x
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                {scopeOptionsError ? <div className="inline-notice inline-notice-error">{scopeOptionsError}</div> : null}
              </fieldset>
            </form>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Research Universe</div>
              </div>
              <div className="portfolio-detail-meta">
                Lifecycle is derived from holdings and transaction history.
              </div>
            </div>
            <div className="table-shell">
              <table className="transactions-table research-universe-table">
                <thead>
                  <tr>
                    <th>Instrument</th>
                    <th>Status</th>
                    <th>Research Eligibility</th>
                    <th>PM Approval</th>
                  </tr>
                </thead>
                <tbody>
                  {!(workbench.instrument_universe ?? []).length ? (
                    <TableStatusRow colSpan={4} label="No instruments are available for Research." />
                  ) : (
                    (workbench.instrument_universe ?? []).map((instrument) => {
                      const isPending = eligibilityPendingId === instrument.instrument_id
                      return (
                        <tr key={instrument.instrument_id}>
                          <td>{instrument.instrument_ref?.instrument_name ?? instrument.instrument_id}</td>
                          <td>{researchLifecycleLabel(instrument.research_lifecycle)}</td>
                          <td>{researchEligibilityLabel(instrument.research_eligibility)}</td>
                          <td>
                            {instrument.research_lifecycle !== 'former' ? (
                              'Not required'
                            ) : (
                              <button
                                type="button"
                                className="toolbar-link"
                                disabled={eligibilityPendingId != null}
                                onClick={() => void handleResearchEligibilityApproval(
                                  instrument.instrument_id,
                                  !instrument.research_pm_approved,
                                )}
                              >
                                {isPending
                                  ? 'Saving...'
                                  : instrument.research_pm_approved
                                    ? 'Revoke approval'
                                    : 'Approve for research'}
                              </button>
                            )}
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {!latestRun ? (
            <section className="panel">
              <div className="empty-state">Run to generate the latest solved result.</div>
            </section>
          ) : latestRun.status === 'completed' && latestRun.detail == null ? (
            <section className="panel" aria-busy={runDetailLoading}>
              {runDetailLoading ? <CalculationStatus /> : (
                <div className="inline-notice inline-notice-error">
                  {runDetailError ?? 'Research run detail is unavailable.'}
                </div>
              )}
            </section>
          ) : latestRun.status === 'failed' ? (
            <section className="panel">
              <div className="inline-notice inline-notice-error">{latestRun.error_message ?? 'Research run failed.'}</div>
            </section>
          ) : (
            <>
              {staleRun ? (
                <section className="panel">
                  <div
                    className="inline-notice inline-notice-warning"
                    role="status"
                    title={staleRunDetail}
                    aria-label={`Historical result — not current or execution-ready. ${staleRunDetail}`}
                    tabIndex={0}
                  >
                    <strong>Historical result — not current or execution-ready.</strong>
                  </div>
                </section>
              ) : null}
              {nonExecutionReadySolveEvents.length ? (
                <section className="panel">
                  <div
                    className="inline-notice inline-notice-warning"
                    role="status"
                    title={constrainedSolveDetail}
                    aria-label={`Constrained solve — not execution-ready. ${constrainedSolveDetail}`}
                    tabIndex={0}
                  >
                    <strong>Constrained solve — not execution-ready.</strong>
                  </div>
                </section>
              ) : null}
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-title">{staleRun ? 'Historical Solved Result' : 'Solved Result'}</div>
                  </div>
                  <div className="portfolio-detail-meta">
                    {latestRun.as_of_date ?? '-'} · {staleRun ? 'Stale' : resolveStatusLabel(latestRun.status)} · {formatTimestamp(latestRun.finished_at)}
                  </div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table research-solved-table">
                    <thead>
                      <tr>
                        <th>Instrument</th>
                        <th>Current → Solved Weight</th>
                        <th>Current MV → Target Capital</th>
                        <th title="Risk-budget share within the instrument's immediate taxonomy sleeve.">
                          Local Target Risk
                        </th>
                        <th title="Portfolio-level risk contribution recomputed from solved leaf weights; hierarchical shrinkage can differ from local sleeve targets.">
                          Look-through RC
                        </th>
                        <th>Bounds</th>
                        <th>Bound</th>
                      </tr>
                    </thead>
                    <tbody>
                      {!solvedGroups.length ? (
                        <TableStatusRow colSpan={7} label="No solved result was recorded for the latest run." />
                      ) : (
                        solvedGroups.map((group) => (
                          <Fragment key={group.top_sleeve_id ?? group.top_sleeve_label}>
                            <tr className="research-result-group-row">
                              <td>{group.top_sleeve_label}</td>
                              <td className="research-transition-cell">
                                {formatMaybePercent(group.current_weight)} → {formatMaybePercent(group.solved_weight)}
                              </td>
                              <td className="research-transition-cell">
                                {formatCurrency(group.current_value_base, workbench?.base_currency, 0)} →{' '}
                                {formatCurrency(group.target_value_base, workbench?.base_currency, 0)}
                              </td>
                              <td>{formatMaybePercent(group.target_risk_share)}</td>
                              <td>{formatMaybePercent(group.forward_risk_contribution)}</td>
                              <td>{formatSolvedBounds(group.min_weight, group.max_weight)}</td>
                              <td>{formatBoundStatus(group.bound_status)}</td>
                            </tr>
                            {group.rows.map((row) => (
                              <tr key={`${row.member_type}:${row.member_id}`}>
                                <td className="research-result-member-cell">{row.label}</td>
                                <td className="research-transition-cell">
                                  {formatMaybePercent(row.current_weight)} → {formatMaybePercent(row.solved_weight)}
                                </td>
                                <td className="research-transition-cell">
                                  {formatCurrency(row.current_value_base, workbench?.base_currency, 0)} →{' '}
                                  {formatCurrency(row.target_value_base, workbench?.base_currency, 0)}
                                </td>
                                <td>{formatMaybePercent(row.target_risk_share)}</td>
                                <td>{formatMaybePercent(row.forward_risk_contribution)}</td>
                                <td>-</td>
                                <td>-</td>
                              </tr>
                            ))}
                          </Fragment>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-title">Rebalance Gaps</div>
                    <div className="panel-subtitle">
                      Current portfolio weight versus the solved weight; review liquidity, costs, and PM flags before trading.
                    </div>
                  </div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table research-rebalance-table">
                    <thead>
                      <tr>
                        <th>Instrument</th>
                        <th>Current Weight</th>
                        <th>Solved Weight</th>
                        <th>Gap</th>
                        <th>Action</th>
                        <th>Readiness</th>
                      </tr>
                    </thead>
                    <tbody>
                      {!rebalanceGaps.length ? (
                        <TableStatusRow colSpan={6} label="No material rebalance gaps were recorded for the latest run." />
                      ) : (
                        rebalanceGaps.map((row) => (
                          <tr key={`${row.member_type}:${row.member_id}`} title={row.execution_note ?? undefined}>
                            <td>{row.label}</td>
                            <td>{formatMaybePercent(row.current_weight)}</td>
                            <td>{formatMaybePercent(row.target_weight)}</td>
                            <td>{formatMaybePercent(row.gap)}</td>
                            <td>{row.action}</td>
                            <td>
                              {row.execution_status === 'manual_review_required' ? 'PM review' : 'Ready'}
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="performance-block-grid research-backtest-grid">
                <div className="portfolio-section-block research-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Backtest</div></div>
                  </div>
                  <ResearchLineChart
                    points={backtest?.points ?? []}
                    benchmarkPoints={displayBenchmark?.points ?? []}
                    benchmarkLabel={displayBenchmark?.label}
                  />
                  {benchmarkComparisonLoading ? (
                    <div className="research-comparison-status">Updating benchmark...</div>
                  ) : null}
                  {benchmarkComparisonError ? (
                    <div className="research-comparison-status research-comparison-status-error">
                      {benchmarkComparisonError}
                    </div>
                  ) : null}
                </div>
                <div className="portfolio-section-block research-metrics-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Metrics</div></div>
                  </div>
                  <MetricTable
                    run={latestRun}
                    benchmark={displayBenchmark}
                    relativeMetrics={displayRelativeMetrics}
                  />
                </div>
              </section>

              <section className="performance-block-grid research-validation-grid">
                <div className="portfolio-section-block">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Point-in-Time Coverage</div></div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table research-validation-summary-table">
                      <tbody>
                        <tr>
                          <th>Method</th>
                          <td>{backtest?.methodology?.name ?? 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Status</th>
                          <td
                            title={pointInTimeCoverage?.unavailable_reason ?? undefined}
                            tabIndex={pointInTimeCoverage?.unavailable_reason ? 0 : undefined}
                          >
                            {pointInTimeCoverage ? formatLabel(pointInTimeCoverage.status) : 'N/A'}
                          </td>
                        </tr>
                        <tr>
                          <th>Decisions</th>
                          <td>{pointInTimeCoverage?.decision_count ?? 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Skipped Decisions</th>
                          <td
                            title={skippedRebalanceDetail || undefined}
                            tabIndex={skippedRebalanceDetail ? 0 : undefined}
                          >
                            {pointInTimeCoverage ? skippedRebalances.length : 'N/A'}
                          </td>
                        </tr>
                        <tr>
                          <th>Configuration Versions</th>
                          <td>{configurationVersionsUsed.length
                            ? configurationVersionsUsed.join(', ')
                            : 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Decision Window</th>
                          <td>{pointInTimeCoverage?.first_decision_date && pointInTimeCoverage.last_decision_date
                            ? `${pointInTimeCoverage.first_decision_date} to ${pointInTimeCoverage.last_decision_date}`
                            : 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Total Turnover</th>
                          <td>{formatMaybePercent(backtest?.total_turnover)}</td>
                        </tr>
                        <tr>
                          <th>Total Cost</th>
                          <td>{formatMaybePercent(backtest?.total_cost)}</td>
                        </tr>
                        <tr>
                          <th>Contribution Residual</th>
                          <td>{latestContributionReconciliation
                            ? formatMaybeNumber(latestContributionReconciliation.residual, 8)
                            : 'N/A'}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
                <div className="portfolio-section-block">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Execution & Costs</div></div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table research-execution-table">
                      <thead>
                        <tr>
                          <th>Decision</th>
                          <th>Execution</th>
                          <th>Config</th>
                          <th>Buy</th>
                          <th>Sell</th>
                          <th>Turnover</th>
                          <th>Cost</th>
                        </tr>
                      </thead>
                      <tbody>
                        {!executionRecords.length ? (
                          <TableStatusRow colSpan={7} label="N/A" />
                        ) : executionRecords.map((record, index) => (
                          <tr key={`${record.decision_date}:${record.actual_execution_date}:${index}`}>
                            <td>{record.decision_date}</td>
                            <td>{record.actual_execution_date}</td>
                            <td>{record.taxonomy_configuration_version ?? 'N/A'}</td>
                            <td>{formatMaybePercent(record.risky_buy_turnover)}</td>
                            <td>{formatMaybePercent(record.risky_sell_turnover)}</td>
                            <td>{formatMaybePercent(record.one_way_turnover)}</td>
                            <td>{formatMaybePercent(record.total_cost)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>

              <section className="performance-block-grid research-validation-grid">
                <div className="portfolio-section-block">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Robustness</div></div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table research-robustness-results-table">
                      <thead>
                        <tr>
                          <th>Scenario</th>
                          <th>Cash</th>
                          <th>Commission</th>
                          <th>Tax</th>
                          <th>Slippage</th>
                          <th>Delay</th>
                          <th>Return</th>
                          <th>Delta</th>
                          <th>Max DD</th>
                          <th>Cost</th>
                        </tr>
                      </thead>
                      <tbody>
                        {!robustnessResults.length ? (
                          <TableStatusRow colSpan={10} label="N/A" />
                        ) : robustnessResults.map((result) => (
                          <tr key={result.scenario_id}>
                            <td>{result.label}</td>
                            <td>{formatMaybePercent(result.cash_yield_annual)}</td>
                            <td>{formatMaybeNumber(result.commission_bps, 1)}</td>
                            <td>{formatMaybeNumber(result.tax_bps, 1)}</td>
                            <td>{formatMaybeNumber(result.slippage_bps, 1)}</td>
                            <td>{result.implementation_delay_days}D</td>
                            <td>{formatMaybePercent(result.metrics?.period_return)}</td>
                            <td>{formatMaybePercent(result.period_return_delta)}</td>
                            <td>{formatMaybePercent(result.metrics?.max_drawdown)}</td>
                            <td>{formatMaybePercent(result.total_cost)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div className="portfolio-section-block">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div
                        className="panel-title"
                        title={walkForward?.methodology_note ?? undefined}
                        tabIndex={walkForward?.methodology_note ? 0 : undefined}
                      >
                        Rolling OOS Holdout
                      </div>
                    </div>
                  </div>
                  <div className="research-oos-summary">
                    <span>OOS Return <strong>{formatMaybePercent(walkForward?.oos_metrics?.period_return)}</strong></span>
                    <span>OOS Volatility <strong>{formatMaybePercent(walkForward?.oos_metrics?.annualized_volatility)}</strong></span>
                    <span>OOS Max DD <strong>{formatMaybePercent(walkForward?.oos_metrics?.max_drawdown)}</strong></span>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table research-walk-forward-table">
                      <thead>
                        <tr>
                          <th>Training (diagnostic)</th>
                          <th>Test</th>
                          <th>Configs</th>
                          <th>OOS Return</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {!walkForward?.windows?.length ? (
                          <TableStatusRow
                            colSpan={5}
                            label={walkForward?.unavailable_reason ? 'Unavailable' : 'N/A'}
                            detail={walkForward?.unavailable_reason}
                          />
                        ) : walkForward.windows.map((window) => (
                          <tr key={`${window.training_start_date}:${window.test_start_date}`}>
                            <td>{window.training_start_date} to {window.training_end_date}</td>
                            <td>{window.test_start_date} to {window.test_end_date}</td>
                            <td>{(window.configuration_versions_used ?? []).length
                              ? (window.configuration_versions_used ?? []).join(', ')
                              : 'N/A'}</td>
                            <td>{formatMaybePercent(window.metrics?.period_return)}</td>
                            <td>{window.available ? 'Available' : window.unavailable_reason ?? 'Unavailable'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>

              <section className="performance-block-grid research-sleeve-grid">
                <div className="portfolio-section-block research-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Top-Level Sleeve Weights</div></div>
                  </div>
                  <ResearchSleeveStackedAreaChart points={backtest?.top_sleeve_weight_points ?? []} />
                </div>
                <div className="portfolio-section-block research-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Top-Level Sleeve Contribution</div></div>
                  </div>
                  <ResearchSleeveLineChart
                    points={backtest?.top_sleeve_contribution_points ?? []}
                    ariaLabel="Top-level sleeve contribution"
                  />
                </div>
              </section>
            </>
          )}
        </>
      ) : null}
    </PortfolioWorkspaceLayout>
  )
}
