import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import RiskExposureRibbon from '../components/RiskExposureRibbon'
import RiskRankedBars from '../components/RiskRankedBars'
import {
  getHoldingsWorkspace,
  getPortfolioPerformance,
  getPortfolioTaxonomyCatalog,
  getWorkspaceSummaryForPortfolio,
  type HoldingsWorkspaceResponse,
  type PortfolioHoldingRow,
  type PortfolioPerformanceCoverageState,
  type PortfolioPerformanceResponse,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioWorkspaceSummary,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatPercent,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
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

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.asset_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.asset_core.identifiers[0]?.identifier_value ??
    row.asset_core.asset_id
  )
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

  const holdingsRows = holdingsWorkspace?.rows ?? []
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
  const pricedLines = holdingsRows.filter(
    (row) => row.last_price != null || row.market_value_base != null || row.market_value != null,
  ).length
  const unpricedLines = holdingsRows.length - pricedLines
  const unrealizedPnl = holdingsRows.reduce((sum, row) => {
    if (row.market_value_base == null || row.cost_basis_base == null) {
      return sum
    }
    return sum + (row.market_value_base - row.cost_basis_base)
  }, 0)
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
  const defaultPlanningTaxonomy =
    taxonomyCatalog?.taxonomies.find((taxonomy) => taxonomy.taxonomy_id === defaultPlanningTaxonomyId) ?? null

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
    const leafBuckets = new Map<string, AllocationBucket>()
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
      const leafId = leafNode?.taxonomy_node_id ?? '__unassigned__'
      const leafLabel = leafNode?.node_name ?? 'Unassigned'

      accumulateBucket(topLevelBuckets, {
        id: topLevelId,
        label: topLevelLabel,
        topLevelId,
        topLevelLabel,
        value,
        weight,
      })

      accumulateBucket(leafBuckets, {
        id: leafId,
        label: leafLabel,
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
      const leafId = cashLeafNode?.taxonomy_node_id ?? '__cash__'
      const leafLabel = cashLeafNode?.node_name ?? topLevelLabel

      accumulateBucket(topLevelBuckets, {
        id: topLevelId,
        label: topLevelLabel,
        topLevelId,
        topLevelLabel,
        value: cashValue,
        weight: cashWeight ?? 0,
      })

      accumulateBucket(leafBuckets, {
        id: leafId,
        label: leafLabel,
        topLevelId,
        topLevelLabel,
        value: cashValue,
        weight: cashWeight ?? 0,
      })
    }

    const sortedTopLevelBuckets = [...topLevelBuckets.values()].sort(
      (left, right) => right.weight - left.weight || right.value - left.value,
    )
    const sortedLeafBuckets = [...leafBuckets.values()].sort(
      (left, right) => right.weight - left.weight || right.value - left.value,
    )
    const indexEnhancementTopLevelId =
      sortedTopLevelBuckets.find((bucket) => bucket.label === '指数增强')?.id ?? null

    return {
      topLevelBuckets: sortedTopLevelBuckets,
      leafBuckets: sortedLeafBuckets,
      indexEnhancementBuckets: sortedLeafBuckets.filter(
        (bucket) => bucket.topLevelId === indexEnhancementTopLevelId && bucket.label !== '指数增强',
      ),
      assignedLabelByAssetId,
      assignedHoldingCount: assignedLabelByAssetId.size,
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
  const indexEnhancementItems = composition.indexEnhancementBuckets.map((bucket) => ({
    id: bucket.id,
    label: bucket.label,
    value: bucket.weight,
    valueLabel: formatPercent(bucket.weight),
    detail: formatCurrency(bucket.value, resolvedBaseCurrency),
  }))
  const topHoldingBarItems = sortedHoldings.slice(0, 8).map((row) => ({
    id: row.line_id,
    label: row.asset_core.asset_name,
    subtitle: composition.assignedLabelByAssetId.get(row.asset_core.asset_id)?.leafLabel ?? formatLabel(row.asset_core.asset_type),
    value: row.allocation ?? 0,
    valueLabel: formatPercent(row.allocation),
    detail: formatCurrency(row.market_value_base ?? row.market_value, resolvedBaseCurrency),
  }))

  const currentSummaryRows = [
    { label: 'As Of Date', value: summary?.as_of_date ?? holdingsWorkspace?.as_of_date ?? '—' },
    { label: 'Holdings', value: String(holdingsRows.length) },
    { label: 'Largest Holding', value: sortedHoldings[0] ? `${sortedHoldings[0].asset_core.asset_name} · ${formatPercent(sortedHoldings[0].allocation)}` : '—' },
    { label: 'Top 5 Weight', value: formatPercent(top5Weight) },
    { label: 'Cash', value: formatCurrency(cashValue, resolvedBaseCurrency) },
    { label: 'Cash Weight', value: formatPercent(cashWeight) },
    { label: 'Priced Holdings', value: `${pricedLines} / ${holdingsRows.length}` },
    {
      label: 'Planning Taxonomy',
      value: defaultPlanningTaxonomy?.name ?? 'Not configured',
    },
  ]

  const performanceSummaryRows = [
    {
      label: 'Performance Window',
      value: performanceWorkspace?.summary.start_date
        ? `${performanceWorkspace.summary.start_date} to ${performanceWorkspace.summary.end_date ?? '—'}`
        : '—',
    },
    { label: 'Coverage', value: performanceWorkspace ? formatLabel(performanceWorkspace.summary.coverage_state) : '—' },
    { label: 'Since Inception TTWROR', value: signedPercent(performanceWorkspace?.summary.cumulative_ttwror) },
    { label: 'Total P&L', value: formatSignedCurrency(performanceWorkspace?.summary.total_pnl, resolvedBaseCurrency) },
    { label: 'Unrealized P&L', value: formatSignedCurrency(unrealizedPnl, resolvedBaseCurrency) },
    { label: 'Current Drawdown', value: signedPercent(performanceWorkspace?.summary.current_drawdown) },
    { label: 'Max Drawdown', value: signedPercent(performanceWorkspace?.summary.max_drawdown) },
    {
      label: 'Latest Day Move',
      value: `${formatSignedCurrency(summary?.day_change_value, resolvedBaseCurrency)} (${signedPercent(summary?.day_change_pct)})`,
    },
  ]

  return (
    <PortfolioWorkspaceLayout activeSection="Overview" toolbarLabel="View: Portfolio Overview">
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Overview</div>
          <div className="portfolio-detail-meta">Current NAV, sleeve mix, top holdings, and since-inception return context</div>
        </div>

        <div className="holdings-meta-row">
          <p className="coverage-note">
            Overview is the current-state report page for this portfolio. It stays focused on present composition and
            cumulative outcome, then links deeper analysis out to <code>Holdings</code>, <code>Performance</code>,
            <code>Risk</code>, and <code>Taxonomies</code>.
          </p>
        </div>

        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}

        {workspaceLoading ? <CalculationStatus label="Loading portfolio summary, holdings, and strategy sleeves…" /> : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No overview workspace is available for this portfolio.</div>
        ) : null}

        {!workspaceLoading && holdingsWorkspace ? (
          <>
            <div className="portfolio-summary-strip overview-summary-strip">
              <div className="summary-card">
                <span className="summary-card-label">Total NAV</span>
                <strong className="summary-card-value">{formatCurrency(summary?.nav, resolvedBaseCurrency)}</strong>
              </div>
              <div className="summary-card">
                <span className="summary-card-label">Since Inception</span>
                <strong className="summary-card-value">{signedPercent(performanceWorkspace?.summary.cumulative_ttwror)}</strong>
              </div>
              <div className="summary-card">
                <span className="summary-card-label">Total P&amp;L</span>
                <strong className="summary-card-value">{formatSignedCurrency(performanceWorkspace?.summary.total_pnl, resolvedBaseCurrency)}</strong>
              </div>
              <div className="summary-card">
                <span className="summary-card-label">Max Drawdown</span>
                <strong className="summary-card-value">{signedPercent(performanceWorkspace?.summary.max_drawdown)}</strong>
              </div>
            </div>

            <div className="performance-summary-grid">
              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Current State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {currentSummaryRows.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td>{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Outcome And Coverage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {performanceSummaryRows.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td>{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="performance-block-grid">
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">NAV Trend</div>
                  <div className="portfolio-detail-meta">
                    {performanceWorkspace?.summary.start_date
                      ? `${performanceWorkspace.summary.start_date} to ${performanceWorkspace.summary.end_date ?? '—'}`
                      : 'Building NAV history…'}
                  </div>
                </div>
                {performanceLoading && !performanceWorkspace ? (
                  <CalculationStatus label="Building NAV path and drawdown summary…" />
                ) : null}
                {!performanceLoading && performanceWorkspace ? (
                  <PerformanceNavChart points={navChartPoints} currency={resolvedBaseCurrency} />
                ) : null}
              </section>

              <div className="overview-panel-grid">
                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Strategy Sleeves</div>
                    <div className="portfolio-detail-meta">
                      Current sleeve mix under the default planning taxonomy
                    </div>
                  </div>
                  <RiskExposureRibbon
                    segments={sleeveRibbonSegments}
                    ariaLabel="Current strategy sleeve allocation"
                    emptyLabel="No sleeve allocations are available."
                  />
                </section>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Index Enhancement Split</div>
                    <div className="portfolio-detail-meta">
                      300 / 500 / 1000 / 2000 sleeves currently inside 指数增强
                    </div>
                  </div>
                  <RiskRankedBars
                    items={indexEnhancementItems}
                    ariaLabel="Index enhancement sleeve allocation"
                    emptyLabel="No index-enhancement sleeve breakdown is available."
                  />
                </section>
              </div>

              <div className="overview-panel-grid">
                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Top Holdings</div>
                    <div className="portfolio-detail-meta">Largest current weights in the live portfolio</div>
                  </div>
                  <RiskRankedBars
                    items={topHoldingBarItems}
                    ariaLabel="Top holdings ranked by current weight"
                    emptyLabel="No holdings are available."
                  />
                </section>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Sleeve Allocation Table</div>
                    <div className="portfolio-detail-meta">
                      {composition.assignedHoldingCount} / {holdingsRows.length} holdings mapped into the active planning tree
                    </div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Sleeve</th>
                          <th>Holdings</th>
                          <th>Weight</th>
                          <th>Market Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {composition.topLevelBuckets.length ? (
                          composition.topLevelBuckets.map((bucket) => (
                            <tr key={bucket.id}>
                              <td>{bucket.label}</td>
                              <td>{bucket.holdingsCount}</td>
                              <td>{formatPercent(bucket.weight)}</td>
                              <td>{formatCurrency(bucket.value, resolvedBaseCurrency)}</td>
                            </tr>
                          ))
                        ) : (
                          <TableStatusRow colSpan={4} label="No sleeve allocations are available." />
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>

              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Top Holdings Detail</div>
                  <div className="portfolio-detail-meta">Current positions sorted by portfolio weight</div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Asset</th>
                        <th>Identifier</th>
                        <th>Sleeve</th>
                        <th>Quantity</th>
                        <th>Last Price</th>
                        <th>Market Value</th>
                        <th>Cost Basis</th>
                        <th>Unrealized P&amp;L</th>
                        <th>Weight</th>
                        <th>Coverage</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedHoldings.length ? (
                        sortedHoldings.slice(0, 12).map((row) => {
                          const unrealized =
                            row.market_value_base != null && row.cost_basis_base != null
                              ? row.market_value_base - row.cost_basis_base
                              : null
                          return (
                            <tr key={row.line_id}>
                              <td>{row.asset_core.asset_name}</td>
                              <td>{primaryIdentifier(row)}</td>
                              <td>{composition.assignedLabelByAssetId.get(row.asset_core.asset_id)?.leafLabel ?? 'Unassigned'}</td>
                              <td>{formatQuantity(row.quantity)}</td>
                              <td>{formatUnitPrice(row.last_price, row.asset_core.currency)}</td>
                              <td>{formatCurrency(row.market_value_base ?? row.market_value, resolvedBaseCurrency)}</td>
                              <td>{formatCurrency(row.cost_basis_base ?? row.cost_basis, resolvedBaseCurrency)}</td>
                              <td className={unrealized != null && unrealized < 0 ? 'negative-cell' : ''}>
                                {formatSignedCurrency(unrealized, resolvedBaseCurrency)}
                              </td>
                              <td>{formatPercent(row.allocation)}</td>
                              <td>
                                <span className={`coverage-pill ${row.last_price != null ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
                                  {row.coverage_status}
                                </span>
                              </td>
                            </tr>
                          )
                        })
                      ) : (
                        <TableStatusRow colSpan={10} label="No holdings are available for this portfolio." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
