import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import BenchmarkSearchBox, { benchmarkInstrumentLabel } from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioResearchRun,
  getPortfolioInstruments,
  getPortfolioRiskPolicy,
  getPortfolioTaxonomyCatalog,
  getPortfolioResearchWorkbench,
  updatePortfolioResearchSettings,
  type PortfolioResearchBacktestPointRecord,
  type PortfolioResearchBacktestRebalanceFrequency,
  type PortfolioResearchBacktestSleevePointRecord,
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
  formatLabel,
  formatNumber,
  formatPercent,
} from '../lib/format'

const CAPITAL_MODE_OPTIONS = [
  { value: 'unit_notional', label: 'Unit' },
  { value: 'fixed_gross', label: 'Fixed Gross' },
  { value: 'target_volatility', label: 'Vol Target' },
  { value: 'volatility_cap', label: 'Vol Cap' },
] as const

const REBALANCE_OPTIONS: Array<{ value: PortfolioResearchBacktestRebalanceFrequency; label: string }> = [
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
]

type ResearchRunSetupDraft = {
  capitalMode: PortfolioResearchCapitalMode
  grossExposure: string
  targetVolatilityPct: string
  maxGrossExposure: string
  frozenNodeIds: string[]
  topSleeveBounds: ResearchTopSleeveBoundDraft[]
  backtestRebalanceFrequency: PortfolioResearchBacktestRebalanceFrequency
  benchmarkInstrumentId: string
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
}: {
  colSpan: number
  label: string
  tone?: 'neutral' | 'error'
}) {
  return (
    <tr className="table-status-row">
      <td colSpan={colSpan} className={`empty-state-cell ${tone === 'error' ? 'table-status-cell-error' : ''}`}>
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

function formatBoundInput(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return ''
  }
  return String(Number((value * 100).toFixed(4)))
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
  },
) {
  const time = new Date(point.date).getTime()
  const value = point.value ?? 0
  const xRange = Math.max(args.maxTime - args.minTime, 1)
  const yRange = Math.max(args.maxValue - args.minValue, 0.000001)
  return {
    x: args.padding + ((time - args.minTime) / xRange) * (args.width - args.padding * 2),
    y: args.height - args.padding - ((value - args.minValue) / yRange) * (args.height - args.padding * 2),
  }
}

function buildPath(points: Array<{ date: string; value?: number | null }>, args: Parameters<typeof chartCoordinate>[1]) {
  const coordinates = points
    .filter((point) => point.value != null)
    .map((point) => chartCoordinate(point, args))
  if (!coordinates.length) {
    return ''
  }
  return coordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ')
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
  const height = 260
  const padding = 28
  const times = allPoints.map((point) => new Date(point.date).getTime())
  const values = allPoints.map((point) => point.value ?? 0)
  const minTime = Math.min(...times)
  const maxTime = Math.max(...times)
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const yPad = Math.max((maxValue - minValue) * 0.08, 0.01)
  const args = { minTime, maxTime, minValue: minValue - yPad, maxValue: maxValue + yPad, width, height, padding }
  return (
    <div className="research-chart">
      <div className="research-chart-legend">
        <span><i style={{ background: CHART_COLORS[0] }} />Solved</span>
        {visibleBenchmark.length > 1 ? <span><i style={{ background: CHART_COLORS[2] }} />{benchmarkLabel ?? 'Benchmark'}</span> : null}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="research-chart-svg" role="img" aria-label="Backtest curve">
        <line x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} className="research-chart-axis" />
        <path d={buildPath(visiblePoints, args)} className="research-chart-line" style={{ stroke: CHART_COLORS[0] }} />
        {visibleBenchmark.length > 1 ? (
          <path d={buildPath(visibleBenchmark, args)} className="research-chart-line research-chart-line-muted" style={{ stroke: CHART_COLORS[2] }} />
        ) : null}
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
  const height = 240
  const padding = 28
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
        <line x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} className="research-chart-axis" />
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
  const height = 240
  const padding = 28
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
        <line x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} className="research-chart-axis" />
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
}: {
  run: PortfolioResearchRunRecord
}) {
  const metrics = run.detail?.backtest?.metrics ?? null
  const relative = run.detail?.backtest_relative_metrics ?? null
  const periodLabel =
    metrics?.start_date && metrics?.end_date
      ? `${metrics.start_date} to ${metrics.end_date}`
      : '-'
  const mddPeriod =
    metrics?.max_drawdown_start_date && metrics?.max_drawdown_end_date
      ? `${metrics.max_drawdown_start_date} to ${metrics.max_drawdown_end_date}`
      : '-'
  return (
    <table className="performance-summary-table research-metric-table">
      <tbody>
        <tr><th>Period</th><td>{periodLabel}</td></tr>
        <tr><th>Annual Return</th><td>{formatMaybePercent(metrics?.annualized_return)}</td></tr>
        <tr><th>Annual Volatility</th><td>{formatMaybePercent(metrics?.annualized_volatility)}</td></tr>
        <tr><th>Sharpe</th><td>{formatMaybeNumber(metrics?.sharpe_ratio)}</td></tr>
        <tr><th>Max Drawdown</th><td>{formatMaybePercent(metrics?.max_drawdown)}</td></tr>
        <tr><th>MDD Period</th><td>{mddPeriod}</td></tr>
        <tr><th>Recovery Time</th><td>{metrics?.max_drawdown_recovery_days != null ? `${metrics.max_drawdown_recovery_days}D` : '-'}</td></tr>
        <tr><th>Excess Return</th><td>{formatMaybePercent(relative?.excess_return)}</td></tr>
        <tr><th>Tracking Error</th><td>{formatMaybePercent(relative?.tracking_error)}</td></tr>
        <tr><th>Information Ratio</th><td>{formatMaybeNumber(relative?.information_ratio)}</td></tr>
      </tbody>
    </table>
  )
}

