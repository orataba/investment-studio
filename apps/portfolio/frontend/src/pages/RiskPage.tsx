import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  getHoldingsWorkspace,
  getPortfolioAccountsWorkspace,
  getPortfolioPerformance,
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
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
} from '../lib/format'

type RiskDetailTab = 'concentration' | 'accounts' | 'monitoring'

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
  const detailTab = (() => {
    const raw = searchParams.get('detail_tab')
    if (raw === 'accounts' || raw === 'monitoring') {
      return raw
    }
    return 'concentration'
  })() satisfies RiskDetailTab
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

  const realizedRiskMetrics = useMemo(() => {
    const dailySeries = performanceWorkspace?.daily_series ?? []
    const validDailyReturns = dailySeries.filter((point) => point.daily_ttwror != null)
    const worstPoint = validDailyReturns.reduce<(typeof validDailyReturns)[number] | null>(
      (worst, point) => {
        if (!worst || (point.daily_ttwror ?? 0) < (worst.daily_ttwror ?? 0)) {
          return point
        }
        return worst
      },
      null,
    )
    return {
      staleDays: dailySeries.filter((point) => point.stale_price_flag || point.stale_fx_flag).length,
      downDays: validDailyReturns.filter((point) => (point.daily_ttwror ?? 0) < 0).length,
      worstPoint,
    }
  }, [performanceWorkspace])

  const accountExposureRows = useMemo(() => {
    const totalNav = holdingsWorkspace?.totals.market_value ?? 0
    return (accountsWorkspace?.accounts ?? [])
      .map((item) => {
        const cashBalance = item.derived_cash_balance ?? 0
        const positionMarketValue = item.position_market_value ?? 0
        const totalExposure = cashBalance + positionMarketValue
        return {
          accountId: item.account.account_id,
          accountName: item.account.account_name,
          accountType: item.account.account_type,
          currency: item.account.currency,
          cashBalance,
          positionMarketValue,
          totalExposure,
          allocation: totalNav > 0 ? totalExposure / totalNav : null,
          positionLineCount: item.position_line_count,
        }
      })
      .sort((left, right) => right.totalExposure - left.totalExposure)
  }, [accountsWorkspace, holdingsWorkspace])

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
  const recentDailyRows = useMemo(
    () => [...(performanceWorkspace?.daily_series ?? [])].reverse().slice(0, 20),
    [performanceWorkspace],
  )

  const summaryRowsLeft = [
    { label: 'As Of Date', value: holdingsWorkspace?.as_of_date ?? '—' },
    { label: 'Position Count', value: String(holdingsRows.length || 0) },
    { label: 'Top 1 Weight', value: formatPercent(concentrationMetrics.top1) },
    { label: 'Top 3 Weight', value: formatPercent(concentrationMetrics.top3) },
    { label: 'Top 5 Weight', value: formatPercent(concentrationMetrics.top5) },
    { label: 'HHI', value: formatNumber(concentrationMetrics.hhi, 4) },
    { label: 'Effective Names', value: formatNumber(concentrationMetrics.effectiveNames, 2) },
    {
      label: 'Pricing Coverage',
      value: `${concentrationMetrics.pricedLines} / ${holdingsRows.length || 0}`,
    },
  ]

  const summaryRowsRight = [
    {
      label: `Realized Window`,
      value: riskWindowEndDate ? `${riskWindowStartDate} to ${riskWindowEndDate}` : '—',
    },
    { label: 'Current Drawdown', value: signedPercent(performanceWorkspace?.summary.current_drawdown) },
    { label: 'Max Drawdown', value: signedPercent(performanceWorkspace?.summary.max_drawdown) },
    { label: 'Annualized Volatility', value: signedPercent(performanceWorkspace?.summary.annualized_volatility) },
    { label: 'Worst Day', value: signedPercent(realizedRiskMetrics.worstPoint?.daily_ttwror, 3) },
    {
      label: 'Worst Day Date',
      value: realizedRiskMetrics.worstPoint?.as_of_date ?? '—',
    },
    { label: 'Down Days', value: String(realizedRiskMetrics.downDays) },
    { label: 'Stale Observations', value: String(realizedRiskMetrics.staleDays) },
  ]

  return (
    <PortfolioWorkspaceLayout activeSection="Risk" toolbarLabel="View: Current Risk">
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Risk</div>
          <div className="portfolio-detail-meta">Current-state concentration, realized risk, and configuration coverage</div>
        </div>

        <div className="holdings-meta-row">
          <p className="coverage-note">
            Risk is assembled from the current statement of assets plus a recent realized-risk window. Target drift and
            risk-budget blocks stay explicit about configuration state instead of pretending there is a live model.
          </p>
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

        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}

        {workspaceLoading ? (
          <CalculationStatus label="Loading holdings, accounts, and taxonomy coverage for current-state risk…" />
        ) : null}

        {!workspaceLoading && holdingsWorkspace && accountsWorkspace ? (
          <>
            <div className="performance-summary-grid">
              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Concentration And Structure</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summaryRowsLeft.map((row) => (
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
                      <th colSpan={2}>Realized Risk And Monitoring</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summaryRowsRight.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td>{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="holdings-detail-tabbar">
              {[
                { key: 'concentration', label: 'Concentration', meta: `${holdingsRows.length} holdings` },
                { key: 'accounts', label: 'Accounts & Exposure', meta: `${accountExposureRows.length} accounts` },
                { key: 'monitoring', label: 'Monitoring', meta: `${planningTaxonomies.length} planning taxonomies` },
              ].map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  className={`holdings-detail-tab ${detailTab === tab.key ? 'holdings-detail-tab-active' : ''}`}
                  onClick={() => updateSearchParam('detail_tab', tab.key)}
                >
                  <span className="holdings-detail-tab-label">{tab.label}</span>
                  <span className="holdings-detail-tab-meta">{tab.meta}</span>
                </button>
              ))}
            </div>

            {detailTab === 'concentration' ? (
              <>
                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Top Concentrations</div>
                    <div className="portfolio-detail-meta">Largest current weights and unrealized contribution to concentration</div>
                  </div>
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
                                <td className={unrealizedPnl != null && unrealizedPnl < 0 ? 'negative-cell' : ''}>
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
              </>
            ) : null}

            {detailTab === 'accounts' ? (
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Account And Cash Location</div>
                  <div className="portfolio-detail-meta">Where portfolio risk and settlement cash currently sit</div>
                </div>
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
                            <td>{formatCurrency(row.totalExposure, row.currency)}</td>
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
            ) : null}

            {detailTab === 'monitoring' ? (
              <>
                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Configuration Coverage</div>
                    <div className="portfolio-detail-meta">Risk blocks stay explicit about what is and is not configured</div>
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
                          <td>{taxonomyCatalog?.taxonomies.length ? `${taxonomyCatalog.taxonomies.length} taxonomies loaded` : 'No portfolio taxonomies yet'}</td>
                        </tr>
                        <tr>
                          <td>Default Planning Taxonomy</td>
                          <td>{defaultPlanningTaxonomy ? 'Configured' : 'Not configured'}</td>
                          <td>
                            {defaultPlanningTaxonomy
                              ? `${defaultPlanningTaxonomy.name} drives default drift and target context`
                              : 'Risk defaults remain provisional until a default planning taxonomy is selected'}
                          </td>
                        </tr>
                        <tr>
                          <td>Planning Targets</td>
                          <td>{activeDefaultSaaTargetSet ? 'Configured' : 'Not configured'}</td>
                          <td>
                            {activeDefaultSaaTargetSet
                              ? `Root scope SAA is active${activeDefaultTaaTargetSet ? '; root TAA also active' : ''}${activeScopedTargetSetCount ? `; ${activeScopedTargetSetCount} local sleeve scopes configured` : ''}`
                              : 'Target drift remains unavailable until the default planning taxonomy has an active root-scope SAA'}
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
                              ? 'The default planning taxonomy has an active risk-budget comparator at the root scope'
                              : 'Risk-budget gap remains unavailable until the active root comparator enables risk-budget targets'}
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
                        <tr>
                          <td>Recent Stale Observations</td>
                          <td>{realizedRiskMetrics.staleDays ? 'Observed' : 'Clean'}</td>
                          <td>
                            {realizedRiskMetrics.staleDays
                              ? `${realizedRiskMetrics.staleDays} recent observations used stale prices or FX`
                              : 'No stale price or FX flags in the selected realized-risk window'}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Recent Monitoring Tape</div>
                    <div className="portfolio-detail-meta">Latest 20 observations from the current realized-risk window</div>
                  </div>
                  {performanceLoading && !performanceWorkspace ? <CalculationStatus label="Loading recent realized-risk tape…" /> : null}
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Coverage</th>
                          <th>Ending NAV</th>
                          <th>Daily Return</th>
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
                              <td className={point.daily_ttwror != null && point.daily_ttwror < 0 ? 'negative-cell' : ''}>
                                {signedPercent(point.daily_ttwror, 3)}
                              </td>
                              <td className={point.drawdown != null && point.drawdown < 0 ? 'negative-cell' : ''}>
                                {signedPercent(point.drawdown)}
                              </td>
                              <td>{point.stale_price_flag ? 'Yes' : 'No'}</td>
                              <td>{point.stale_fx_flag ? 'Yes' : 'No'}</td>
                            </tr>
                          ))
                        ) : performanceLoading ? (
                          <TableStatusRow colSpan={7} label="Loading recent monitoring tape…" />
                        ) : performanceError ? (
                          <TableStatusRow colSpan={7} label={performanceError} tone="error" />
                        ) : (
                          <TableStatusRow colSpan={7} label="No recent monitoring rows available." />
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            ) : null}
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
