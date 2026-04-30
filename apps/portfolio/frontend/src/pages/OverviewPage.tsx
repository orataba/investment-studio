import { useDeferredValue, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import RiskRankedBars from '../components/RiskRankedBars'
import {
  getHoldingsWorkspace,
  getPortfolioAssetPriceChart,
  getPortfolioPerformance,
  getPortfolioInstruments,
  getPortfolioTaxonomyCatalog,
  getWorkspaceSummaryForPortfolio,
  type HoldingsWorkspaceResponse,
  type PortfolioAssetPriceChartPoint,
  type PortfolioAssetPriceChartResponse,
  type PortfolioHoldingRow,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceResponse,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioWorkspaceSummary,
  type SharedInstrumentRecord,
  type SparklinePoint,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatPercent,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'

type AllocationBucket = {
  id: string
  label: string
  topLevelId: string
  topLevelLabel: string
  value: number
  weight: number
  holdingsCount: number
}

type TopHoldingColumnKey =
  | 'asset'
  | 'identifier'
  | 'sleeve'
  | 'sparkline'
  | 'quantity'
  | 'last_price'
  | 'market_value'
  | 'cost_basis'
  | 'unrealized_pnl'
  | 'day_change'
  | 'weight'
  | 'coverage'

type TopHoldingColumnDefinition = {
  key: TopHoldingColumnKey
  label: string
  render: (row: PortfolioHoldingRow) => ReactNode
}

type OverviewMetricRow = {
  label: string
  value: string
  benchmark?: string | null
  emphasis?: boolean
  toneClassName?: string
}

const DEFAULT_TOP_HOLDING_COLUMNS: TopHoldingColumnKey[] = [
  'asset',
  'identifier',
  'sleeve',
  'sparkline',
  'market_value',
  'unrealized_pnl',
  'day_change',
  'weight',
  'coverage',
]

const TOP_HOLDING_COLUMN_LABELS: Record<TopHoldingColumnKey, string> = {
  asset: 'Asset',
  identifier: 'Identifier',
  sleeve: 'Sleeve',
  sparkline: 'Sparkchart',
  quantity: 'Quantity',
  last_price: 'Last Price',
  market_value: 'Market Value',
  cost_basis: 'Cost Basis',
  unrealized_pnl: 'Unrealized P&L',
  day_change: 'Day Change',
  weight: 'Weight',
  coverage: 'Coverage',
}

const TOP_HOLDING_COLUMN_GROUPS: Array<{ label: string; columns: TopHoldingColumnKey[] }> = [
  { label: 'Core', columns: ['asset', 'identifier', 'sleeve', 'coverage'] },
  { label: 'Market', columns: ['sparkline', 'last_price', 'market_value', 'weight', 'day_change'] },
  { label: 'Position', columns: ['quantity', 'cost_basis', 'unrealized_pnl'] },
]

const DONUT_COLORS = ['#0b72d7', '#0f766e', '#f59e0b', '#7c3aed', '#db2777', '#64748b', '#14b8a6']

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.asset_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.asset_core.identifiers[0]?.identifier_value ??
    row.asset_core.asset_id
  )
}

function instrumentPrimaryIdentifier(instrument: SharedInstrumentRecord) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.asset_id
  )
}

function benchmarkInstrumentLabel(instrument: SharedInstrumentRecord) {
  return `${instrumentPrimaryIdentifier(instrument)} · ${instrument.asset_name}`
}

function signedPercent(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  const absolute = formatPercent(Math.abs(value), digits)
  if (value > 0) {
    return `+${absolute}`
  }
  if (value < 0) {
    return `-${absolute}`
  }
  return absolute
}