export default function ResearchPage() {
  const { portfolioId = '' } = useParams()
  const [workbench, setWorkbench] = useState<PortfolioResearchWorkbenchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState<'run' | null>(null)
  const [frozenMenuOpen, setFrozenMenuOpen] = useState(false)
  const [boundsMenuOpen, setBoundsMenuOpen] = useState(false)
  const [dynamicScopeOptions, setDynamicScopeOptions] = useState<PortfolioResearchPlanningScopeOption[] | null>(null)
  const [scopeOptionsLoading, setScopeOptionsLoading] = useState(false)
  const [scopeOptionsError, setScopeOptionsError] = useState<string | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const frozenMenuRef = useRef<HTMLDivElement | null>(null)
  const boundsMenuRef = useRef<HTMLDivElement | null>(null)
  const autoSaveTimeoutRef = useRef<number | null>(null)
  const runSetupDraftRef = useRef<ResearchRunSetupDraft>({
    capitalMode: 'unit_notional',
    grossExposure: '',
    targetVolatilityPct: '',
    maxGrossExposure: '',
    frozenNodeIds: [],
    topSleeveBounds: [],
    backtestRebalanceFrequency: '1m',
    benchmarkInstrumentId: '',
  })

  const [planningTaxonomyId, setPlanningTaxonomyId] = useState('')
  const [asOfDate, setAsOfDate] = useState('')
  const [capitalMode, setCapitalMode] = useState<PortfolioResearchCapitalMode>('unit_notional')
  const [grossExposure, setGrossExposure] = useState('')
  const [targetVolatilityPct, setTargetVolatilityPct] = useState('')
  const [maxGrossExposure, setMaxGrossExposure] = useState('')
  const [frozenNodeIds, setFrozenNodeIds] = useState<string[]>([])
  const [topSleeveBounds, setTopSleeveBounds] = useState<ResearchTopSleeveBoundDraft[]>([])
  const [backtestRebalanceFrequency, setBacktestRebalanceFrequency] = useState<PortfolioResearchBacktestRebalanceFrequency>('1m')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')

  async function reloadWorkbench() {
    if (!portfolioId) {
      setWorkbench(null)
      setLoading(false)
      setWorkspaceError(null)
      return
    }
    setLoading(true)
    try {
      const response = await getPortfolioResearchWorkbench(portfolioId)
      setWorkbench(response)
      setWorkspaceError(null)
    } catch (error) {
      setWorkbench(null)
      setWorkspaceError(extractErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void reloadWorkbench()
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
    return () => {
      if (autoSaveTimeoutRef.current != null) {
        window.clearTimeout(autoSaveTimeoutRef.current)
      }
    }
  }, [])

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

  useEffect(() => {
    if (!workbench) {
      return
    }
	    const nextBenchmarkInstrumentId = workbench.settings.backtest_benchmark_instrument_id ?? ''
	    const nextCapitalMode = workbench.settings.capital_mode
	    const nextTopSleeveBounds = (workbench.settings.top_sleeve_weight_bounds ?? []).map((item) => ({
	      taxonomyNodeId: item.taxonomy_node_id,
	      minWeightPct: formatBoundInput(item.min_weight),
	      maxWeightPct: formatBoundInput(item.max_weight),
	    }))
	    const nextDraft: ResearchRunSetupDraft = {
	      capitalMode: nextCapitalMode,
      grossExposure: workbench.settings.gross_exposure != null ? String(workbench.settings.gross_exposure) : '',
      targetVolatilityPct:
        workbench.settings.target_volatility != null ? String(workbench.settings.target_volatility * 100) : '',
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
    }
    setPlanningTaxonomyId(workbench.default_planning_taxonomy_id ?? workbench.settings.planning_taxonomy_id ?? '')
    setAsOfDate(workbench.as_of_date)
    setCapitalMode(nextDraft.capitalMode)
    setGrossExposure(nextDraft.grossExposure)
    setTargetVolatilityPct(nextDraft.targetVolatilityPct)
	    setMaxGrossExposure(nextDraft.maxGrossExposure)
	    setFrozenNodeIds(nextDraft.frozenNodeIds)
	    setTopSleeveBounds(nextTopSleeveBounds)
	    setBacktestRebalanceFrequency(nextDraft.backtestRebalanceFrequency)
    setBenchmarkInstrumentId(nextBenchmarkInstrumentId)
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
    runSetupDraftRef.current = nextDraft
    if (autoSaveTimeoutRef.current != null) {
      window.clearTimeout(autoSaveTimeoutRef.current)
    }
    autoSaveTimeoutRef.current = window.setTimeout(() => {
      autoSaveTimeoutRef.current = null
      void persistSettings({
        draft: nextDraft,
        analysisDate: workbench?.as_of_date ?? null,
      }).catch(() => undefined)
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
    analysisDate = asOfDate,
  }: {
    draft?: ResearchRunSetupDraft
    analysisDate?: string | null
  } = {}) {
    if (!portfolioId) {
      return null
    }
    const parsedGrossExposure = draft.grossExposure.trim() ? Number(draft.grossExposure) : null
    const parsedTargetVolatility = draft.targetVolatilityPct.trim() ? Number(draft.targetVolatilityPct) / 100 : null
    const parsedMaxGrossExposure = draft.maxGrossExposure.trim() ? Number(draft.maxGrossExposure) : null
    const volatilityMode = isVolatilityCapitalMode(draft.capitalMode)
	    const riskPolicy = await getPortfolioRiskPolicy(portfolioId)
	    const validFrozenNodeIds = new Set(selectableFrozenScopes.map((item) => item.taxonomy_node_id ?? ''))
	    const frozenTaxonomyNodeIds = draft.frozenNodeIds.filter((nodeId) => validFrozenNodeIds.has(nodeId))
	    const topSleeveWeightBounds = serializeTopSleeveBounds(draft.topSleeveBounds)
	    return updatePortfolioResearchSettings(portfolioId, {
      planning_taxonomy_id: planningTaxonomyId || null,
      comparator_taxonomy_node_id: null,
      as_of_date: analysisDate || null,
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
      notes: null,
    })
  }

  async function handleRunResearch() {
    if (!portfolioId) {
      return
    }
    if (autoSaveTimeoutRef.current != null) {
      window.clearTimeout(autoSaveTimeoutRef.current)
      autoSaveTimeoutRef.current = null
    }
    setActionPending('run')
    setActionError(null)
    setNotice(null)
    try {
      await persistSettings({
        draft: runSetupDraftRef.current,
        analysisDate: asOfDate,
      })
      await createPortfolioResearchRun(portfolioId, { requested_by: 'workspace-ui' })
      setNotice('Research run completed.')
      await reloadWorkbench()
    } catch (error) {
      setActionError(extractErrorMessage(error))
      try {
        await reloadWorkbench()
      } catch {
        // Keep the original run error visible if the follow-up refresh also fails.
      }
    } finally {
      setActionPending(null)
    }
  }

  const solvedGroups = latestRun?.detail?.solved_result_groups ?? []
  const backtest = latestRun?.detail?.backtest ?? null
  const benchmark = latestRun?.detail?.backtest_benchmark ?? null

  return (
    <PortfolioWorkspaceLayout activeSection="Research" toolbarLabel="View: Research Workbench">
      {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
      {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
      {actionError ? <div className="inline-notice inline-notice-error">{actionError}</div> : null}
      {loading && !workbench ? <CalculationStatus /> : null}

      {!loading && !workbench && !workspaceError ? <div className="empty-state">No data.</div> : null}

      {workbench ? (
        <>
          <section className="panel">
            <form
              className="transaction-form taxonomy-form-compact research-run-form"
              onSubmit={(event) => event.preventDefault()}
            >
              <div className="research-settings-bar">
                <div className="taxonomy-form-grid taxonomy-form-grid-wide research-settings-grid">
                  <label>
                    <span>Analysis Date</span>
                    <input type="date" value={asOfDate} onChange={(event) => setAsOfDate(event.target.value)} />
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
              {scopeOptionsError ? <div className="inline-notice inline-notice-error">{scopeOptionsError}</div> : null}
            </form>
          </section>

          {!latestRun ? (
            <section className="panel">
              <div className="empty-state">Run to generate the latest solved result.</div>
            </section>
          ) : latestRun.status === 'failed' ? (
            <section className="panel">
              <div className="inline-notice inline-notice-error">{latestRun.error_message ?? 'Research run failed.'}</div>
            </section>
          ) : (
            <>
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-title">Solved Result</div>
                  </div>
                  <div className="portfolio-detail-meta">
                    {latestRun.as_of_date ?? '-'} · {resolveStatusLabel(latestRun.status)} · {formatTimestamp(latestRun.finished_at)}
                  </div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table research-solved-table">
                    <thead>
                      <tr>
                        <th>Instrument</th>
	                        <th>Solved Weight</th>
	                        <th>Target Risk</th>
	                        <th>Forward RC</th>
	                        <th>Bounds</th>
	                        <th>Bound</th>
	                      </tr>
	                    </thead>
	                    <tbody>
	                      {!solvedGroups.length ? (
	                        <TableStatusRow colSpan={6} label="No solved result was recorded for the latest run." />
	                      ) : (
                        solvedGroups.map((group) => (
                          <Fragment key={group.top_sleeve_id ?? group.top_sleeve_label}>
                            <tr className="research-result-group-row">
                              <td>{group.top_sleeve_label}</td>
	                              <td>{formatMaybePercent(group.solved_weight)}</td>
	                              <td>{formatMaybePercent(group.target_risk_share)}</td>
	                              <td>{formatMaybePercent(group.forward_risk_contribution)}</td>
	                              <td>{formatSolvedBounds(group.min_weight, group.max_weight)}</td>
	                              <td>{formatBoundStatus(group.bound_status)}</td>
	                            </tr>
                            {group.rows.map((row) => (
                              <tr key={`${row.member_type}:${row.member_id}`}>
                                <td className="research-result-member-cell">{row.label}</td>
	                                <td>{formatMaybePercent(row.solved_weight)}</td>
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

              <section className="performance-block-grid research-backtest-grid">
                <div className="performance-section-block research-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Backtest</div></div>
                  </div>
                  <ResearchLineChart
                    points={backtest?.points ?? []}
                    benchmarkPoints={benchmark?.points ?? []}
                    benchmarkLabel={benchmark?.label}
                  />
                </div>
                <div className="performance-section-block research-metrics-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Metrics</div></div>
                  </div>
                  <MetricTable run={latestRun} />
                </div>
              </section>

              <section className="performance-block-grid research-backtest-grid">
                <div className="performance-section-block research-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Top Sleeve Weights</div></div>
                  </div>
                  <ResearchSleeveStackedAreaChart points={backtest?.top_sleeve_weight_points ?? []} />
                </div>
                <div className="performance-section-block research-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Top Sleeve Contribution</div></div>
                  </div>
                  <ResearchSleeveLineChart
                    points={backtest?.top_sleeve_contribution_points ?? []}
                    ariaLabel="Top sleeve contribution"
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
