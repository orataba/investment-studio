import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  getPortfolioPerformance,
  getPortfolioPerformanceBoundaryHoldings,
  getPortfolioPerformanceCalculation,
  getPortfolioPerformanceContribution,
  getPortfolioResearchWorkbench,
  getPortfolioTaxonomyCatalog,
  type PortfolioContributionLineRecord,
  type PortfolioContributionReportResponse,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceCalculationResponse,
  type PortfolioPerformanceCoverageState,
  type PortfolioPerformanceResponse,
  type PortfolioPeriodBoundaryHoldingRecord,
  type PortfolioPeriodBoundaryHoldingsResponse,
  type PortfolioResearchRunRecord,
  type PortfolioResearchWorkbenchResponse,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTargetSetRecord,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatSignedCurrency,
  signedValueClass,
} from '../lib/format'
import { buildTwrIndexPoints } from '../lib/performanceSeries'

const DEFAULT_REVIEW_LOOKBACK_DAYS = 30

type MonthlyBucket = {
  bucketKey: string
  startDate: string
  endDate: string
  coverageState: PortfolioPerformanceCoverageState
  observationCount: number
  staleCount: number
  cumulativeReturn: number | null
  absoluteChange: number | null
  maxDrawdown: number | null
}

function localDateIso(input = new Date()) {
  const year = input.getFullYear()
  const month = `${input.getMonth() + 1}`.padStart(2, '0')
  const day = `${input.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

function shiftIsoDate(isoDate: string, days: number) {
  const [year, month, day] = isoDate.split('-').map(Number)
  const nextDate = new Date(year, (month || 1) - 1, day || 1)
  nextDate.setDate(nextDate.getDate() + days)
  return localDateIso(nextDate)
}

function coverageClassName(coverageState: PortfolioPerformanceCoverageState) {
  return coverageState === 'complete' ? 'coverage-pill-live' : 'coverage-pill-warning'
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

function isPeriodOverlap(
  effectiveFrom: string | null | undefined,
  effectiveTo: string | null | undefined,
  startDate: string,
  endDate: string,
) {
  if (effectiveFrom && effectiveFrom > endDate) {
    return false
  }
  if (effectiveTo && effectiveTo < startDate) {
    return false
  }
  return true
}

function buildMonthlyBuckets(dailySeries: PortfolioDailyPerformancePoint[]) {
  const orderedKeys: string[] = []
  const buckets = new Map<
    string,
    {
      bucketKey: string
      startDate: string
      endDate: string
      observationCount: number
      staleCount: number
      growthIndex: number
      absoluteChange: number
      absoluteChangeComplete: boolean
      seenComplete: boolean
      seenPartial: boolean
      seenUnavailable: boolean
      maxDrawdown: number | null
    }
  >()

  dailySeries.forEach((point) => {
    const bucketKey = point.as_of_date.slice(0, 7)
    let bucket = buckets.get(bucketKey)
    if (!bucket) {
      bucket = {
        bucketKey,
        startDate: point.as_of_date,
        endDate: point.as_of_date,
        observationCount: 0,
        staleCount: 0,
        growthIndex: 1,
        absoluteChange: 0,
        absoluteChangeComplete: true,
        seenComplete: false,
        seenPartial: false,
        seenUnavailable: false,
        maxDrawdown: null,
      }
      buckets.set(bucketKey, bucket)
      orderedKeys.push(bucketKey)
    }

    bucket.endDate = point.as_of_date
    if (point.daily_twr != null) {
      bucket.growthIndex *= 1 + point.daily_twr
      bucket.observationCount += 1
    }
    if (point.absolute_change == null) {
      bucket.absoluteChangeComplete = false
    } else {
      bucket.absoluteChange += point.absolute_change
    }
    if (point.stale_price_flag || point.stale_fx_flag) {
      bucket.staleCount += 1
    }
    if (point.drawdown != null) {
      bucket.maxDrawdown = bucket.maxDrawdown == null ? point.drawdown : Math.min(bucket.maxDrawdown, point.drawdown)
    }
    if (point.coverage_state === 'complete') {
      bucket.seenComplete = true
    } else if (point.coverage_state === 'partial') {
      bucket.seenPartial = true
    } else {
      bucket.seenUnavailable = true
    }
  })

  return orderedKeys.map((bucketKey) => {
    const bucket = buckets.get(bucketKey)!
    let coverageState: PortfolioPerformanceCoverageState = 'unavailable'
    if (bucket.seenComplete) {
      coverageState = bucket.seenPartial || bucket.seenUnavailable ? 'partial' : 'complete'
    } else if (bucket.seenPartial) {
      coverageState = 'partial'
    }

    return {
      bucketKey: bucket.bucketKey,
      startDate: bucket.startDate,
      endDate: bucket.endDate,
      coverageState,
      observationCount: bucket.observationCount,
      staleCount: bucket.staleCount,
      cumulativeReturn: bucket.observationCount ? bucket.growthIndex - 1 : null,
      absoluteChange: bucket.absoluteChangeComplete ? bucket.absoluteChange : null,
      maxDrawdown: bucket.maxDrawdown,
    } satisfies MonthlyBucket
  })
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

export default function ReviewPage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [performanceWorkspace, setPerformanceWorkspace] = useState<PortfolioPerformanceResponse | null>(null)
  const [calculationWorkspace, setCalculationWorkspace] = useState<PortfolioPerformanceCalculationResponse | null>(null)
  const [contributionWorkspace, setContributionWorkspace] = useState<PortfolioContributionReportResponse | null>(null)
  const [boundaryWorkspace, setBoundaryWorkspace] = useState<PortfolioPeriodBoundaryHoldingsResponse | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [researchWorkbench, setResearchWorkbench] = useState<PortfolioResearchWorkbenchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [workspaceIssues, setWorkspaceIssues] = useState<string[]>([])

  const appliedStartDate = searchParams.get('start_date') ?? ''
  const appliedEndDate = searchParams.get('end_date') ?? ''
  const selectedRunId = searchParams.get('run_id') ?? ''
  const defaultEndDate = useMemo(() => localDateIso(), [])
  const defaultStartDate = useMemo(
    () => shiftIsoDate(defaultEndDate, -(DEFAULT_REVIEW_LOOKBACK_DAYS - 1)),
    [defaultEndDate],
  )
  const effectiveStartDate = appliedStartDate || defaultStartDate
  const effectiveEndDate = appliedEndDate || defaultEndDate
  const [draftStartDate, setDraftStartDate] = useState(effectiveStartDate)
  const [draftEndDate, setDraftEndDate] = useState(effectiveEndDate)

  useEffect(() => {
    setDraftStartDate(effectiveStartDate)
    setDraftEndDate(effectiveEndDate)
  }, [effectiveEndDate, effectiveStartDate])

  function updateSearchParams(updates: Record<string, string | null>) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      let changed = false

      Object.entries(updates).forEach(([key, value]) => {
        const normalizedValue = value && value.trim() ? value : null
        const currentValue = current.get(key)
        if (normalizedValue === currentValue || (!normalizedValue && !currentValue)) {
          return
        }
        changed = true
        if (normalizedValue) {
          next.set(key, normalizedValue)
        } else {
          next.delete(key)
        }
      })

      return changed ? next : current
    })
  }

  function handleApplyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    updateSearchParams({
      start_date: draftStartDate || null,
      end_date: draftEndDate || null,
    })
  }

  function handleResetFilters() {
    setDraftStartDate(defaultStartDate)
    setDraftEndDate(defaultEndDate)
    updateSearchParams({
      start_date: null,
      end_date: null,
    })
  }

  useEffect(() => {
    if (!portfolioId) {
      setPerformanceWorkspace(null)
      setCalculationWorkspace(null)
      setContributionWorkspace(null)
      setBoundaryWorkspace(null)
      setTaxonomyCatalog(null)
      setResearchWorkbench(null)
      setWorkspaceIssues([])
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)

    Promise.allSettled([
      getPortfolioPerformance(portfolioId, {
        start_date: effectiveStartDate || undefined,
        end_date: effectiveEndDate || undefined,
      }),
      getPortfolioPerformanceCalculation(portfolioId, {
        start_date: effectiveStartDate || undefined,
        end_date: effectiveEndDate || undefined,
      }),
      getPortfolioPerformanceContribution(portfolioId, {
        start_date: effectiveStartDate || undefined,
        end_date: effectiveEndDate || undefined,
        axis: 'instrument',
      }),
      getPortfolioPerformanceBoundaryHoldings(portfolioId, {
        start_date: effectiveStartDate || undefined,
        end_date: effectiveEndDate || undefined,
      }),
      getPortfolioTaxonomyCatalog(portfolioId),
      getPortfolioResearchWorkbench(portfolioId, selectedRunId || undefined),
    ])
      .then(([performanceResult, calculationResult, contributionResult, boundaryResult, taxonomyResult, researchResult]) => {
        if (cancelled) {
          return
        }

        const issues: string[] = []

        if (performanceResult.status === 'fulfilled') {
          setPerformanceWorkspace(performanceResult.value)
        } else {
          setPerformanceWorkspace(null)
          issues.push('Performance window unavailable.')
        }

        if (calculationResult.status === 'fulfilled') {
          setCalculationWorkspace(calculationResult.value)
        } else {
          setCalculationWorkspace(null)
          issues.push('Period calculation unavailable.')
        }

        if (contributionResult.status === 'fulfilled') {
          setContributionWorkspace(contributionResult.value)
        } else {
          setContributionWorkspace(null)
          issues.push('Contribution ranking unavailable.')
        }

        if (boundaryResult.status === 'fulfilled') {
          setBoundaryWorkspace(boundaryResult.value)
        } else {
          setBoundaryWorkspace(null)
          issues.push('Boundary holdings unavailable.')
        }

        if (taxonomyResult.status === 'fulfilled') {
          setTaxonomyCatalog(taxonomyResult.value)
        } else {
          setTaxonomyCatalog(null)
          issues.push('Planning-target context unavailable.')
        }

        if (researchResult.status === 'fulfilled') {
          setResearchWorkbench(researchResult.value)
        } else {
          setResearchWorkbench(null)
          issues.push('Research handoff unavailable.')
        }

        setWorkspaceIssues(Array.from(new Set(issues)))
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [effectiveEndDate, effectiveStartDate, portfolioId, selectedRunId])

  const baseCurrency =
    performanceWorkspace?.base_currency ??
    calculationWorkspace?.base_currency ??
    contributionWorkspace?.base_currency ??
    boundaryWorkspace?.base_currency ??
    researchWorkbench?.base_currency ??
    'USD'

  const monthlyBuckets = useMemo(
    () => buildMonthlyBuckets(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )
  const recentMonthlyBuckets = useMemo(
    () => [...monthlyBuckets].reverse().slice(0, 12),
    [monthlyBuckets],
  )
  const navChartPoints = useMemo(
    () =>
      (performanceWorkspace?.daily_series ?? [])
        .filter((point) => point.ending_nav != null)
        .map((point) => ({ date: point.as_of_date, value: point.ending_nav as number })),
    [performanceWorkspace],
  )
  const twrIndexChartPoints = useMemo(
    () => buildTwrIndexPoints(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )
  const recentDailyRows = useMemo(
    () => [...(performanceWorkspace?.daily_series ?? [])].reverse().slice(0, 20),
    [performanceWorkspace],
  )
  const worstDailyRows = useMemo(
    () =>
      [...(performanceWorkspace?.daily_series ?? [])]
        .filter((point) => point.daily_twr != null)
        .sort((left, right) => (left.daily_twr ?? 0) - (right.daily_twr ?? 0))
        .slice(0, 10),
    [performanceWorkspace],
  )
  const contributionLines = useMemo(
    () => contributionWorkspace?.lines ?? [],
    [contributionWorkspace],
  )
  const rankedContributionLines = useMemo(
    () =>
      [...contributionLines].sort(
        (left, right) =>
          Math.abs(right.period_contribution ?? right.total_pnl ?? 0) - Math.abs(left.period_contribution ?? left.total_pnl ?? 0),
      ),
    [contributionLines],
  )
  const topContributors = useMemo(
    () =>
      [...contributionLines]
        .filter((line) => (line.period_contribution ?? 0) > 0)
        .sort((left, right) => (right.period_contribution ?? 0) - (left.period_contribution ?? 0))
        .slice(0, 8),
    [contributionLines],
  )
  const topDetractors = useMemo(
    () =>
      [...contributionLines]
        .filter((line) => (line.period_contribution ?? 0) < 0)
        .sort((left, right) => (left.period_contribution ?? 0) - (right.period_contribution ?? 0))
        .slice(0, 8),
    [contributionLines],
  )
  const startBoundaryRows = useMemo(
    () => [...(boundaryWorkspace?.start_positions ?? [])].sort((left, right) => (right.market_value_base ?? 0) - (left.market_value_base ?? 0)),
    [boundaryWorkspace],
  )
  const endBoundaryRows = useMemo(
    () => [...(boundaryWorkspace?.end_positions ?? [])].sort((left, right) => (right.market_value_base ?? 0) - (left.market_value_base ?? 0)),
    [boundaryWorkspace],
  )
  const realizedRiskMetrics = useMemo(() => {
    const dailySeries = performanceWorkspace?.daily_series ?? []
    const validDailyReturns = dailySeries.filter((point) => point.daily_twr != null)
    const worstDay =
      [...validDailyReturns].sort((left, right) => (left.daily_twr ?? 0) - (right.daily_twr ?? 0))[0] ?? null
    const deepestDrawdown =
      [...dailySeries].filter((point) => point.drawdown != null).sort((left, right) => (left.drawdown ?? 0) - (right.drawdown ?? 0))[0] ??
      null
    return {
      observationCount: performanceWorkspace?.summary.return_observation_count ?? 0,
      staleDays: dailySeries.filter((point) => point.stale_price_flag || point.stale_fx_flag).length,
      downDays: validDailyReturns.filter((point) => (point.daily_twr ?? 0) < 0).length,
      worstDay,
      deepestDrawdown,
    }
  }, [performanceWorkspace])

  const planningTaxonomies = taxonomyCatalog?.taxonomies.filter((taxonomy) => taxonomy.planning_enabled) ?? []
  const defaultPlanningTaxonomy =
    planningTaxonomies.find((taxonomy) => taxonomy.taxonomy_id === taxonomyCatalog?.default_planning_taxonomy_id) ?? null
  const overlappingTargetSets = useMemo(
    () =>
      (taxonomyCatalog?.target_sets ?? []).filter(
        (targetSet) =>
          targetSet.taxonomy_id === defaultPlanningTaxonomy?.taxonomy_id &&
          isPeriodOverlap(targetSet.effective_from, targetSet.effective_to, effectiveStartDate, effectiveEndDate),
      ),
    [defaultPlanningTaxonomy?.taxonomy_id, effectiveEndDate, effectiveStartDate, taxonomyCatalog],
  )
  const rootSaaTargetSets = overlappingTargetSets.filter(
    (targetSet) => !targetSet.comparator_taxonomy_node_id && targetSet.target_set_type === 'saa' && targetSet.status === 'active',
  )
  const rootTaaTargetSets = overlappingTargetSets.filter(
    (targetSet) => !targetSet.comparator_taxonomy_node_id && targetSet.target_set_type === 'taa' && targetSet.status === 'active',
  )
  const overlappingScopedTargetSetCount = overlappingTargetSets.filter(
    (targetSet) => Boolean(targetSet.comparator_taxonomy_node_id) && targetSet.status === 'active',
  ).length

  function describeTargetTimeline(targetSets: PortfolioTargetSetRecord[]) {
    if (!targetSets.length) {
      return 'Not configured'
    }
    if (targetSets.length === 1) {
      return targetSets[0].name
    }
    return `Mixed (${targetSets.length})`
  }

  const targetResolutionMode = (() => {
    if (!rootSaaTargetSets.length && !rootTaaTargetSets.length) {
      return 'Not configured'
    }
    if (rootSaaTargetSets.length > 1 || rootTaaTargetSets.length > 1) {
      return 'Mixed Targets'
    }
    return rootTaaTargetSets.length ? 'TAA over SAA' : 'SAA only'
  })()

  const selectedResearchRun =
    researchWorkbench?.selected_run ??
    (selectedRunId
      ? researchWorkbench?.runs.find((run) => run.research_run_id === selectedRunId) ?? null
      : researchWorkbench?.runs[0] ?? null)

  const selectedResearchRunValue = selectedResearchRun?.research_run_id ?? ''
  const selectedRunSignalMap = useMemo(() => {
    const entries = (selectedResearchRun?.detail?.signals ?? []).map((signal) => [signal.label, signal.value] as const)
    return new Map(entries)
  }, [selectedResearchRun])
  const rankedTargetGaps = useMemo(
    () =>
      [...(selectedResearchRun?.detail?.target_weight_gaps ?? [])].sort(
        (left, right) => Math.abs(right.gap ?? 0) - Math.abs(left.gap ?? 0),
      ),
    [selectedResearchRun],
  )
  const actionItems = useMemo(() => {
    const rows: Array<{ source: string; action: string; detail: string }> = []

    rankedTargetGaps.slice(0, 4).forEach((suggestion) => {
      rows.push({
        source: 'Research',
        action: `${formatLabel(suggestion.action)} ${suggestion.label}`,
        detail: `${formatPercent(suggestion.current_weight)} -> ${formatPercent(suggestion.target_weight)} (${signedPercent(
          suggestion.gap,
        )})`,
      })
    })

    topDetractors.slice(0, 3).forEach((line) => {
      rows.push({
        source: 'Performance',
        action: `Review ${line.group_label}`,
        detail: `${signedPercent(line.period_contribution)} contribution; ${formatSignedCurrency(line.total_pnl, baseCurrency)} total P&L`,
      })
    })

    ;(selectedResearchRun?.detail?.warnings ?? []).slice(0, 3).forEach((warning) => {
      rows.push({
        source: 'Research Warning',
        action: warning,
        detail: 'Target solve coverage or solver issue should be reviewed before translating weights into orders.',
      })
    })

    return rows.slice(0, 10)
  }, [baseCurrency, rankedTargetGaps, selectedResearchRun, topDetractors])

  const reviewSummaryLeft = [
    { label: 'Window', value: `${effectiveStartDate} to ${effectiveEndDate}` },
    {
      label: 'Coverage',
      value: performanceWorkspace?.summary.coverage_state ? formatLabel(performanceWorkspace.summary.coverage_state) : '—',
    },
    { label: 'Start NAV', value: formatCurrency(performanceWorkspace?.summary.start_nav, baseCurrency) },
    { label: 'End NAV', value: formatCurrency(performanceWorkspace?.summary.end_nav, baseCurrency) },
    {
      label: 'Cumulative Return',
      value: signedPercent(performanceWorkspace?.summary.cumulative_twr),
      toneClassName: signedValueClass(performanceWorkspace?.summary.cumulative_twr),
    },
    {
      label: 'Total P&L',
      value: formatSignedCurrency(performanceWorkspace?.summary.total_pnl, baseCurrency),
      toneClassName: signedValueClass(performanceWorkspace?.summary.total_pnl),
    },
    { label: 'Net External Inflow', value: formatSignedCurrency(performanceWorkspace?.summary.net_external_inflow, baseCurrency) },
    { label: 'Observation Count', value: String(realizedRiskMetrics.observationCount) },
  ]

  const reviewSummaryRight = [
    { label: 'Default Planning Taxonomy', value: defaultPlanningTaxonomy?.name ?? 'Not configured' },
    { label: 'Target Resolution', value: targetResolutionMode },
    { label: 'Root SAA Timeline', value: describeTargetTimeline(rootSaaTargetSets) },
    { label: 'Root TAA Timeline', value: describeTargetTimeline(rootTaaTargetSets) },
    { label: 'Scoped Target Sets', value: String(overlappingScopedTargetSetCount) },
    {
      label: 'Risk Budget Timeline',
      value: overlappingTargetSets.some((targetSet) => targetSet.risk_budget_enabled) ? 'Configured' : 'Not configured',
    },
    { label: 'Research Handoff', value: selectedResearchRun ? resolveRunLabel(selectedResearchRun) : 'Not selected' },
    { label: 'Research Scope', value: selectedResearchRun?.detail?.selected_scope?.path ?? 'Top Level' },
  ]

  const riskSummaryLeft = [
    { label: 'Annualized Volatility', value: signedPercent(performanceWorkspace?.summary.annualized_volatility) },
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
    { label: 'Sortino Ratio', value: formatNumber(performanceWorkspace?.summary.sortino_ratio, 2) },
    { label: 'Down Days', value: String(realizedRiskMetrics.downDays) },
    { label: 'Stale Days', value: String(realizedRiskMetrics.staleDays) },
  ]

  const riskSummaryRight = [
    {
      label: 'Worst Day',
      value: signedPercent(realizedRiskMetrics.worstDay?.daily_twr, 3),
      toneClassName: signedValueClass(realizedRiskMetrics.worstDay?.daily_twr),
    },
    { label: 'Worst Day Date', value: realizedRiskMetrics.worstDay?.as_of_date ?? '—' },
    { label: 'Deepest Drawdown Date', value: realizedRiskMetrics.deepestDrawdown?.as_of_date ?? '—' },
    { label: 'Sharpe Ratio', value: formatNumber(performanceWorkspace?.summary.sharpe_ratio, 2) },
    { label: 'Observation Window', value: `${performanceWorkspace?.summary.start_date ?? '—'} to ${performanceWorkspace?.summary.end_date ?? '—'}` },
    { label: 'Pricing Coverage', value: performanceWorkspace?.summary.latest_complete_as_of_date ?? '—' },
  ]

  return (
    <PortfolioWorkspaceLayout activeSection="Review" toolbarLabel="View: Review Pack">
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Review</div>
        </div>

        <form className="performance-filter-bar" onSubmit={handleApplyFilters}>
          <div className="review-filter-grid">
            <label>
              <span>Start Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={draftStartDate}
                onChange={(event) => setDraftStartDate(event.target.value)}
              />
            </label>
            <label>
              <span>End Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={draftEndDate}
                onChange={(event) => setDraftEndDate(event.target.value)}
              />
            </label>
            <label>
              <span>Research Run</span>
              <select
                value={selectedResearchRunValue}
                onChange={(event) => updateSearchParams({ run_id: event.target.value || null })}
              >
                <option value="">Latest</option>
                {(researchWorkbench?.runs ?? []).map((run) => (
                  <option key={run.research_run_id} value={run.research_run_id}>
                    {resolveRunLabel(run)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="performance-filter-actions">
            <button type="submit">Apply Period</button>
            <button type="button" onClick={handleResetFilters}>
              Reset
            </button>
          </div>
        </form>

        {workspaceIssues.map((issue) => (
          <div className="inline-notice inline-notice-error" key={issue}>
            {issue}
          </div>
        ))}

        {loading && !performanceWorkspace ? (
          <CalculationStatus label="Building review pack from performance, boundary composition, targets, and research handoff…" />
        ) : null}

        {!loading && !performanceWorkspace && !workspaceIssues.length ? (
          <div className="empty-state">No review workspace is available for this portfolio.</div>
        ) : null}

        {performanceWorkspace ? (
          <>
            <div className="performance-summary-grid">
              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Period Scorecard</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reviewSummaryLeft.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td className={row.toneClassName}>{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Target And Review Context</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reviewSummaryRight.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td>{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                <div className="panel-title">NAV Trend</div>
              </div>
              <PerformanceNavChart points={navChartPoints} twrPoints={twrIndexChartPoints} currency={baseCurrency} />
            </section>

            <div className="performance-block-grid">
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Period Calculation</div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Line</th>
                        <th>Kind</th>
                        <th>Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {calculationWorkspace?.lines.length ? (
                        calculationWorkspace.lines.map((line) => (
                          <tr key={line.key}>
                            <td className={line.parent_key ? 'performance-line-label performance-line-label-child' : 'performance-line-label'}>
                              {line.label}
                            </td>
                            <td>{formatLabel(line.line_kind)}</td>
                            <td className={signedValueClass(line.amount)}>
                              {formatSignedCurrency(line.amount, calculationWorkspace.base_currency)}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow colSpan={3} label="No period calculation lines available." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Monthly Return Tape</div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Bucket</th>
                        <th>Coverage</th>
                        <th>Observations</th>
                        <th>Stale Days</th>
                        <th>Return</th>
                        <th>Absolute Change</th>
                        <th>Max DD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentMonthlyBuckets.length ? (
                        recentMonthlyBuckets.map((bucket) => (
                          <tr key={bucket.bucketKey}>
                            <td>{bucket.bucketKey}</td>
                            <td>
                              <span className={`coverage-pill ${coverageClassName(bucket.coverageState)}`}>
                                {formatLabel(bucket.coverageState)}
                              </span>
                            </td>
                            <td>{bucket.observationCount}</td>
                            <td>{bucket.staleCount}</td>
                            <td className={signedValueClass(bucket.cumulativeReturn)}>
                              {signedPercent(bucket.cumulativeReturn)}
                            </td>
                            <td className={signedValueClass(bucket.absoluteChange)}>
                              {formatSignedCurrency(bucket.absoluteChange, baseCurrency)}
                            </td>
                            <td className={signedValueClass(bucket.maxDrawdown)}>
                              {signedPercent(bucket.maxDrawdown)}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow colSpan={7} label="No monthly buckets available for this review window." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>

            <div className="performance-summary-grid">
              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th colSpan={6}>Top Contributors</th>
                    </tr>
                    <tr>
                      <th>Group</th>
                      <th>Avg Weight</th>
                      <th>End Weight</th>
                      <th>Total P&amp;L</th>
                      <th>Contribution</th>
                      <th>Ending Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topContributors.length ? (
                      topContributors.map((line) => (
                        <ReviewContributionRow key={`positive:${line.group_key}`} line={line} currency={baseCurrency} />
                      ))
                    ) : (
                      <TableStatusRow colSpan={6} label="No positive contributors in the selected window." />
                    )}
                  </tbody>
                </table>
              </div>

              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th colSpan={6}>Top Detractors</th>
                    </tr>
                    <tr>
                      <th>Group</th>
                      <th>Avg Weight</th>
                      <th>End Weight</th>
                      <th>Total P&amp;L</th>
                      <th>Contribution</th>
                      <th>Ending Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topDetractors.length ? (
                      topDetractors.map((line) => (
                        <ReviewContributionRow key={`negative:${line.group_key}`} line={line} currency={baseCurrency} />
                      ))
                    ) : (
                      <TableStatusRow colSpan={6} label="No negative contributors in the selected window." />
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="performance-summary-grid">
              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Realized Risk Summary</th>
                    </tr>
                  </thead>
                  <tbody>
                    {riskSummaryLeft.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td className={row.toneClassName}>{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Stress Markers</th>
                    </tr>
                  </thead>
                  <tbody>
                    {riskSummaryRight.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td className={row.toneClassName}>{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                <div className="panel-title">Worst Days</div>
              </div>
              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Coverage</th>
                      <th>Ending NAV</th>
                      <th>Daily Return</th>
                      <th>Drawdown</th>
                      <th>P&amp;L Ex Flows</th>
                      <th>Stale Flags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {worstDailyRows.length ? (
                      worstDailyRows.map((point) => (
                        <tr key={point.as_of_date}>
                          <td>{point.as_of_date}</td>
                          <td>
                            <span className={`coverage-pill ${coverageClassName(point.coverage_state)}`}>
                              {formatLabel(point.coverage_state)}
                            </span>
                          </td>
                          <td>{formatCurrency(point.ending_nav, baseCurrency)}</td>
                          <td className={signedValueClass(point.daily_twr)}>
                            {signedPercent(point.daily_twr, 3)}
                          </td>
                          <td className={signedValueClass(point.drawdown)}>
                            {signedPercent(point.drawdown)}
                          </td>
                          <td className={signedValueClass(point.delta)}>
                            {formatSignedCurrency(point.delta, baseCurrency)}
                          </td>
                          <td>{point.stale_price_flag || point.stale_fx_flag ? 'Observed' : 'Clean'}</td>
                        </tr>
                      ))
                    ) : (
                      <TableStatusRow colSpan={7} label="No worst-day rows available for this window." />
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                <div className="panel-title">Boundary Composition</div>
              </div>
              <div className="performance-summary-grid">
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th colSpan={4}>Start Boundary</th>
                      </tr>
                      <tr>
                        <th>Asset</th>
                        <th>Quantity</th>
                        <th>Market Value</th>
                        <th>Weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      {startBoundaryRows.length ? (
                        startBoundaryRows.slice(0, 12).map((position) => (
                          <BoundaryRow key={`start:${position.position_id}`} position={position} currency={baseCurrency} />
                        ))
                      ) : (
                        <TableStatusRow colSpan={4} label="No start-boundary positions available." />
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th colSpan={4}>End Boundary</th>
                      </tr>
                      <tr>
                        <th>Asset</th>
                        <th>Quantity</th>
                        <th>Market Value</th>
                        <th>Weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      {endBoundaryRows.length ? (
                        endBoundaryRows.slice(0, 12).map((position) => (
                          <BoundaryRow key={`end:${position.position_id}`} position={position} currency={baseCurrency} />
                        ))
                      ) : (
                        <TableStatusRow colSpan={4} label="No end-boundary positions available." />
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                <div className="panel-title">Research Handoff</div>
              </div>

              {selectedResearchRun ? (
                <>
                  <div className="performance-summary-grid">
                    <div className="table-shell">
                      <table className="performance-summary-table">
                        <thead>
                          <tr>
                            <th colSpan={2}>Selected Run</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <th>Run</th>
                            <td>{resolveRunLabel(selectedResearchRun)}</td>
                          </tr>
                          <tr>
                            <th>Status</th>
                            <td>{formatLabel(selectedResearchRun.status)}</td>
                          </tr>
                          <tr>
                            <th>Planning Axis</th>
                            <td>{selectedResearchRun.planning_taxonomy_name ?? 'Not configured'}</td>
                          </tr>
                          <tr>
                            <th>Scope</th>
                            <td>{selectedResearchRun.detail?.selected_scope?.path ?? 'Top Level'}</td>
                          </tr>
                          <tr>
                            <th>Headline</th>
                            <td>{selectedResearchRun.headline ?? selectedResearchRun.detail?.headline ?? '—'}</td>
                          </tr>
                          <tr>
                            <th>Warnings</th>
                            <td>{String(selectedResearchRun.detail?.warnings.length ?? 0)}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>

                    <div className="table-shell">
                      <table className="performance-summary-table">
                        <thead>
                          <tr>
                            <th colSpan={2}>Target Solve</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[
                            { label: 'Target Layer', value: selectedRunSignalMap.get('Target Layer') ?? '—' },
                            { label: 'Target Volatility', value: selectedRunSignalMap.get('Target Volatility') ?? '—' },
                            { label: 'Estimated Volatility', value: selectedRunSignalMap.get('Estimated Volatility') ?? '—' },
                            { label: 'Gross Exposure', value: selectedRunSignalMap.get('Gross Exposure') ?? '—' },
                            { label: 'Largest Weight Gap', value: selectedRunSignalMap.get('Largest Weight Gap') ?? '—' },
                            { label: 'Largest Risk Gap', value: selectedRunSignalMap.get('Largest Risk Gap') ?? '—' },
                          ].map((row) => (
                            <tr key={row.label}>
                              <th>{row.label}</th>
                              <td>{row.value}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div className="performance-summary-grid">
                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th colSpan={2}>Findings</th>
                          </tr>
                          <tr>
                            <th>Finding</th>
                            <th>Detail</th>
                          </tr>
                        </thead>
                        <tbody>
                          {selectedResearchRun.detail?.findings.length ? (
                            selectedResearchRun.detail.findings.map((finding) => (
                              <tr key={finding.title}>
                                <td>{finding.title}</td>
                                <td>{finding.detail}</td>
                              </tr>
                            ))
                          ) : (
                            <TableStatusRow colSpan={2} label="No findings on the selected research run." />
                          )}
                        </tbody>
                      </table>
                    </div>

                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th colSpan={4}>Target Weight Gaps</th>
                          </tr>
                          <tr>
                            <th>Action</th>
                            <th>Member</th>
                            <th>Current</th>
                            <th>Target</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rankedTargetGaps.length ? (
                            rankedTargetGaps.slice(0, 8).map((suggestion) => (
                              <tr key={`${suggestion.member_type}:${suggestion.member_id}`}>
                                <td>{formatLabel(suggestion.action)}</td>
                                <td>{suggestion.label}</td>
                                <td>{formatPercent(suggestion.current_weight)}</td>
                                <td>{formatPercent(suggestion.target_weight)}</td>
                              </tr>
                            ))
                          ) : (
                            <TableStatusRow colSpan={4} label="No target weight gaps on the selected research run." />
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </>
              ) : (
                <div className="empty-state">No research run is available for review handoff.</div>
              )}
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                <div className="panel-title">Action Items</div>
              </div>
              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Action</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {actionItems.length ? (
                      actionItems.map((item) => (
                        <tr key={`${item.source}:${item.action}`}>
                          <td>{item.source}</td>
                          <td>{item.action}</td>
                          <td>{item.detail}</td>
                        </tr>
                      ))
                    ) : (
                      <TableStatusRow colSpan={3} label="No action items generated for the selected review context." />
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                <div className="panel-title">Recent Monitoring Tape</div>
              </div>
              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Coverage</th>
                      <th>Ending NAV</th>
                      <th>Daily Return</th>
                      <th>Cumulative Return</th>
                      <th>Drawdown</th>
                      <th>Stale Price</th>
                      <th>Stale FX</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentDailyRows.length ? (
                      recentDailyRows.map((point) => (
                        <tr key={point.as_of_date}>
                          <td>{point.as_of_date}</td>
                          <td>
                            <span className={`coverage-pill ${coverageClassName(point.coverage_state)}`}>
                              {formatLabel(point.coverage_state)}
                            </span>
                          </td>
                          <td>{formatCurrency(point.ending_nav, baseCurrency)}</td>
                          <td className={signedValueClass(point.daily_twr)}>
                            {signedPercent(point.daily_twr, 3)}
                          </td>
                          <td className={signedValueClass(point.cumulative_twr)}>
                            {signedPercent(point.cumulative_twr)}
                          </td>
                          <td className={signedValueClass(point.drawdown)}>
                            {signedPercent(point.drawdown)}
                          </td>
                          <td>{point.stale_price_flag ? 'Yes' : 'No'}</td>
                          <td>{point.stale_fx_flag ? 'Yes' : 'No'}</td>
                        </tr>
                      ))
                    ) : (
                      <TableStatusRow colSpan={8} label="No recent monitoring rows available." />
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}

function resolveRunLabel(run: PortfolioResearchRunRecord) {
  const finishedAt = run.finished_at ?? run.started_at ?? run.requested_at ?? run.as_of_date ?? '—'
  return `${run.research_run_id} · ${formatLabel(run.status)} · ${finishedAt}`
}

function ReviewContributionRow({
  line,
  currency,
}: {
  line: PortfolioContributionLineRecord
  currency: string
}) {
  return (
    <tr>
      <td>{line.group_label}</td>
      <td>{formatPercent(line.average_weight)}</td>
      <td>{formatPercent(line.ending_weight)}</td>
      <td className={signedValueClass(line.total_pnl)}>
        {formatSignedCurrency(line.total_pnl, currency)}
      </td>
      <td className={signedValueClass(line.period_contribution)}>
        {signedPercent(line.period_contribution)}
      </td>
      <td>{formatCurrency(line.end_value_base, currency)}</td>
    </tr>
  )
}

function BoundaryRow({
  position,
  currency,
}: {
  position: PortfolioPeriodBoundaryHoldingRecord
  currency: string
}) {
  return (
    <tr>
      <td>{position.instrument_ref.asset_name}</td>
      <td>{formatQuantity(position.quantity)}</td>
      <td>{formatCurrency(position.market_value_base, currency)}</td>
      <td>{formatPercent(position.portfolio_weight)}</td>
    </tr>
  )
}