function dateFromString(date: string) {
  const time = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00:00` : date)
  return Number.isNaN(time) ? null : new Date(time)
}

function addDays(date: Date, days: number) {
  const nextDate = new Date(date)
  nextDate.setDate(nextDate.getDate() + days)
  return nextDate
}

function formatDateKey(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function findAnchorPerformancePoint(points: PortfolioDailyPerformancePoint[], targetDate: string) {
  let anchor: PortfolioDailyPerformancePoint | null = null
  points.forEach((point) => {
    if (point.as_of_date <= targetDate) {
      anchor = point
    }
  })
  return anchor ?? points[0] ?? null
}

function periodReturnFromTtwror(
  points: PortfolioDailyPerformancePoint[],
  targetDate: string | null,
) {
  const sortedPoints = points
    .filter((point) => point.cumulative_ttwror != null || point.ending_nav != null)
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
  if (sortedPoints.length < 2) {
    return null
  }

  const latestPoint = sortedPoints[sortedPoints.length - 1]
  const anchorPoint = targetDate ? findAnchorPerformancePoint(sortedPoints, targetDate) : sortedPoints[0]
  if (!anchorPoint) {
    return null
  }

  if (latestPoint.cumulative_ttwror != null && anchorPoint.cumulative_ttwror != null) {
    const anchorGrowth = 1 + anchorPoint.cumulative_ttwror
    return anchorGrowth !== 0 ? (1 + latestPoint.cumulative_ttwror) / anchorGrowth - 1 : null
  }

  if (latestPoint.ending_nav != null && anchorPoint.ending_nav != null && anchorPoint.ending_nav !== 0) {
    return latestPoint.ending_nav / anchorPoint.ending_nav - 1
  }

  return null
}

function buildPortfolioReturnMetrics(points: PortfolioDailyPerformancePoint[]) {
  const sortedPoints = points.slice().sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
  const latestPoint = sortedPoints[sortedPoints.length - 1]
  const latestDate = latestPoint ? dateFromString(latestPoint.as_of_date) : null
  if (!latestPoint || !latestDate) {
    return {
      oneWeek: null,
      mtd: null,
      ytd: null,
    }
  }

  const monthStart = new Date(latestDate.getFullYear(), latestDate.getMonth(), 1)
  const yearStart = new Date(latestDate.getFullYear(), 0, 1)

  return {
    oneWeek: periodReturnFromTtwror(sortedPoints, formatDateKey(addDays(latestDate, -7))),
    mtd: periodReturnFromTtwror(sortedPoints, formatDateKey(monthStart)),
    ytd: periodReturnFromTtwror(sortedPoints, formatDateKey(yearStart)),
  }
}

function periodReturnFromValuePoints(
  points: PortfolioAssetPriceChartPoint[],
  targetDate: string | null,
) {
  const sortedPoints = points
    .filter((point) => Number.isFinite(point.value))
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  if (sortedPoints.length < 2) {
    return null
  }

  const latestPoint = sortedPoints[sortedPoints.length - 1]
  const anchorPoint = targetDate
    ? sortedPoints.reduce<PortfolioAssetPriceChartPoint | null>(
        (current, point) => (point.date <= targetDate ? point : current),
        null,
      ) ?? sortedPoints[0]
    : sortedPoints[0]
  return anchorPoint.value !== 0 ? latestPoint.value / anchorPoint.value - 1 : null
}

function sampleStandardDeviation(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance =
    values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1)
  return Math.sqrt(variance)
}

function buildDrawdownMetrics(points: PortfolioAssetPriceChartPoint[]) {
  let highWater = points[0]?.value ?? 0
  let maxDrawdown: number | null = null
  let currentDrawdown: number | null = null

  points.forEach((point) => {
    highWater = Math.max(highWater, point.value)
    const drawdown = highWater > 0 ? point.value / highWater - 1 : null
    if (drawdown != null) {
      currentDrawdown = drawdown
      maxDrawdown = maxDrawdown == null ? drawdown : Math.min(maxDrawdown, drawdown)
    }
  })

  return { currentDrawdown, maxDrawdown }
}

function buildBenchmarkMetrics(points: PortfolioAssetPriceChartPoint[]) {
  const sortedPoints = points
    .filter((point) => Number.isFinite(point.value))
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  const latestPoint = sortedPoints[sortedPoints.length - 1]
  const firstPoint = sortedPoints[0]
  const latestDate = latestPoint ? dateFromString(latestPoint.date) : null
  if (!firstPoint || !latestPoint || !latestDate) {
    return null
  }

  const monthStart = new Date(latestDate.getFullYear(), latestDate.getMonth(), 1)
  const yearStart = new Date(latestDate.getFullYear(), 0, 1)
  const dailyReturns = sortedPoints
    .slice(1)
    .map((point, index) => {
      const previous = sortedPoints[index]
      return previous.value !== 0 ? point.value / previous.value - 1 : null
    })
    .filter((value): value is number => value != null)
  const volatility = sampleStandardDeviation(dailyReturns)
  const startDate = dateFromString(firstPoint.date)
  const daySpan = startDate ? Math.max(1, (latestDate.getTime() - startDate.getTime()) / 86_400_000) : null
  const sinceInception = firstPoint.value !== 0 ? latestPoint.value / firstPoint.value - 1 : null
  const annualizedReturn =
    sinceInception != null && daySpan != null
      ? (1 + sinceInception) ** (365.25 / daySpan) - 1
      : null
  const annualizedVolatility = volatility == null ? null : volatility * Math.sqrt(252)
  const drawdowns = buildDrawdownMetrics(sortedPoints)

  return {
    oneWeek: periodReturnFromValuePoints(sortedPoints, formatDateKey(addDays(latestDate, -7))),
    mtd: periodReturnFromValuePoints(sortedPoints, formatDateKey(monthStart)),
    ytd: periodReturnFromValuePoints(sortedPoints, formatDateKey(yearStart)),
    sinceInception,
    annualizedReturn,
    annualizedVolatility,
    sharpe:
      annualizedReturn != null && annualizedVolatility != null && annualizedVolatility !== 0
        ? annualizedReturn / annualizedVolatility
        : null,
    currentDrawdown: drawdowns.currentDrawdown,
    maxDrawdown: drawdowns.maxDrawdown,
  }
}

function benchmarkNote(
  selected: SharedInstrumentRecord | null,
  loading: boolean,
  value: number | null | undefined,
  formatter: (input: number | null | undefined) => string = signedPercent,
) {
  if (!selected) {
    return null
  }
  if (loading) {
    return 'BM loading'
  }
  return `BM ${formatter(value)}`
}

function MiniSparkline({ values }: { values: SparklinePoint[] }) {
  if (values.length < 2) {
    return <span className="sparkline-empty">—</span>
  }

  const width = 88
  const height = 24
  const min = Math.min(...values.map((point) => point.value))
  const max = Math.max(...values.map((point) => point.value))
  const span = max - min || 1
  const line = values
    .map((point, index) => {
      const x = (index / (values.length - 1)) * (width - 1)
      const y = height - ((point.value - min) / span) * (height - 6) - 2
      return `${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' L ')

  const firstValue = values[0]?.value ?? 0
  const lastValue = values[values.length - 1]?.value ?? firstValue
  const stroke = lastValue >= firstValue ? '#0f766e' : '#b42318'
  const fill = lastValue >= firstValue ? 'rgba(15, 118, 110, 0.12)' : 'rgba(180, 35, 24, 0.12)'
  const area = `${line} L ${width - 1} ${height} L 0 ${height} Z`

  return (
    <svg className="mini-sparkline" viewBox="0 0 88 24" aria-hidden="true">
      <path d={`M ${area}`} fill={fill} />
      <path d={`M ${line}`} fill="none" stroke={stroke} strokeWidth="1.8" />
    </svg>
  )
}

