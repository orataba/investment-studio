import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import RiskAccountBars from '../components/RiskAccountBars'
import RiskExposureRibbon from '../components/RiskExposureRibbon'
import RiskRankedBars from '../components/RiskRankedBars'
import {
  getHoldingsWorkspace,
  getPortfolioAccountsWorkspace,
  getPortfolioPerformance,
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceCoverageState,
  type PortfolioPerformanceResponse,
  type PortfolioTaxonomyCatalogResponse,
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

type RiskMode = 'current' | 'realized'

const LOOKBACK_OPTIONS = [30, 60, 90] as const

function shiftIsoDate(isoDate: string, days: number) {
  const [year, month, day] = isoDate.split('-').map(Number)
  const nextDate = new Date(year, (month || 1) - 1, day || 1)
  nextDate.setDate(nextDate.getDate() + days)
  const nextYear = nextDate.getFullYear()
  const nextMonth = `${nextDate.getMonth() + 1}`.padStart(2, '0')
  const nextDay = `${nextDate.getDate()}`.padStart(2, '0')
  return `${nextYear}-${nextMonth}-${nextDay}`
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

function buildMonthlyBuckets(dailySeries: PortfolioDailyPerformancePoint[]) {
  const orderedKeys: string[] = []
  const buckets = new Map<
    string,
    {
      bucketKey: string
      startDate: string
      endDate: string
      observationCount: number
      growthIndex: number
      staleCount: number
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
        growthIndex: 1,
        staleCount: 0,
        seenComplete: false,
        seenPartial: false,
        seenUnavailable: false,
        maxDrawdown: null,
      }
      buckets.set(bucketKey, bucket)
      orderedKeys.push(bucketKey)
    }

    bucket.endDate = point.as_of_date
    if (point.daily_ttwror != null) {
      bucket.growthIndex *= 1 + point.daily_ttwror
      bucket.observationCount += 1
    }
    if (point.stale_price_flag || point.stale_fx_flag) {
      bucket.staleCount += 1
    }
    if (point.drawdown != null) {
      bucket.maxDrawdown =
        bucket.maxDrawdown == null ? point.drawdown : Math.min(bucket.maxDrawdown, point.drawdown)
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
      maxDrawdown: bucket.maxDrawdown,
    }
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

export default function RiskPage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [accountsWorkspace, setAccountsWorkspace] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [performanceWorkspace, setPerformanceWorkspace] = useState<PortfolioPerformanceResponse | null>(null)
  const [performanceLoading, setPerformanceLoading] = useState(false)
  const [performanceError, setPerformanceError] = useState<string | null>(null)

  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const riskMode = (searchParams.get('risk_tab') === 'realized' ? 'realized' : 'current') satisfies RiskMode
  const lookbackDays = (() => {
    const raw = Number(searchParams.get('lookback_days') ?? '')
    if (LOOKBACK_OPTIONS.includes(raw as (typeof LOOKBACK_OPTIONS)[number])) {
      return raw as (typeof LOOKBACK_OPTIONS)[number]
    }
    return 30
  })()
  const [draftAsOfDate, setDraftAsOfDate] = useState(requestedAsOfDate)

  useEffect(() => {
    setDraftAsOfDate(requestedAsOfDate)
  }, [requestedAsOfDate])

  function updateSearchParam(key: string, value: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      const normalizedValue = value && value.trim() ? value : null
      const currentValue = current.get(key)
      if (normalizedValue === currentValue || (!normalizedValue && !currentValue)) {
        return current
      }
      if (normalizedValue) {
        next.set(key, normalizedValue)
      } else {
        next.delete(key)
      }
      return next
    })
  }

  function handleApplyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    updateSearchParam('as_of_date', draftAsOfDate || null)
  }

  function handleResetFilters() {
    setDraftAsOfDate('')
    updateSearchParam('as_of_date', null)
  }

  useEffect(() => {
    if (!portfolioId) {
      setHoldingsWorkspace(null)
      setAccountsWorkspace(null)
      setTaxonomyCatalog(null)
      setWorkspaceLoading(false)
      setWorkspaceError(null)
      return
    }

    let cancelled = false
    setWorkspaceLoading(true)

    Promise.all([
      getHoldingsWorkspace(portfolioId, { as_of_date: requestedAsOfDate || undefined }),
      getPortfolioAccountsWorkspace(portfolioId),
      getPortfolioTaxonomyCatalog(portfolioId),
    ])
      .then(([holdingsResponse, accountsResponse, taxonomyResponse]) => {
        if (cancelled) {
          return
        }
        setHoldingsWorkspace(holdingsResponse)
        setAccountsWorkspace(accountsResponse)
        setTaxonomyCatalog(taxonomyResponse)
        setWorkspaceError(null)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setWorkspaceError(requestError instanceof Error ? requestError.message : 'Failed to load risk workspace.')
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
  }, [portfolioId, requestedAsOfDate])

  const riskWindowEndDate = holdingsWorkspace?.as_of_date ?? ''
  const riskWindowStartDate = riskWindowEndDate ? shiftIsoDate(riskWindowEndDate, -(lookbackDays - 1)) : ''

  useEffect(() => {
    if (!portfolioId || !riskWindowEndDate) {
      setPerformanceWorkspace(null)
      setPerformanceLoading(false)
      setPerformanceError(null)
      return
    }

    let cancelled = false
    setPerformanceLoading(true)

    getPortfolioPerformance(portfolioId, {
      start_date: riskWindowStartDate || undefined,
      end_date: riskWindowEndDate || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setPerformanceWorkspace(response)
          setPerformanceError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPerformanceError(
            requestError instanceof Error ? requestError.message : 'Failed to load realized-risk window.',
          )
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
  }, [lookbackDays, portfolioId, riskWindowEndDate, riskWindowStartDate])

  const holdingsRows = holdingsWorkspace?.rows ?? []
  const sortedHoldings = useMemo(
    () => [...holdingsRows].sort((left, right) => (right.allocation ?? 0) - (left.allocation ?? 0)),
    [holdingsRows],
  )

  const assetTypeExposure = useMemo(() => {
    const buckets = new Map<string, { key: string; count: number; marketValue: number; allocation: number }>()
    holdingsRows.forEach((row) => {
      const key = row.asset_core.asset_type || 'unknown'
      const current = buckets.get(key) ?? { key, count: 0, marketValue: 0, allocation: 0 }
      current.count += 1
      current.marketValue += row.market_value_base ?? row.market_value ?? 0
      current.allocation += row.allocation ?? 0
      buckets.set(key, current)
    })
    return [...buckets.values()].sort((left, right) => right.marketValue - left.marketValue)
  }, [holdingsRows])

  const currencyExposure = useMemo(() => {
    const buckets = new Map<string, { key: string; count: number; marketValue: number; allocation: number }>()
    holdingsRows.forEach((row) => {
      const key = row.asset_core.currency || 'unknown'
      const current = buckets.get(key) ?? { key, count: 0, marketValue: 0, allocation: 0 }
      current.count += 1
      current.marketValue += row.market_value_base ?? row.market_value ?? 0
      current.allocation += row.allocation ?? 0
      buckets.set(key, current)
    })
    return [...buckets.values()].sort((left, right) => right.marketValue - left.marketValue)
  }, [holdingsRows])

  const concentrationMetrics = useMemo(() => {
    const weights = sortedHoldings.map((row) => row.allocation ?? 0)
    const hhi = weights.reduce((total, weight) => total + weight * weight, 0)
    return {
      top1: weights.slice(0, 1).reduce((total, weight) => total + weight, 0),
      top3: weights.slice(0, 3).reduce((total, weight) => total + weight, 0),
      top5: weights.slice(0, 5).reduce((total, weight) => total + weight, 0),
      hhi,
      effectiveNames: hhi > 1e-9 ? 1 / hhi : null,
      unpricedLines: holdingsRows.filter((row) => row.market_value_base == null).length,
      pricedLines: holdingsRows.filter((row) => row.market_value_base != null).length,
    }
  }, [holdingsRows, sortedHoldings])

  const accountExposureRows = useMemo(() => {
    const totalNav = holdingsWorkspace?.totals.market_value ?? 0
    return (accountsWorkspace?.accounts ?? [])
      .map((item) => {
        const cashBalance = item.derived_cash_balance ?? 0
        const cashBalanceBase = item.derived_cash_balance_base ?? cashBalance
        const positionMarketValue = item.position_market_value ?? 0
        const totalExposureBase = cashBalanceBase + positionMarketValue
        const grossExposureBase = Math.abs(cashBalanceBase) + Math.abs(positionMarketValue)
        return {
          accountId: item.account.account_id,
          accountName: item.account.account_name,
          accountType: item.account.account_type,
          currency: item.account.currency,
          cashBalance,
          cashBalanceBase,
          positionMarketValue,
          totalExposureBase,
          grossExposureBase,
          allocation: totalNav > 0 ? totalExposureBase / totalNav : null,
          positionLineCount: item.position_line_count,
        }
      })
      .sort((left, right) => right.totalExposureBase - left.totalExposureBase)
  }, [accountsWorkspace, holdingsWorkspace])

  const concentrationChartItems = useMemo(
    () =>
      sortedHoldings.slice(0, 10).map((row) => {
        const marketValue = row.market_value_base ?? row.market_value ?? 0
        const unrealizedPnl =
          row.market_value_base != null && row.cost_basis_base != null
            ? row.market_value_base - row.cost_basis_base
            : null
        return {
          id: row.line_id,
          label: row.asset_core.asset_name,
          value: row.allocation ?? 0,
          valueLabel: formatPercent(row.allocation),
          subtitle: formatLabel(row.asset_core.asset_type),
          detail: `${formatCurrency(marketValue, holdingsWorkspace?.base_currency ?? 'USD')} · ${formatSignedCurrency(
            unrealizedPnl,
            holdingsWorkspace?.base_currency ?? 'USD',
          )}`,
        }
      }),
    [holdingsWorkspace?.base_currency, sortedHoldings],
  )

  const assetTypeChartSegments = useMemo(
    () =>
      assetTypeExposure.map((bucket) => ({
        id: bucket.key,
        label: formatLabel(bucket.key),
        value: bucket.allocation,
        valueLabel: formatPercent(bucket.allocation),
        detail: `${bucket.count} lines · ${formatCurrency(bucket.marketValue, holdingsWorkspace?.base_currency ?? 'USD')}`,
      })),
    [assetTypeExposure, holdingsWorkspace?.base_currency],
  )

  const currencyChartSegments = useMemo(
    () =>
      currencyExposure.map((bucket) => ({
        id: bucket.key,
        label: bucket.key,
        value: bucket.allocation,
        valueLabel: formatPercent(bucket.allocation),
        detail: `${bucket.count} lines · ${formatCurrency(bucket.marketValue, holdingsWorkspace?.base_currency ?? 'USD')}`,
      })),
    [currencyExposure, holdingsWorkspace?.base_currency],
  )

  const accountExposureChartItems = useMemo(
    () =>
      [...accountExposureRows]
        .sort((left, right) => right.grossExposureBase - left.grossExposureBase)
        .map((row) => ({
          id: row.accountId,
          label: row.accountName,
          grossValue: row.grossExposureBase,
          cashValue: row.cashBalanceBase,
          positionValue: row.positionMarketValue,
          totalLabel: formatSignedCurrency(row.totalExposureBase, holdingsWorkspace?.base_currency ?? 'USD'),
          shareLabel: formatPercent(row.allocation),
          cashLabel: `Cash ${formatSignedCurrency(row.cashBalanceBase, holdingsWorkspace?.base_currency ?? 'USD')}`,
          positionLabel: `Positions ${formatCurrency(
            row.positionMarketValue,
            holdingsWorkspace?.base_currency ?? 'USD',
          )}`,
          detail: `${formatLabel(row.accountType)} · ${row.currency} · ${row.positionLineCount} lines`,
        })),
    [accountExposureRows, holdingsWorkspace?.base_currency],
  )

  const planningTaxonomies = taxonomyCatalog?.taxonomies.filter((taxonomy) => taxonomy.planning_enabled) ?? []
  const defaultPlanningTaxonomy =
    planningTaxonomies.find((taxonomy) => taxonomy.taxonomy_id === taxonomyCatalog?.default_planning_taxonomy_id) ?? null
  const activeDefaultScopeTargetSets =
    (taxonomyCatalog?.target_sets ?? []).filter(
      (targetSet) =>
        targetSet.taxonomy_id === defaultPlanningTaxonomy?.taxonomy_id &&
        !targetSet.comparator_taxonomy_node_id &&
        targetSet.status === 'active' &&
        isRecordActive(targetSet.effective_from, targetSet.effective_to, holdingsWorkspace?.as_of_date ?? null),
    ) ?? []
  const activeDefaultSaaTargetSet =
    activeDefaultScopeTargetSets.find((targetSet) => targetSet.target_set_type === 'saa') ?? null
  const activeDefaultTaaTargetSet =
    activeDefaultScopeTargetSets.find((targetSet) => targetSet.target_set_type === 'taa') ?? null
  const activeScopedTargetSetCount =
    (taxonomyCatalog?.target_sets ?? []).filter(
      (targetSet) =>
        targetSet.taxonomy_id === defaultPlanningTaxonomy?.taxonomy_id &&
        Boolean(targetSet.comparator_taxonomy_node_id) &&
        targetSet.status === 'active' &&
        isRecordActive(targetSet.effective_from, targetSet.effective_to, holdingsWorkspace?.as_of_date ?? null),
    ).length ?? 0

  const navChartPoints = useMemo(
    () =>
      (performanceWorkspace?.daily_series ?? [])
        .filter((point) => point.ending_nav != null)
        .map((point) => ({ date: point.as_of_date, value: point.ending_nav as number })),
    [performanceWorkspace],
  )
  const monthlyBuckets = useMemo(
    () => buildMonthlyBuckets(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )
  const recentMonthlyBuckets = useMemo(
    () => [...monthlyBuckets].reverse().slice(0, 12),
    [monthlyBuckets],
  )
  const recentDailyRows = useMemo(
    () => [...(performanceWorkspace?.daily_series ?? [])].reverse().slice(0, 20),
    [performanceWorkspace],
  )
  const worstDailyRows = useMemo(
    () =>
      [...(performanceWorkspace?.daily_series ?? [])]
        .filter((point) => point.daily_ttwror != null)
        .sort((left, right) => (left.daily_ttwror ?? 0) - (right.daily_ttwror ?? 0))
        .slice(0, 10),
    [performanceWorkspace],
  )

  const realizedRiskMetrics = useMemo(() => {
    const dailySeries = performanceWorkspace?.daily_series ?? []
    const validDailyReturns = dailySeries.filter((point) => point.daily_ttwror != null)
    const worstDay =
      [...validDailyReturns].sort((left, right) => (left.daily_ttwror ?? 0) - (right.daily_ttwror ?? 0))[0] ?? null
    const deepestDrawdown =
      [...dailySeries].filter((point) => point.drawdown != null).sort((left, right) => (left.drawdown ?? 0) - (right.drawdown ?? 0))[0] ??
      null
    return {
      observationCount: performanceWorkspace?.summary.return_observation_count ?? 0,
      staleDays: dailySeries.filter((point) => point.stale_price_flag || point.stale_fx_flag).length,
      downDays: validDailyReturns.filter((point) => (point.daily_ttwror ?? 0) < 0).length,
      worstDay,
      deepestDrawdown,
    }
  }, [performanceWorkspace])

  const currentSummaryLeft = [
    { label: 'As Of Date', value: holdingsWorkspace?.as_of_date ?? '—' },
    { label: 'Positions', value: String(holdingsRows.length || 0) },
    { label: 'Top 1 Weight', value: formatPercent(concentrationMetrics.top1) },
    { label: 'Top 3 Weight', value: formatPercent(concentrationMetrics.top3) },
    { label: 'Top 5 Weight', value: formatPercent(concentrationMetrics.top5) },
    { label: 'HHI', value: formatNumber(concentrationMetrics.hhi, 4) },
    { label: 'Effective Names', value: formatNumber(concentrationMetrics.effectiveNames, 2) },
    { label: 'Pricing Coverage', value: `${concentrationMetrics.pricedLines} / ${holdingsRows.length || 0}` },
  ]

  const currentSummaryRight = [
    { label: 'Planning Taxonomy', value: defaultPlanningTaxonomy?.name ?? 'Not configured' },
    { label: 'Root SAA', value: activeDefaultSaaTargetSet ? activeDefaultSaaTargetSet.name : 'Not configured' },
    { label: 'Root TAA', value: activeDefaultTaaTargetSet ? activeDefaultTaaTargetSet.name : 'Not configured' },
    { label: 'Scoped Target Sets', value: String(activeScopedTargetSetCount) },
    {
      label: 'Risk Budget Comparator',
      value: activeDefaultScopeTargetSets.some((targetSet) => targetSet.risk_budget_enabled) ? 'Configured' : 'Not configured',
    },
    { label: 'Planning Taxonomies', value: String(planningTaxonomies.length) },
    {
      label: 'Deposit Accounts',
      value: String(accountExposureRows.filter((row) => row.accountType === 'deposit_account').length),
    },
    { label: 'Unpriced Holdings', value: String(concentrationMetrics.unpricedLines) },
  ]

  const realizedSummaryLeft = [
    {
      label: 'Window',
      value: riskWindowEndDate ? `${riskWindowStartDate} to ${riskWindowEndDate}` : '—',
    },
    { label: 'Observations', value: String(realizedRiskMetrics.observationCount) },
    {
      label: 'Cumulative Return',
      value: signedPercent(performanceWorkspace?.summary.cumulative_ttwror),
      toneClassName: signedValueClass(performanceWorkspace?.summary.cumulative_ttwror),
    },
    { label: 'Annualized Volatility', value: signedPercent(performanceWorkspace?.summary.annualized_volatility) },
    {
      label: 'Current Drawdown',
      value: signedPercent(performanceWorkspace?.summary.current_drawdown),
      toneClassName: signedValueClass(performanceWorkspace?.summary.current_drawdown),
    },
    {
      label: 'Max Drawdown',
      value: signedPercent(performanceWorkspace?.summary.max_drawdown),
      toneClassName: signedValueClass(performanceWorkspace?.summary.max_drawdown),
    },
    { label: 'Down Days', value: String(realizedRiskMetrics.downDays) },
    { label: 'Stale Days', value: String(realizedRiskMetrics.staleDays) },
  ]

  const realizedSummaryRight = [
    { label: 'Start NAV', value: formatCurrency(performanceWorkspace?.summary.start_nav, performanceWorkspace?.base_currency ?? holdingsWorkspace?.base_currency ?? 'USD') },
    { label: 'End NAV', value: formatCurrency(performanceWorkspace?.summary.end_nav, performanceWorkspace?.base_currency ?? holdingsWorkspace?.base_currency ?? 'USD') },
    { label: 'Net External Inflow', value: formatSignedCurrency(performanceWorkspace?.summary.net_external_inflow, performanceWorkspace?.base_currency ?? holdingsWorkspace?.base_currency ?? 'USD') },
    {
      label: 'Total P&L',
      value: formatSignedCurrency(performanceWorkspace?.summary.total_pnl, performanceWorkspace?.base_currency ?? holdingsWorkspace?.base_currency ?? 'USD'),
      toneClassName: signedValueClass(performanceWorkspace?.summary.total_pnl),
    },
    {
      label: 'Worst Day',
      value: signedPercent(realizedRiskMetrics.worstDay?.daily_ttwror, 3),
      toneClassName: signedValueClass(realizedRiskMetrics.worstDay?.daily_ttwror),
    },
    { label: 'Worst Day Date', value: realizedRiskMetrics.worstDay?.as_of_date ?? '—' },
    { label: 'Deepest Drawdown Date', value: realizedRiskMetrics.deepestDrawdown?.as_of_date ?? '—' },
    { label: 'Sortino Ratio', value: formatNumber(performanceWorkspace?.summary.sortino_ratio, 2) },
  ]

  return (
    <PortfolioWorkspaceLayout
      activeSection="Risk"
      toolbarLabel={riskMode === 'realized' ? 'View: Realized Risk' : 'View: Current Risk'}
    >
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Risk</div>
        </div>

        <form className="performance-filter-bar" onSubmit={handleApplyFilters}>
          <div className="performance-filter-group">
            <label>
              <span>As Of Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={draftAsOfDate}
                onChange={(event) => setDraftAsOfDate(event.target.value)}
              />
            </label>
          </div>
          <div className="performance-filter-actions">
            <button type="submit">Apply Boundary</button>
            <button type="button" onClick={handleResetFilters}>
              Reset
            </button>
          </div>
        </form>

        <div className="holdings-detail-tabbar">
          {[
            { key: 'current', label: 'Current', meta: `${holdingsRows.length} lines` },
            { key: 'realized', label: 'Realized', meta: `${lookbackDays}D window` },
          ].map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`holdings-detail-tab ${riskMode === tab.key ? 'holdings-detail-tab-active' : ''}`}
              onClick={() => updateSearchParam('risk_tab', tab.key)}
            >
              <span className="holdings-detail-tab-label">{tab.label}</span>
              <span className="holdings-detail-tab-meta">{tab.meta}</span>
            </button>
          ))}
        </div>

        {riskMode === 'realized' ? (
          <div className="performance-inline-tabs">
            {LOOKBACK_OPTIONS.map((days) => (
              <button
                key={days}
                type="button"
                className={`performance-inline-tab ${lookbackDays === days ? 'performance-inline-tab-active' : ''}`}
                onClick={() => updateSearchParam('lookback_days', String(days))}
              >
                {days}D
              </button>
            ))}
          </div>
        ) : null}

        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}

        {workspaceLoading ? (
          <CalculationStatus label="Loading holdings, accounts, taxonomy coverage, and realized-risk window…" />
        ) : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No risk workspace is available for this portfolio.</div>
        ) : null}

        {!workspaceLoading && holdingsWorkspace && accountsWorkspace ? (
          <>
            {riskMode === 'current' ? (
              <>
                <div className="performance-summary-grid">
                  <div className="table-shell">
                    <table className="performance-summary-table">
                      <thead>
                        <tr>
                          <th colSpan={2}>Current Structure</th>
                        </tr>
                      </thead>
                      <tbody>
                        {currentSummaryLeft.map((row) => (
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
                          <th colSpan={2}>Target And Monitoring Context</th>
                        </tr>
                      </thead>
                      <tbody>
                        {currentSummaryRight.map((row) => (
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
                    <div className="panel-title">Top Concentrations</div>
                    <div className="portfolio-detail-meta">
                      Largest current weights and their unrealized contribution to concentration
                    </div>
                  </div>
                  <RiskRankedBars
                    items={concentrationChartItems}
                    ariaLabel="Top concentration holdings ranked by current portfolio weight"
                    emptyLabel="No holdings available for concentration review."
                  />
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Rank</th>
                          <th>Asset</th>
                          <th>Type</th>
                          <th>Quantity</th>
                          <th>Last Price</th>
                          <th>Market Value</th>
                          <th>Cost Basis</th>
                          <th>Unrealized P&amp;L</th>
                          <th>Weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedHoldings.length ? (
                          sortedHoldings.slice(0, 12).map((row, index) => {
                            const unrealizedPnl =
                              row.market_value_base != null && row.cost_basis_base != null
                                ? row.market_value_base - row.cost_basis_base
                                : null
                            return (
                              <tr key={row.line_id}>
                                <td>{index + 1}</td>
                                <td>{row.asset_core.asset_name}</td>
                                <td>{formatLabel(row.asset_core.asset_type)}</td>
                                <td>{formatQuantity(row.quantity)}</td>
                                <td>{formatUnitPrice(row.last_price, row.asset_core.currency)}</td>
                                <td>{formatCurrency(row.market_value_base ?? row.market_value, holdingsWorkspace.base_currency)}</td>
                                <td>{formatCurrency(row.cost_basis_base ?? row.cost_basis, holdingsWorkspace.base_currency)}</td>
                                <td className={signedValueClass(unrealizedPnl)}>
                                  {formatSignedCurrency(unrealizedPnl, holdingsWorkspace.base_currency)}
                                </td>
                                <td>{formatPercent(row.allocation)}</td>
                              </tr>
                            )
                          })
                        ) : (
                          <TableStatusRow colSpan={9} label="No holdings available for concentration review." />
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>

                <div className="performance-summary-grid">
                  <div className="table-shell">
                    <RiskExposureRibbon
                      segments={assetTypeChartSegments}
                      ariaLabel="Asset-type x-ray strip for current portfolio exposure"
                      emptyLabel="No asset-type exposure rows available."
                    />
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th colSpan={4}>Exposure By Asset Type</th>
                        </tr>
                        <tr>
                          <th>Type</th>
                          <th>Lines</th>
                          <th>Market Value</th>
                          <th>Weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        {assetTypeExposure.length ? (
                          assetTypeExposure.map((bucket) => (
                            <tr key={bucket.key}>
                              <td>{formatLabel(bucket.key)}</td>
                              <td>{bucket.count}</td>
                              <td>{formatCurrency(bucket.marketValue, holdingsWorkspace.base_currency)}</td>
                              <td>{formatPercent(bucket.allocation)}</td>
                            </tr>
                          ))
                        ) : (
                          <TableStatusRow colSpan={4} label="No asset-type exposure rows available." />
                        )}
                      </tbody>
                    </table>
                  </div>

                  <div className="table-shell">
                    <RiskExposureRibbon
                      segments={currencyChartSegments}
                      ariaLabel="Currency x-ray strip for current portfolio exposure"
                      emptyLabel="No currency exposure rows available."
                    />
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th colSpan={4}>Exposure By Currency</th>
                        </tr>
                        <tr>
                          <th>Currency</th>
                          <th>Lines</th>
                          <th>Market Value</th>
                          <th>Weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        {currencyExposure.length ? (
                          currencyExposure.map((bucket) => (
                            <tr key={bucket.key}>
                              <td>{bucket.key}</td>
                              <td>{bucket.count}</td>
                              <td>{formatCurrency(bucket.marketValue, holdingsWorkspace.base_currency)}</td>
                              <td>{formatPercent(bucket.allocation)}</td>
                            </tr>
                          ))
                        ) : (
                          <TableStatusRow colSpan={4} label="No currency exposure rows available." />
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Account And Cash Location</div>
                  </div>
                  <RiskAccountBars
                    items={accountExposureChartItems}
                    ariaLabel="Account exposure bars showing cash and positions by account"
                    emptyLabel="No account exposure rows available."
                  />
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Account</th>
                          <th>Type</th>
                          <th>Currency</th>
                          <th>Cash Balance</th>
                          <th>Position Market Value</th>
                          <th>Total Exposure</th>
                          <th>Portfolio Share</th>
                          <th>Position Lines</th>
                        </tr>
                      </thead>
                      <tbody>
                        {accountExposureRows.length ? (
                          accountExposureRows.map((row) => (
                            <tr key={row.accountId}>
                              <td>{row.accountName}</td>
                              <td>{formatLabel(row.accountType)}</td>
                              <td>{row.currency}</td>
                              <td>{formatSignedCurrency(row.cashBalance, row.currency)}</td>
                              <td>{formatCurrency(row.positionMarketValue, row.currency)}</td>
                              <td>{formatCurrency(row.totalExposureBase, holdingsWorkspace.base_currency)}</td>
                              <td>{formatPercent(row.allocation)}</td>
                              <td>{row.positionLineCount}</td>
                            </tr>
                          ))
                        ) : (
                          <TableStatusRow colSpan={8} label="No account exposure rows available." />
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Configuration Coverage</div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Module</th>
                          <th>Status</th>
                          <th>Detail</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>Taxonomy Catalog</td>
                          <td>{taxonomyCatalog?.taxonomies.length ? 'Configured' : 'Not configured'}</td>
                          <td>
                            {taxonomyCatalog?.taxonomies.length
                              ? `${taxonomyCatalog.taxonomies.length} taxonomies loaded`
                              : 'No portfolio taxonomies yet'}
                          </td>
                        </tr>
                        <tr>
                          <td>Default Planning Taxonomy</td>
                          <td>{defaultPlanningTaxonomy ? 'Configured' : 'Not configured'}</td>
                          <td>
                            {defaultPlanningTaxonomy
                              ? `${defaultPlanningTaxonomy.name} drives default drift and target context`
                              : '—'}
                          </td>
                        </tr>
                        <tr>
                          <td>Root Planning Targets</td>
                          <td>{activeDefaultSaaTargetSet ? 'Configured' : 'Not configured'}</td>
                          <td>
                            {activeDefaultSaaTargetSet
                              ? `Root scope SAA is active${activeDefaultTaaTargetSet ? '; root TAA also active' : ''}${activeScopedTargetSetCount ? `; ${activeScopedTargetSetCount} local scopes configured` : ''}`
                              : 'Root-scope target compare remains unavailable until a planning SAA is active'}
                          </td>
                        </tr>
                        <tr>
                          <td>Risk Budgets</td>
                          <td>
                            {activeDefaultScopeTargetSets.some((targetSet) => targetSet.risk_budget_enabled)
                              ? 'Configured'
                              : 'Not configured'}
                          </td>
                          <td>
                            {activeDefaultScopeTargetSets.some((targetSet) => targetSet.risk_budget_enabled)
                              ? 'The default planning taxonomy enables root-scope risk-budget comparison'
                              : 'Risk-budget gap stays unavailable until the active comparator enables risk-budget targets'}
                          </td>
                        </tr>
                        <tr>
                          <td>Pricing Coverage</td>
                          <td>{concentrationMetrics.unpricedLines ? 'Needs attention' : 'Complete'}</td>
                          <td>
                            {concentrationMetrics.unpricedLines
                              ? `${concentrationMetrics.unpricedLines} holdings are not fully priced at the selected as-of date`
                              : 'Every holding in the current statement is priced'}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            ) : (
              <>
                <div className="performance-summary-grid">
                  <div className="table-shell">
                    <table className="performance-summary-table">
                      <thead>
                        <tr>
                          <th colSpan={2}>Realized Risk Window</th>
                        </tr>
                      </thead>
                      <tbody>
                        {realizedSummaryLeft.map((row) => (
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
                          <th colSpan={2}>Return And Stress Summary</th>
                        </tr>
                      </thead>
                      <tbody>
                        {realizedSummaryRight.map((row) => (
                          <tr key={row.label}>
                            <th>{row.label}</th>
                            <td className={row.toneClassName}>{row.value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {performanceLoading && !performanceWorkspace ? (
                  <CalculationStatus label="Building realized-risk path from the recent performance window…" />
                ) : null}

                <div className="performance-block-grid">
                  <section className="performance-section-block">
                    <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                      <div className="panel-title">NAV And Drawdown Path</div>
                      <div className="portfolio-detail-meta">
                        {riskWindowEndDate ? `${lookbackDays}D realized-risk window through ${riskWindowEndDate}` : 'No active risk window'}
                      </div>
                    </div>
                    <PerformanceNavChart
                      points={navChartPoints}
                      currency={performanceWorkspace?.base_currency ?? holdingsWorkspace.base_currency}
                    />
                  </section>

                  <section className="performance-section-block">
                    <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                      <div className="panel-title">Monthly Risk Buckets</div>
                    </div>
                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th>Bucket</th>
                            <th>Coverage</th>
                            <th>Observations</th>
                            <th>Stale Days</th>
                            <th>Bucket Return</th>
                            <th>Max Drawdown</th>
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
                                <td className={signedValueClass(bucket.maxDrawdown)}>
                                  {signedPercent(bucket.maxDrawdown)}
                                </td>
                              </tr>
                            ))
                          ) : (
                            <TableStatusRow colSpan={6} label="No monthly realized-risk buckets available." />
                          )}
                        </tbody>
                      </table>
                    </div>
                  </section>
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
                              <td>{formatCurrency(point.ending_nav, performanceWorkspace?.base_currency ?? holdingsWorkspace.base_currency)}</td>
                              <td className={signedValueClass(point.daily_ttwror)}>
                                {signedPercent(point.daily_ttwror, 3)}
                              </td>
                              <td className={signedValueClass(point.drawdown)}>
                                {signedPercent(point.drawdown)}
                              </td>
                              <td className={signedValueClass(point.delta)}>
                                {formatSignedCurrency(point.delta, performanceWorkspace?.base_currency ?? holdingsWorkspace.base_currency)}
                              </td>
                              <td>{point.stale_price_flag || point.stale_fx_flag ? 'Observed' : 'Clean'}</td>
                            </tr>
                          ))
                        ) : (
                          <TableStatusRow colSpan={7} label="No negative daily-return rows are available." />
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
                              <td>{formatCurrency(point.ending_nav, performanceWorkspace?.base_currency ?? holdingsWorkspace.base_currency)}</td>
                              <td className={signedValueClass(point.daily_ttwror)}>
                                {signedPercent(point.daily_ttwror, 3)}
                              </td>
                              <td className={signedValueClass(point.cumulative_ttwror)}>
                                {signedPercent(point.cumulative_ttwror)}
                              </td>
                              <td className={signedValueClass(point.drawdown)}>
                                {signedPercent(point.drawdown)}
                              </td>
                              <td>{point.stale_price_flag ? 'Yes' : 'No'}</td>
                              <td>{point.stale_fx_flag ? 'Yes' : 'No'}</td>
                            </tr>
                          ))
                        ) : (
                          <TableStatusRow colSpan={8} label="No recent realized-risk rows available." />
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            )}
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
