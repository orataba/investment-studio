import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import RiskRankedBars from '../components/RiskRankedBars'
import Sparkline from '../../../../../packages/ui/src/Sparkline'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  getHoldingsWorkspace,
  getPortfolioPerformance,
  getPortfolioReturnCalendar,
  getPortfolioTaxonomyCatalog,
  getWorkspaceSummaryForPortfolio,
  type HoldingsWorkspaceResponse,
  type PortfolioHoldingRow,
  type PortfolioPerformanceResponse,
  type PortfolioReturnCalendarResponse,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioWorkspaceSummary,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'
import { buildTwrIndexPoints } from '../lib/performanceSeries'
import { annualizedReturnDisplayEligible } from '../lib/performanceHistoryPresentation'
import {
  buildReturnCalendarMatrixRows,
  RETURN_CALENDAR_MONTH_LABELS,
} from '../lib/returnCalendarPresentation'

type AllocationBucket = {
  id: string
  label: string
  topLevelId: string
  topLevelLabel: string
  value: number
  baseValueComplete: boolean
  weight: number
  holdingsCount: number
}

type TopHoldingColumnKey =
  | 'instrument'
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
  | 'return_1w'
  | 'return_mtd'
  | 'return_ytd'

type TopHoldingColumnDefinition = {
  key: TopHoldingColumnKey
  label: string
  align?: 'center'
  render: (row: PortfolioHoldingRow) => ReactNode
}

type OverviewMetricRow = {
  label: string
  value: string
  emphasis?: boolean
  toneClassName?: string
  title?: string
}

const DEFAULT_TOP_HOLDING_COLUMNS: TopHoldingColumnKey[] = [
  'instrument',
  'sparkline',
  'market_value',
  'weight',
  'return_1w',
  'return_mtd',
  'return_ytd',
]

const TOP_HOLDING_COLUMN_LABELS: Record<TopHoldingColumnKey, string> = {
  instrument: 'Instrument',
  identifier: 'Identifier',
  sleeve: 'Sleeve',
  sparkline: 'Chart 6M',
  quantity: 'Quantity',
  last_price: 'Last Price',
  market_value: 'Market Value',
  cost_basis: 'Cost Basis',
  unrealized_pnl: 'Unrealized P&L',
  day_change: 'Day Change',
  weight: 'Weight',
  coverage: 'Coverage',
  return_1w: '1W Return',
  return_mtd: 'MTD',
  return_ytd: 'YTD',
}

const TOP_HOLDING_COLUMN_GROUPS: Array<{ label: string; columns: TopHoldingColumnKey[] }> = [
  { label: 'Core', columns: ['instrument', 'identifier', 'sleeve', 'coverage'] },
  { label: 'Market', columns: ['sparkline', 'last_price', 'market_value', 'weight', 'day_change'] },
  { label: 'Position', columns: ['quantity', 'cost_basis'] },
  { label: 'Return', columns: ['return_1w', 'return_mtd', 'return_ytd'] },
]

const TOP_HOLDINGS_LIMIT = 10

const DONUT_COLORS = ['#0b72d7', '#0f766e', '#64748b', '#7c3aed', '#db2777', '#14b8a6', '#475569']

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.instrument_core.identifiers[0]?.identifier_value ??
    row.instrument_core.instrument_id
  )
}

function isCashHoldingRow(row: PortfolioHoldingRow) {
  return (
    row.instrument_core.instrument_type === 'cash' ||
    row.instrument_core.instrument_id.toLowerCase().startsWith('cash:') ||
    row.line_id.toLowerCase().startsWith('cash:')
  )
}