function StrategySleeveDonut({
  segments,
}: {
  segments: Array<{ id: string; label: string; value: number; valueLabel: string; detail: string }>
}) {
  const visibleSegments = segments.filter((segment) => segment.value > 0)
  const total = visibleSegments.reduce((sum, segment) => sum + segment.value, 0)
  if (!visibleSegments.length || total <= 0) {
    return <div className="price-chart-empty">No sleeve allocations are available.</div>
  }

  const radius = 44
  const circumference = 2 * Math.PI * radius
  let offset = 0

  return (
    <div className="overview-sleeve-donut">
      <div className="overview-sleeve-donut-chart">
        <svg viewBox="0 0 120 120" role="img" aria-label="Strategy sleeve allocation">
          <circle className="overview-sleeve-donut-track" cx="60" cy="60" r={radius} />
          {visibleSegments.map((segment, index) => {
            const slice = (segment.value / total) * circumference
            const dashOffset = -offset
            offset += slice
            return (
              <circle
                key={segment.id}
                className="overview-sleeve-donut-slice"
                cx="60"
                cy="60"
                r={radius}
                stroke={DONUT_COLORS[index % DONUT_COLORS.length]}
                strokeDasharray={`${slice} ${circumference - slice}`}
                strokeDashoffset={dashOffset}
              />
            )
          })}
        </svg>
        <div className="overview-sleeve-donut-center">
          <strong>{formatPercent(total)}</strong>
          <span>Allocated</span>
        </div>
      </div>
      <div className="overview-sleeve-donut-list">
        {visibleSegments.map((segment, index) => (
          <div className="overview-sleeve-donut-row" key={segment.id}>
            <span style={{ background: DONUT_COLORS[index % DONUT_COLORS.length] }} />
            <div>
              <strong>{segment.label}</strong>
              <small>{segment.detail}</small>
            </div>
            <em>{segment.valueLabel}</em>
          </div>
        ))}
      </div>
    </div>
  )
}

function isRecordActive(effectiveFrom?: string | null, effectiveTo?: string | null, referenceDate?: string | null) {
  if (!referenceDate) {
    return true
  }
  if (effectiveFrom && effectiveFrom > referenceDate) {
    return false
  }
  if (effectiveTo && effectiveTo < referenceDate) {
    return false
  }
  return true
}

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

function resolveNodePath(
  taxonomyNodeId: string,
  nodesById: Map<string, PortfolioTaxonomyNodeRecord>,
) {
  const path: PortfolioTaxonomyNodeRecord[] = []
  let cursor: PortfolioTaxonomyNodeRecord | undefined = nodesById.get(taxonomyNodeId)
  const seen = new Set<string>()
  while (cursor && !seen.has(cursor.taxonomy_node_id)) {
    path.unshift(cursor)
    seen.add(cursor.taxonomy_node_id)
    cursor = cursor.parent_taxonomy_node_id ? nodesById.get(cursor.parent_taxonomy_node_id) : undefined
  }
  return path
}

function accumulateBucket(
  buckets: Map<string, AllocationBucket>,
  args: {
    id: string
    label: string
    topLevelId: string
    topLevelLabel: string
    value: number
    weight: number
  },
) {
  const current = buckets.get(args.id)
  if (current) {
    current.value += args.value
    current.weight += args.weight
    current.holdingsCount += 1
    return
  }
  buckets.set(args.id, {
    id: args.id,
    label: args.label,
    topLevelId: args.topLevelId,
    topLevelLabel: args.topLevelLabel,
    value: args.value,
    weight: args.weight,
    holdingsCount: 1,
  })
}

export default function OverviewPage() {
  const { portfolioId = '' } = useParams()
  const [summary, setSummary] = useState<PortfolioWorkspaceSummary | null>(null)
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [performanceWorkspace, setPerformanceWorkspace] = useState<PortfolioPerformanceResponse | null>(null)
  const [performanceLoading, setPerformanceLoading] = useState(false)
  const [performanceError, setPerformanceError] = useState<string | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkSearchFocused, setBenchmarkSearchFocused] = useState(false)
  const [benchmarkAssetId, setBenchmarkAssetId] = useState('')
  const [benchmarkChart, setBenchmarkChart] = useState<PortfolioAssetPriceChartResponse | null>(null)
  const [benchmarkLoading, setBenchmarkLoading] = useState(false)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const [topHoldingColumns, setTopHoldingColumns] = useState<TopHoldingColumnKey[]>(DEFAULT_TOP_HOLDING_COLUMNS)
  const [topHoldingColumnDraft, setTopHoldingColumnDraft] = useState<TopHoldingColumnKey[]>(DEFAULT_TOP_HOLDING_COLUMNS)
  const [topHoldingColumnsOpen, setTopHoldingColumnsOpen] = useState(false)

  useEffect(() => {
    if (!portfolioId) {
      setSummary(null)
      setHoldingsWorkspace(null)
      setTaxonomyCatalog(null)
      setWorkspaceLoading(false)
      setWorkspaceError('Portfolio id is required.')
      return
    }

    let cancelled = false
    setWorkspaceLoading(true)

    Promise.all([
      getWorkspaceSummaryForPortfolio(portfolioId),
      getHoldingsWorkspace(portfolioId),
      getPortfolioTaxonomyCatalog(portfolioId),
    ])
      .then(([summaryResponse, holdingsResponse, taxonomyResponse]) => {
        if (cancelled) {
          return
        }
        setSummary(summaryResponse)
        setHoldingsWorkspace(holdingsResponse)
        setTaxonomyCatalog(taxonomyResponse)
        setWorkspaceError(null)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setWorkspaceError(requestError instanceof Error ? requestError.message : 'Failed to load portfolio overview.')
          setSummary(null)
          setHoldingsWorkspace(null)
          setTaxonomyCatalog(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setWorkspaceLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setPerformanceWorkspace(null)
      setPerformanceLoading(false)
      setPerformanceError(null)
      return
    }

    let cancelled = false
    setPerformanceLoading(true)

    getPortfolioPerformance(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setPerformanceWorkspace(response)
          setPerformanceError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPerformanceError(requestError instanceof Error ? requestError.message : 'Failed to load NAV history.')
          setPerformanceWorkspace(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPerformanceLoading(false)
        }
      })

    return () => {
      cancelled = true
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
    const asOfDate = summary?.as_of_date ?? holdingsWorkspace?.as_of_date
    if (!portfolioId || !benchmarkAssetId || !asOfDate) {
      setBenchmarkChart(null)
      setBenchmarkLoading(false)
      setBenchmarkError(null)
      return
    }

    let cancelled = false
    setBenchmarkLoading(true)
    setBenchmarkError(null)

    getPortfolioAssetPriceChart(portfolioId, benchmarkAssetId, {
      as_of_date: asOfDate,
      range: 'all',
    })
      .then((response) => {
        if (!cancelled) {
          setBenchmarkChart(response)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setBenchmarkChart(null)
          setBenchmarkError(requestError instanceof Error ? requestError.message : 'Failed to load benchmark history.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBenchmarkLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [benchmarkAssetId, holdingsWorkspace?.as_of_date, portfolioId, summary?.as_of_date])

  const holdingsRows = holdingsWorkspace?.rows ?? []
  const deferredBenchmarkSearch = useDeferredValue(benchmarkSearch)
  const resolvedBaseCurrency =
    summary?.base_currency ?? holdingsWorkspace?.base_currency ?? performanceWorkspace?.base_currency ?? 'USD'
  const sortedHoldings = useMemo(
    () =>
      [...holdingsRows].sort(
        (left, right) =>
          (right.allocation ?? 0) - (left.allocation ?? 0) ||
          (right.market_value_base ?? right.market_value ?? 0) - (left.market_value_base ?? left.market_value ?? 0),
      ),
    [holdingsRows],
  )
  const holdingsMarketValueBase = useMemo(
    () =>
      holdingsRows.reduce(
        (sum, row) => sum + (row.market_value_base ?? row.market_value ?? 0),
        0,
      ),
    [holdingsRows],
  )
  const cashValueRaw = (summary?.nav ?? 0) - holdingsMarketValueBase
  const cashValue = Math.abs(cashValueRaw) < 1 ? 0 : cashValueRaw
  const cashWeight = summary?.nav ? cashValue / summary.nav : null
  const top5Weight = sortedHoldings.slice(0, 5).reduce((sum, row) => sum + (row.allocation ?? 0), 0)
  const navChartPoints = useMemo(
    () =>
      (performanceWorkspace?.daily_series ?? [])
        .filter((point) => point.ending_nav != null)
        .map((point) => ({
          date: point.as_of_date,
          value: point.ending_nav as number,
        })),
    [performanceWorkspace],
  )

  const defaultPlanningTaxonomyId =
    summary?.default_planning_taxonomy_id ?? taxonomyCatalog?.default_planning_taxonomy_id ?? null

  const composition = useMemo(() => {
    const nodesById = new Map<string, PortfolioTaxonomyNodeRecord>(
      (taxonomyCatalog?.taxonomy_nodes ?? []).map((node) => [node.taxonomy_node_id, node]),
    )
    const activeAssignments = (taxonomyCatalog?.taxonomy_assignments ?? []).filter(
      (assignment) =>
        assignment.taxonomy_id === defaultPlanningTaxonomyId &&
        assignment.target_scope === 'instrument' &&
        assignment.status === 'active' &&
        isRecordActive(assignment.effective_from, assignment.effective_to, holdingsWorkspace?.as_of_date ?? summary?.as_of_date),
    )
    const assignmentByAssetId = new Map<string, PortfolioTaxonomyAssignmentRecord>(
      activeAssignments.map((assignment) => [assignment.target_entity_id, assignment]),
    )

    const topLevelBuckets = new Map<string, AllocationBucket>()
    const assignedLabelByAssetId = new Map<
      string,
      {
        topLevelLabel: string
        leafLabel: string
      }
    >()

    holdingsRows.forEach((row) => {
      const value = row.market_value_base ?? row.market_value ?? 0
      const weight = row.allocation ?? ((summary?.nav ?? 0) > 0 ? value / (summary?.nav ?? 1) : 0)
      const assignment = assignmentByAssetId.get(row.asset_core.asset_id)
      const leafNode = assignment ? nodesById.get(assignment.taxonomy_node_id) ?? null : null
      const path = leafNode ? resolveNodePath(leafNode.taxonomy_node_id, nodesById) : []
      const topLevelNode = path[0] ?? leafNode
      const topLevelId = topLevelNode?.taxonomy_node_id ?? '__unassigned__'
      const topLevelLabel = topLevelNode?.node_name ?? 'Unassigned'
      const leafLabel = leafNode?.node_name ?? 'Unassigned'

      accumulateBucket(topLevelBuckets, {
        id: topLevelId,
        label: topLevelLabel,
        topLevelId,
        topLevelLabel,
        value,
        weight,
      })

      assignedLabelByAssetId.set(row.asset_core.asset_id, {
        topLevelLabel,
        leafLabel,
      })
    })

    const cashNode =
      (taxonomyCatalog?.taxonomy_assignments ?? []).find(
        (assignment) =>
          assignment.taxonomy_id === defaultPlanningTaxonomyId &&
          assignment.target_scope === 'cash_bucket' &&
          assignment.status === 'active' &&
          isRecordActive(assignment.effective_from, assignment.effective_to, holdingsWorkspace?.as_of_date ?? summary?.as_of_date),
      ) ?? null

    const cashLeafNode = cashNode ? nodesById.get(cashNode.taxonomy_node_id) ?? null : null
    const cashPath = cashLeafNode ? resolveNodePath(cashLeafNode.taxonomy_node_id, nodesById) : []
    const cashTopLevelNode = cashPath[0] ?? cashLeafNode

    if (cashValue > 0) {
      const topLevelId = cashTopLevelNode?.taxonomy_node_id ?? '__cash__'
      const topLevelLabel = cashTopLevelNode?.node_name ?? '现金'

      accumulateBucket(topLevelBuckets, {
        id: topLevelId,
        label: topLevelLabel,
        topLevelId,
        topLevelLabel,
        value: cashValue,
        weight: cashWeight ?? 0,
      })
    }

    const sortedTopLevelBuckets = [...topLevelBuckets.values()].sort(
      (left, right) => right.weight - left.weight || right.value - left.value,
    )

    return {
      topLevelBuckets: sortedTopLevelBuckets,
      assignedLabelByAssetId,
    }
  }, [
    cashValue,
    cashWeight,
    defaultPlanningTaxonomyId,
    holdingsRows,
    holdingsWorkspace?.as_of_date,
    summary?.as_of_date,
    summary?.nav,
    taxonomyCatalog,
  ])

  const sleeveRibbonSegments = composition.topLevelBuckets.map((bucket) => ({
    id: bucket.id,
    label: bucket.label,
    value: bucket.weight,
    valueLabel: formatPercent(bucket.weight),
    detail: `${bucket.holdingsCount} lines · ${formatCurrency(bucket.value, resolvedBaseCurrency)}`,
  }))
  const topHoldingBarItems = sortedHoldings.slice(0, 8).map((row) => ({
    id: row.line_id,
    label: row.asset_core.asset_name,
    subtitle: composition.assignedLabelByAssetId.get(row.asset_core.asset_id)?.leafLabel ?? formatLabel(row.asset_core.asset_type),
    value: row.allocation ?? 0,
    valueLabel: formatPercent(row.allocation),
    detail: formatCurrency(row.market_value_base ?? row.market_value, resolvedBaseCurrency),
  }))
  const selectedBenchmarkInstrument =
    benchmarkInstruments.find((instrument) => instrument.asset_id === benchmarkAssetId) ?? null
  const selectedBenchmarkLabel = selectedBenchmarkInstrument
    ? benchmarkInstrumentLabel(selectedBenchmarkInstrument)
    : ''
  const benchmarkInputValue =
    selectedBenchmarkInstrument && !benchmarkSearch ? selectedBenchmarkLabel : benchmarkSearch
  const filteredBenchmarkOptions = useMemo(() => {
    const normalizedSearch = deferredBenchmarkSearch.trim().toLowerCase()
    if (!normalizedSearch) {
      return []
    }

    return benchmarkInstruments
      .filter((instrument) => {
        const haystack = [
          instrument.asset_name,
          instrument.asset_type,
          instrument.currency,
          instrumentPrimaryIdentifier(instrument),
          benchmarkInstrumentLabel(instrument),
          ...instrument.identifiers.map((identifier) => identifier.identifier_value),
        ]
          .join(' ')
          .toLowerCase()
        return haystack.includes(normalizedSearch)
      })
      .slice(0, 10)
  }, [benchmarkInstruments, deferredBenchmarkSearch])
  const showBenchmarkResults =
    benchmarkSearchFocused &&
    benchmarkInputValue.trim() !== '' &&
    (!selectedBenchmarkInstrument || benchmarkInputValue !== selectedBenchmarkLabel)
  const benchmarkMetrics = useMemo(
    () => buildBenchmarkMetrics(benchmarkChart?.points ?? []) ?? null,
    [benchmarkChart],
  )
  const portfolioReturnMetrics = useMemo(
    () => buildPortfolioReturnMetrics(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )

  const overviewMetricGroups: Array<{ label: string; rows: OverviewMetricRow[] }> = [
    {
      label: 'Performance',
      rows: [
        {
          label: '1W Return',
          value: signedPercent(portfolioReturnMetrics.oneWeek),
          benchmark: benchmarkNote(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.oneWeek),
          emphasis: true,
          toneClassName: signedValueClass(portfolioReturnMetrics.oneWeek),
        },
        {
          label: 'MTD',
          value: signedPercent(portfolioReturnMetrics.mtd),
          benchmark: benchmarkNote(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.mtd),
          emphasis: true,
          toneClassName: signedValueClass(portfolioReturnMetrics.mtd),
        },
        {
          label: 'YTD',
          value: signedPercent(portfolioReturnMetrics.ytd),
          benchmark: benchmarkNote(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.ytd),
          emphasis: true,
          toneClassName: signedValueClass(portfolioReturnMetrics.ytd),
        },
        {
          label: 'Since Inception',
          value: signedPercent(performanceWorkspace?.summary.cumulative_ttwror),
          benchmark: benchmarkNote(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.sinceInception),
          emphasis: true,
          toneClassName: signedValueClass(performanceWorkspace?.summary.cumulative_ttwror),
        },
      ],
    },
    {
      label: 'Risk Watch',
      rows: [
        {
          label: 'Current DD',
          value: signedPercent(performanceWorkspace?.summary.current_drawdown),
          benchmark: benchmarkNote(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.currentDrawdown),
          toneClassName: signedValueClass(performanceWorkspace?.summary.current_drawdown),
        },
        {
          label: 'Max DD',
          value: signedPercent(performanceWorkspace?.summary.max_drawdown),
          benchmark: benchmarkNote(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.maxDrawdown),
          toneClassName: signedValueClass(performanceWorkspace?.summary.max_drawdown),
        },
      ],
    },
    {
      label: 'Portfolio',
      rows: [
        { label: 'Total NAV', value: formatCurrency(summary?.nav, resolvedBaseCurrency), emphasis: true },
        {
          label: 'Total P&L',
          value: formatSignedCurrency(performanceWorkspace?.summary.total_pnl, resolvedBaseCurrency),
          emphasis: true,
          toneClassName: signedValueClass(performanceWorkspace?.summary.total_pnl),
        },
        { label: 'Cash Weight', value: formatPercent(cashWeight) },
        { label: 'Top 5 Weight', value: formatPercent(top5Weight) },
      ],
    },
  ]
  const topHoldingColumnDefinitions: TopHoldingColumnDefinition[] = [
    {
      key: 'asset',
      label: TOP_HOLDING_COLUMN_LABELS.asset,
      render: (row) => row.asset_core.asset_name,
    },
    {
      key: 'identifier',
      label: TOP_HOLDING_COLUMN_LABELS.identifier,
      render: (row) => primaryIdentifier(row),
    },
    {
      key: 'sleeve',
      label: TOP_HOLDING_COLUMN_LABELS.sleeve,
      render: (row) => composition.assignedLabelByAssetId.get(row.asset_core.asset_id)?.leafLabel ?? 'Unassigned',
    },
    {
      key: 'sparkline',
      label: TOP_HOLDING_COLUMN_LABELS.sparkline,
      render: (row) => <MiniSparkline values={row.price_chart} />,
    },
    {
      key: 'quantity',
      label: TOP_HOLDING_COLUMN_LABELS.quantity,
      render: (row) => formatQuantity(row.quantity),
    },
    {
      key: 'last_price',
      label: TOP_HOLDING_COLUMN_LABELS.last_price,
      render: (row) => formatUnitPrice(row.last_price, row.asset_core.currency),
    },
    {
      key: 'market_value',
      label: TOP_HOLDING_COLUMN_LABELS.market_value,
      render: (row) => formatCurrency(row.market_value_base ?? row.market_value, resolvedBaseCurrency),
    },
    {
      key: 'cost_basis',
      label: TOP_HOLDING_COLUMN_LABELS.cost_basis,
      render: (row) => formatCurrency(row.cost_basis_base ?? row.cost_basis, resolvedBaseCurrency),
    },
    {
      key: 'unrealized_pnl',
      label: TOP_HOLDING_COLUMN_LABELS.unrealized_pnl,
      render: (row) => {
        const unrealized =
          row.market_value_base != null && row.cost_basis_base != null
            ? row.market_value_base - row.cost_basis_base
            : null
        return (
          <span className={signedValueClass(unrealized)}>
            {formatSignedCurrency(unrealized, resolvedBaseCurrency)}
          </span>
        )
      },
    },
    {
      key: 'day_change',
      label: TOP_HOLDING_COLUMN_LABELS.day_change,
      render: (row) => (
        <span className={signedValueClass(row.day_change_pct)}>
          {formatSignedCurrency(row.day_change_value, resolvedBaseCurrency)} ({signedPercent(row.day_change_pct)})
        </span>
      ),
    },
    {
      key: 'weight',
      label: TOP_HOLDING_COLUMN_LABELS.weight,
      render: (row) => formatPercent(row.allocation),
    },
    {
      key: 'coverage',
      label: TOP_HOLDING_COLUMN_LABELS.coverage,
      render: (row) => (
        <span className={`coverage-pill ${row.last_price != null ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
          {row.coverage_status}
        </span>
      ),
    },
  ]
  const topHoldingColumnDefinitionByKey = new Map(
    topHoldingColumnDefinitions.map((definition) => [definition.key, definition]),
  )
  const visibleTopHoldingColumns = topHoldingColumns
    .map((column) => topHoldingColumnDefinitionByKey.get(column))
    .filter((definition): definition is TopHoldingColumnDefinition => Boolean(definition))

  return (
    <PortfolioWorkspaceLayout activeSection="Overview" toolbarLabel="View: Portfolio Overview">
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Overview</div>
        </div>

        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}

        {workspaceLoading ? <CalculationStatus label="Loading portfolio summary, holdings, and strategy sleeves…" /> : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No overview workspace is available for this portfolio.</div>
        ) : null}

        {!workspaceLoading && holdingsWorkspace ? (
          <>
            <div className="performance-block-grid">
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Portfolio NAV</div>
                  <div className="overview-chart-controls">
                    <label className="overview-benchmark-search">
                      <span>Benchmark</span>
                      <div className="overview-benchmark-search-box">
                        <input
                          type="search"
                          placeholder="Search database benchmark"
                          value={benchmarkInputValue}
                          onFocus={() => setBenchmarkSearchFocused(true)}
                          onBlur={() => window.setTimeout(() => setBenchmarkSearchFocused(false), 140)}
                          onChange={(event) => {
                            const nextValue = event.target.value
                            setBenchmarkSearch(nextValue)
                            if (selectedBenchmarkInstrument && nextValue !== selectedBenchmarkLabel) {
                              setBenchmarkAssetId('')
                              setBenchmarkChart(null)
                            }
                          }}
                        />
                        {selectedBenchmarkInstrument ? (
                          <button
                            type="button"
                            aria-label="Clear benchmark"
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={() => {
                              setBenchmarkAssetId('')
                              setBenchmarkSearch('')
                              setBenchmarkChart(null)
                              setBenchmarkError(null)
                            }}
                          >
                            Clear
                          </button>
                        ) : null}
                        {showBenchmarkResults ? (
                          <div className="overview-benchmark-results">
                            {filteredBenchmarkOptions.length ? (
                              filteredBenchmarkOptions.map((instrument) => (
                                <button
                                  type="button"
                                  key={instrument.asset_id}
                                  onMouseDown={(event) => event.preventDefault()}
                                  onClick={() => {
                                    setBenchmarkAssetId(instrument.asset_id)
                                    setBenchmarkSearch(benchmarkInstrumentLabel(instrument))
                                    setBenchmarkSearchFocused(false)
                                  }}
                                >
                                  <strong>{instrument.asset_name}</strong>
                                  <span>
                                    {instrumentPrimaryIdentifier(instrument)} · {formatLabel(instrument.asset_type)} · {instrument.currency}
                                  </span>
                                </button>
                              ))
                            ) : (
                              <div className="overview-benchmark-empty">No database match</div>
                            )}
                          </div>
                        ) : null}
                      </div>
                    </label>
                  </div>
                </div>
                <div className="overview-nav-grid">
                  <div className="overview-nav-chart-panel">
                    {performanceLoading && !performanceWorkspace ? (
                      <CalculationStatus label="Building NAV path and drawdown summary…" />
                    ) : null}
                    {!performanceLoading && performanceWorkspace ? (
                      <PerformanceNavChart
                        points={navChartPoints}
                        currency={resolvedBaseCurrency}
                        showRangeControls={false}
                      />
                    ) : null}
                  </div>

                  <aside className="overview-key-metrics" aria-label="Portfolio overview key metrics">
                    <div className="overview-key-metrics-header">
                      <div>
                        <span>Key Metrics</span>
                        <strong>{formatCurrency(summary?.nav, resolvedBaseCurrency)}</strong>
                      </div>
                      <span>{selectedBenchmarkInstrument ? `BM ${instrumentPrimaryIdentifier(selectedBenchmarkInstrument)}` : 'No benchmark'}</span>
                    </div>
                    {benchmarkError ? <div className="overview-benchmark-error">{benchmarkError}</div> : null}
                    <div className="overview-key-metric-groups">
                      {overviewMetricGroups.map((group) => (
                        <table className="overview-key-metrics-table" key={group.label}>
                          <thead>
                            <tr>
                              <th colSpan={2}>{group.label}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {group.rows.map((row) => (
                              <tr key={`${group.label}:${row.label}`}>
                                <th>{row.label}</th>
                                <td
                                  className={[
                                    row.emphasis ? 'overview-key-metric-emphasis' : '',
                                    row.toneClassName ?? '',
                                  ]
                                    .filter(Boolean)
                                    .join(' ') || undefined}
                                >
                                  <strong>{row.value}</strong>
                                  {'benchmark' in row && row.benchmark ? <small>{row.benchmark}</small> : null}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ))}
                    </div>
                  </aside>
                </div>
              </section>

              <div className="overview-panel-grid">
                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Strategy Sleeves</div>
                  </div>
                  <StrategySleeveDonut segments={sleeveRibbonSegments} />
                </section>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Top Holdings</div>
                  </div>
                  <RiskRankedBars
                    items={topHoldingBarItems}
                    ariaLabel="Top holdings ranked by current weight"
                    emptyLabel="No holdings are available."
                  />
                </section>
              </div>

              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Top Holdings Detail</div>
                  <button
                    type="button"
                    className="overview-columns-button"
                    onClick={() => {
                      setTopHoldingColumnDraft(topHoldingColumns)
                      setTopHoldingColumnsOpen(true)
                    }}
                  >
                    Data Columns
                  </button>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        {visibleTopHoldingColumns.map((column) => (
                          <th key={column.key}>{column.label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedHoldings.length ? (
                        sortedHoldings.slice(0, 12).map((row) => (
                          <tr key={row.line_id}>
                            {visibleTopHoldingColumns.map((column) => (
                              <td key={column.key}>{column.render(row)}</td>
                            ))}
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow
                          colSpan={Math.max(1, visibleTopHoldingColumns.length)}
                          label="No holdings are available for this portfolio."
                        />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          </>
        ) : null}

        {topHoldingColumnsOpen ? (
          <div className="overview-columns-modal-backdrop" onClick={() => setTopHoldingColumnsOpen(false)}>
            <div className="overview-columns-modal" onClick={(event) => event.stopPropagation()}>
              <div className="overview-columns-modal-header">
                <div>
                  <div className="panel-title">Data Columns</div>
                  <div className="portfolio-detail-meta">Configure Top Holdings Detail</div>
                </div>
                <button type="button" onClick={() => setTopHoldingColumnsOpen(false)}>
                  Close
                </button>
              </div>

              <div className="overview-columns-modal-body">
                <div className="overview-columns-field-groups">
                  {TOP_HOLDING_COLUMN_GROUPS.map((group) => (
                    <section className="overview-columns-field-group" key={group.label}>
                      <div className="section-heading">{group.label}</div>
                      {group.columns.map((column) => {
                        const locked = column === 'asset'
                        return (
                          <label className="overview-columns-field-item" key={column}>
                            <input
                              type="checkbox"
                              checked={topHoldingColumnDraft.includes(column)}
                              disabled={locked}
                              onChange={(event) =>
                                setTopHoldingColumnDraft((current) =>
                                  event.target.checked
                                    ? [...current, column]
                                    : current.filter((item) => item !== column),
                                )
                              }
                            />
                            <span>{TOP_HOLDING_COLUMN_LABELS[column]}</span>
                          </label>
                        )
                      })}
                    </section>
                  ))}
                </div>

                <div className="overview-columns-arrange">
                  <div className="overview-columns-arrange-head">
                    <span>Selected</span>
                    <button type="button" onClick={() => setTopHoldingColumnDraft(DEFAULT_TOP_HOLDING_COLUMNS)}>
                      Reset
                    </button>
                  </div>
                  <div className="overview-columns-arrange-list">
                    {topHoldingColumnDraft.map((column, index) => (
                      <div className="overview-columns-arrange-item" key={column}>
                        <span>{TOP_HOLDING_COLUMN_LABELS[column]}</span>
                        <div>
                          <button
                            type="button"
                            disabled={index === 0}
                            onClick={() =>
                              setTopHoldingColumnDraft((current) => {
                                const next = [...current]
                                const [moved] = next.splice(index, 1)
                                next.splice(index - 1, 0, moved)
                                return next
                              })
                            }
                          >
                            Up
                          </button>
                          <button
                            type="button"
                            disabled={index === topHoldingColumnDraft.length - 1}
                            onClick={() =>
                              setTopHoldingColumnDraft((current) => {
                                const next = [...current]
                                const [moved] = next.splice(index, 1)
                                next.splice(index + 1, 0, moved)
                                return next
                              })
                            }
                          >
                            Down
                          </button>
                          {column !== 'asset' ? (
                            <button
                              type="button"
                              onClick={() => setTopHoldingColumnDraft((current) => current.filter((item) => item !== column))}
                            >
                              Remove
                            </button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="overview-columns-modal-actions">
                <button
                  type="button"
                  onClick={() => {
                    setTopHoldingColumnDraft(topHoldingColumns)
                    setTopHoldingColumnsOpen(false)
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="button-primary"
                  onClick={() => {
                    setTopHoldingColumns(
                      topHoldingColumnDraft.includes('asset')
                        ? topHoldingColumnDraft
                        : ['asset', ...topHoldingColumnDraft],
                    )
                    setTopHoldingColumnsOpen(false)
                  }}
                >
                  Update
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