function signedPercent(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(value)) {
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

function StrategySleeveDonut({
  segments,
}: {
  segments: Array<{ id: string; label: string; value: number; valueLabel: string; detail: string }>
}) {
  const visibleSegments = segments.filter((segment) => segment.value > 0)
  const total = visibleSegments.reduce((sum, segment) => sum + segment.value, 0)
  if (!visibleSegments.length || total <= 0) {
    return <div className="price-chart-empty">No sleeves.</div>
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
            </div>
            <em>{segment.valueLabel}</em>
          </div>
        ))}
      </div>
    </div>
  )
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
    value: number | null
    weight: number
  },
) {
  const current = buckets.get(args.id)
  if (current) {
    if (args.value == null) {
      current.baseValueComplete = false
    } else {
      current.value += args.value
    }
    current.weight += args.weight
    current.holdingsCount += 1
    return
  }
  buckets.set(args.id, {
    id: args.id,
    label: args.label,
    topLevelId: args.topLevelId,
    topLevelLabel: args.topLevelLabel,
    value: args.value ?? 0,
    baseValueComplete: args.value != null,
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
  const [returnCalendar, setReturnCalendar] = useState<PortfolioReturnCalendarResponse | null>(null)
  const [performanceLoading, setPerformanceLoading] = useState(false)
  const [performanceError, setPerformanceError] = useState<string | null>(null)
  const [topHoldingColumns, setTopHoldingColumns] = useState<TopHoldingColumnKey[]>(DEFAULT_TOP_HOLDING_COLUMNS)
  const [topHoldingColumnDraft, setTopHoldingColumnDraft] = useState<TopHoldingColumnKey[]>(DEFAULT_TOP_HOLDING_COLUMNS)
  const [topHoldingColumnsOpen, setTopHoldingColumnsOpen] = useState(false)
  const topHoldingColumnsDialogRef = useModalDialog(
    topHoldingColumnsOpen,
    () => setTopHoldingColumnsOpen(false),
  )

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
    const asOfDate = holdingsWorkspace?.as_of_date ?? summary?.as_of_date
    if (!portfolioId || !asOfDate) {
      setPerformanceWorkspace(null)
      setReturnCalendar(null)
      setPerformanceLoading(false)
      setPerformanceError(null)
      return
    }

    let cancelled = false
    setPerformanceWorkspace(null)
    setReturnCalendar(null)
    setPerformanceError(null)
    setPerformanceLoading(true)

    Promise.all([
      getPortfolioPerformance(portfolioId, { end_date: asOfDate }),
      getPortfolioReturnCalendar(portfolioId, { end_date: asOfDate, frequency: 'monthly' }),
    ])
      .then(([performanceResponse, calendarResponse]) => {
        if (!cancelled) {
          setPerformanceWorkspace(performanceResponse)
          setReturnCalendar(calendarResponse)
          setPerformanceError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPerformanceError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load authoritative performance and return calendar.',
          )
          setPerformanceWorkspace(null)
          setReturnCalendar(null)
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
  }, [holdingsWorkspace?.as_of_date, portfolioId, summary?.as_of_date])

  const holdingsRows = holdingsWorkspace?.rows ?? []
  const nonCashHoldingsRows = useMemo(() => holdingsRows.filter((row) => !isCashHoldingRow(row)), [holdingsRows])
  const resolvedBaseCurrency =
    summary?.base_currency ?? holdingsWorkspace?.base_currency ?? performanceWorkspace?.base_currency ?? ''
  const sortedHoldings = useMemo(
    () =>
      [...nonCashHoldingsRows].sort(
        (left, right) =>
          (right.allocation ?? 0) - (left.allocation ?? 0) ||
          (right.market_value_base ?? 0) - (left.market_value_base ?? 0),
      ),
    [nonCashHoldingsRows],
  )
  const navChartPoints = useMemo(
    () =>
      (performanceWorkspace?.daily_series ?? []).map((point) => ({
        date: point.as_of_date,
        value: point.ending_nav,
      })),
    [performanceWorkspace],
  )
  const twrIndexChartPoints = useMemo(
    () => buildTwrIndexPoints(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )
  const drawdownChartPoints = useMemo(
    () =>
      (performanceWorkspace?.daily_series ?? []).map((point) => ({
        date: point.as_of_date,
        value: point.drawdown,
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
        assignment.status === 'active',
    )
    const assignmentByInstrumentId = new Map<string, PortfolioTaxonomyAssignmentRecord>(
      activeAssignments.map((assignment) => [assignment.target_entity_id, assignment]),
    )

    const topLevelBuckets = new Map<string, AllocationBucket>()
    const assignedLabelByInstrumentId = new Map<
      string,
      {
        topLevelLabel: string
        leafLabel: string
      }
    >()

    nonCashHoldingsRows.forEach((row) => {
      const value = row.market_value_base ?? null
      const weight = row.allocation ?? 0
      const assignment = assignmentByInstrumentId.get(row.instrument_core.instrument_id)
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

      assignedLabelByInstrumentId.set(row.instrument_core.instrument_id, {
        topLevelLabel,
        leafLabel,
      })
    })

    const sortedTopLevelBuckets = [...topLevelBuckets.values()].sort(
      (left, right) => right.weight - left.weight || right.value - left.value,
    )

    return {
      topLevelBuckets: sortedTopLevelBuckets,
      assignedLabelByInstrumentId,
    }
  }, [
    defaultPlanningTaxonomyId,
    nonCashHoldingsRows,
    holdingsWorkspace?.as_of_date,
    summary?.as_of_date,
    taxonomyCatalog,
  ])

  const sleeveRibbonSegments = composition.topLevelBuckets.map((bucket) => ({
    id: bucket.id,
    label: bucket.label,
    value: bucket.weight,
    valueLabel: formatPercent(bucket.weight),
    detail: bucket.baseValueComplete
      ? `${bucket.holdingsCount} lines · ${formatCurrency(bucket.value, resolvedBaseCurrency)}`
      : `${bucket.holdingsCount} lines · base value unavailable`,
  }))
  const topHoldingBarItems = sortedHoldings.slice(0, TOP_HOLDINGS_LIMIT).map((row) => ({
    id: row.line_id,
    label: row.instrument_core.instrument_name,
    subtitle: composition.assignedLabelByInstrumentId.get(row.instrument_core.instrument_id)?.leafLabel ?? formatLabel(row.instrument_core.instrument_type),
    value: row.allocation ?? 0,
    valueLabel: formatPercent(row.allocation),
    detail: formatCurrency(row.market_value_base, resolvedBaseCurrency),
  }))
  const monthlyMatrixRows = useMemo(
    () => buildReturnCalendarMatrixRows(returnCalendar?.buckets ?? []),
    [returnCalendar],
  )
  const overviewPerformanceHistoryReliability =
    performanceWorkspace?.summary.history_reliability ?? null
  const overviewAnnualizedReturnEligible = annualizedReturnDisplayEligible(
    overviewPerformanceHistoryReliability,
  )
  const overviewAnnualizationMessage =
    overviewPerformanceHistoryReliability?.annualization_message ??
    'Annualized metrics are unavailable for this history window.'

  const overviewMetricGroups: Array<{ label: string; rows: OverviewMetricRow[] }> = [
    {
      label: 'Performance',
      rows: [
        {
          label: 'Reported Period TWR',
          value: signedPercent(performanceWorkspace?.summary.cumulative_twr),
          emphasis: true,
          toneClassName: signedValueClass(performanceWorkspace?.summary.cumulative_twr),
        },
        {
          label: 'Annualized TWR',
          value: overviewAnnualizedReturnEligible
            ? signedPercent(performanceWorkspace?.summary.annualized_twr)
            : 'N/A',
          emphasis: true,
          toneClassName: overviewAnnualizedReturnEligible
            ? signedValueClass(performanceWorkspace?.summary.annualized_twr)
            : undefined,
          title: overviewAnnualizedReturnEligible
            ? undefined
            : overviewAnnualizationMessage,
        },
        {
          label: 'IRR / MWRR',
          value: overviewAnnualizedReturnEligible
            ? `${signedPercent(performanceWorkspace?.summary.irr)} / ${signedPercent(performanceWorkspace?.summary.mwror)}`
            : 'N/A',
          title: overviewAnnualizedReturnEligible
            ? undefined
            : overviewAnnualizationMessage,
        },
        {
          label: 'TWR Reliability',
          value: formatLabel(performanceWorkspace?.summary.twr_reliability_status ?? 'unavailable'),
          title: performanceWorkspace?.summary.twr_reliability_reasons.map(formatLabel).join(', ') || undefined,
        },
      ],
    },
    {
      label: 'Risk Watch',
      rows: [
        {
          label: 'Current DD',
          value: signedPercent(performanceWorkspace?.summary.current_drawdown),
          toneClassName: signedValueClass(performanceWorkspace?.summary.current_drawdown),
        },
        {
          label: 'Max DD',
          value: signedPercent(performanceWorkspace?.summary.max_drawdown),
          toneClassName: signedValueClass(performanceWorkspace?.summary.max_drawdown),
        },
        {
          label: 'Annualized VOL',
          value: formatPercent(performanceWorkspace?.summary.annualized_volatility),
          title: 'Backend reported-period volatility.',
        },
        {
          label: 'Downside VOL',
          value: formatPercent(performanceWorkspace?.summary.annualized_downside_volatility),
          title: 'Backend reported-period downside volatility.',
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
        { label: 'Base Currency', value: resolvedBaseCurrency || '—' },
        { label: 'Position Lines', value: formatNumber(nonCashHoldingsRows.length, 0) },
      ],
    },
  ]
  const topHoldingColumnDefinitions: TopHoldingColumnDefinition[] = [
    {
      key: 'instrument',
      label: TOP_HOLDING_COLUMN_LABELS.instrument,
      render: (row) => row.instrument_core.instrument_name,
    },
    {
      key: 'identifier',
      label: TOP_HOLDING_COLUMN_LABELS.identifier,
      render: (row) => primaryIdentifier(row),
    },
    {
      key: 'sleeve',
      label: TOP_HOLDING_COLUMN_LABELS.sleeve,
      render: (row) => composition.assignedLabelByInstrumentId.get(row.instrument_core.instrument_id)?.leafLabel ?? 'Unassigned',
    },
    {
      key: 'sparkline',
      label: TOP_HOLDING_COLUMN_LABELS.sparkline,
      align: 'center',
      render: (row) => <Sparkline values={row.price_chart_6m} maxPoints={120} />,
    },
    {
      key: 'quantity',
      label: TOP_HOLDING_COLUMN_LABELS.quantity,
      render: (row) => formatQuantity(row.quantity),
    },
    {
      key: 'last_price',
      label: TOP_HOLDING_COLUMN_LABELS.last_price,
      render: (row) => formatUnitPrice(row.last_price, row.instrument_core.currency),
    },
    {
      key: 'market_value',
      label: TOP_HOLDING_COLUMN_LABELS.market_value,
      render: (row) => formatCurrency(row.market_value_base, resolvedBaseCurrency),
    },
    {
      key: 'cost_basis',
      label: TOP_HOLDING_COLUMN_LABELS.cost_basis,
      render: (row) => formatCurrency(row.cost_basis_base, resolvedBaseCurrency),
    },
    {
      key: 'unrealized_pnl',
      label: TOP_HOLDING_COLUMN_LABELS.unrealized_pnl,
      render: () => <span title="Awaiting backend unrealized P&L projection">—</span>,
    },
    {
      key: 'day_change',
      label: TOP_HOLDING_COLUMN_LABELS.day_change,
      render: (row) => (
        <span className={signedValueClass(row.day_change_pct)}>
          {formatSignedCurrency(row.day_change_value_base, resolvedBaseCurrency)} ({signedPercent(row.day_change_pct)})
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
    {
      key: 'return_1w',
      label: TOP_HOLDING_COLUMN_LABELS.return_1w,
      render: (row) => {
        const value = row.instrument_return_1w ?? null
        return <span className={signedValueClass(value)}>{signedPercent(value)}</span>
      },
    },
    {
      key: 'return_mtd',
      label: TOP_HOLDING_COLUMN_LABELS.return_mtd,
      render: (row) => {
        const value = row.instrument_return_mtd ?? null
        return <span className={signedValueClass(value)}>{signedPercent(value)}</span>
      },
    },
    {
      key: 'return_ytd',
      label: TOP_HOLDING_COLUMN_LABELS.return_ytd,
      render: (row) => {
        const value = row.instrument_return_ytd ?? null
        return <span className={signedValueClass(value)}>{signedPercent(value)}</span>
      },
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
      <section className="portfolio-detail-surface portfolio-overview-surface">
        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}

        {workspaceLoading ? <CalculationStatus /> : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No data.</div>
        ) : null}

        {!workspaceLoading && holdingsWorkspace ? (
          <>
            <div className="performance-block-grid">
              <section className="performance-section-block overview-performance-block">
                <div className="overview-nav-grid">
                  <div className="overview-nav-chart-panel">
                    <div className="overview-chart-controls">
                      <span className="portfolio-detail-meta">
                        Benchmark-relative analytics are withheld until the backend comparison contract is available.
                      </span>
                    </div>
                    {performanceLoading && !performanceWorkspace ? (
                      <CalculationStatus />
                    ) : null}
                    {!performanceLoading && performanceWorkspace ? (
                      <PerformanceNavChart
                        points={navChartPoints}
                        twrPoints={twrIndexChartPoints}
                        drawdownPoints={drawdownChartPoints}
                        summary={performanceWorkspace.summary}
                        currency={resolvedBaseCurrency}
                        showRangeControls
                      />
                    ) : null}
                  </div>

                  <aside className="overview-key-metrics" aria-label="Portfolio overview key metrics">
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
                                <th title={row.title}>{row.label}</th>
                                <td
                                  className={[
                                    row.emphasis ? 'overview-key-metric-emphasis' : '',
                                    row.toneClassName ?? '',
                                  ]
                                    .filter(Boolean)
                                    .join(' ') || undefined}
                                >
                                  <strong>{row.value}</strong>
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

              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                  <div className="panel-title">Monthly Return Matrix</div>
                  <div className="portfolio-detail-meta">
                    Backend calendar · {formatLabel(returnCalendar?.summary.twr_reliability_status ?? 'unavailable')}
                  </div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table performance-return-matrix-table">
                    <thead>
                      <tr>
                        <th>Year</th>
                        {RETURN_CALENDAR_MONTH_LABELS.map((monthLabel) => (
                          <th key={monthLabel}>{monthLabel}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {monthlyMatrixRows.length ? (
                        monthlyMatrixRows.map((row) => (
                          <tr key={row.year}>
                            <th scope="row">
                              <span>{row.year}</span>
                            </th>
                            {row.months.map((bucket, index) => (
                              <td
                                key={`${row.year}:${RETURN_CALENDAR_MONTH_LABELS[index]}`}
                                className={`performance-cell-number performance-return-cell ${signedValueClass(
                                  bucket?.cumulative_twr,
                                )}`}
                                title={
                                  bucket
                                    ? `${bucket.start_date} to ${bucket.end_date}; ${formatLabel(
                                        bucket.nav_coverage_state,
                                      )}; ${formatLabel(bucket.twr_reliability_status)} TWR; ${formatNumber(
                                        bucket.observation_count,
                                        0,
                                      )} observations${
                                        bucket.twr_reliability_reasons.length
                                          ? `; ${bucket.twr_reliability_reasons.map(formatLabel).join(', ')}`
                                          : ''
                                      }`
                                    : undefined
                                }
                              >
                                {signedPercent(bucket?.cumulative_twr)}
                              </td>
                            ))}
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow colSpan={13} label="No authoritative monthly returns." />
                      )}
                    </tbody>
                  </table>
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
                    emptyLabel="No holdings."
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
                          <th key={column.key} className={column.align === 'center' ? 'chart-cell' : undefined}>
                            {column.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedHoldings.length ? (
                        sortedHoldings.slice(0, TOP_HOLDINGS_LIMIT).map((row) => (
                          <tr key={row.line_id}>
                            {visibleTopHoldingColumns.map((column) => (
                              <td key={column.key} className={column.align === 'center' ? 'chart-cell' : undefined}>
                                {column.render(row)}
                              </td>
                            ))}
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow
                          colSpan={Math.max(1, visibleTopHoldingColumns.length)}
                          label="No holdings."
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
            <div
              ref={topHoldingColumnsDialogRef}
              className="overview-columns-modal"
              role="dialog"
              aria-modal="true"
              aria-label="Choose top holdings columns"
              tabIndex={-1}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="overview-columns-modal-header">
                <div>
                  <div className="panel-title">Data Columns</div>
                  <div className="portfolio-detail-meta">Top Holdings Detail</div>
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
                        const locked = column === 'instrument'
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
                          {column !== 'instrument' ? (
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
                      topHoldingColumnDraft.includes('instrument')
                        ? topHoldingColumnDraft
                        : ['instrument', ...topHoldingColumnDraft],
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
