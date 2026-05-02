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
  type PortfolioContributionAxis,
  type PortfolioContributionReportResponse,
  type PortfolioDailyPerformancePoint,
  type PortfolioPeriodBoundaryHoldingsResponse,
  type PortfolioPerformanceCalculationResponse,
  type PortfolioPerformanceCoverageState,
  type PortfolioPerformanceResponse,
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

type PerformanceDetailTab = 'daily' | 'calculation' | 'contribution' | 'boundary'

const DEFAULT_PERFORMANCE_LOOKBACK_DAYS = 30
const DAYS_PER_YEAR = 365.25

type ContributionLine = PortfolioContributionReportResponse['lines'][number]
type ContributionSlice = PortfolioContributionReportResponse['daily_slices'][number]
type AssetContributionRow = ContributionLine & {
  periodReturn: number | null
  annualizedVolatility: number | null
  returnObservationCount: number
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

type MonthlyBucket = {
  bucket_key: string
  start_date: string
  end_date: string
  coverage_state: PortfolioPerformanceCoverageState
  observation_count: number
  start_nav: number | null
  end_nav: number | null
  net_external_inflow: number
  absolute_change: number | null
  delta: number | null
  cumulative_ttwror: number | null
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

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function sampleStddev(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((total, value) => total + value, 0) / values.length
  const variance =
    values.reduce((total, value) => total + (value - mean) * (value - mean), 0) / (values.length - 1)
  return Math.sqrt(Math.max(variance, 0))
}

function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

function annualizationPeriodsPerYear(dateKeys: string[], observationCount = dateKeys.length, startDate?: string | null) {
  const sortedDates = [...dateKeys].sort()
  if (observationCount < 1 || sortedDates.length < 2) {
    return null
  }
  if (startDate) {
    const elapsedDays = dayDiff(startDate, sortedDates[sortedDates.length - 1])
    return elapsedDays != null && elapsedDays > 0 ? (observationCount / elapsedDays) * DAYS_PER_YEAR : null
  }
  const elapsedDays = dayDiff(sortedDates[0], sortedDates[sortedDates.length - 1])
  if (elapsedDays == null) {
    return null
  }
  const gaps = sortedDates
    .slice(1)
    .map((dateKey, index) => dayDiff(sortedDates[index], dateKey))
    .filter((value): value is number => value != null && value > 0)
    .sort((left, right) => left - right)
  const medianGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 1
  const observationSpanDays = elapsedDays + medianGap
  return observationSpanDays > 0 ? (observationCount / observationSpanDays) * DAYS_PER_YEAR : null
}

function annualizedVolatility(values: number[], dateKeys: string[] = [], startDate?: string | null) {
  const stddev = sampleStddev(values)
  const periodsPerYear = annualizationPeriodsPerYear(dateKeys, values.length, startDate)
  return stddev == null || periodsPerYear == null ? null : stddev * Math.sqrt(periodsPerYear)
}

function compoundReturn(values: number[]) {
  if (!values.length) {
    return null
  }
  return values.reduce((growthIndex, value) => growthIndex * (1 + value), 1) - 1
}

function buildAssetContributionRows(lines: ContributionLine[], slices: ContributionSlice[]) {
  const slicesByGroup = new Map<string, ContributionSlice[]>()
  slices.forEach((slice) => {
    const groupSlices = slicesByGroup.get(slice.group_key) ?? []
    groupSlices.push(slice)
    slicesByGroup.set(slice.group_key, groupSlices)
  })

  return lines.map((line) => {
    const groupSlices = slicesByGroup.get(line.group_key) ?? []
    const returns = groupSlices
      .map((slice) => finiteNumber(slice.daily_return))
      .filter((value): value is number => value != null)
    const returnDates = groupSlices
      .filter((slice) => finiteNumber(slice.daily_return) != null && slice.return_observation_eligible)
      .map((slice) => slice.as_of_date)
    const riskReturns = groupSlices
      .filter((slice) => finiteNumber(slice.daily_return) != null && slice.return_observation_eligible)
      .map((slice) => slice.daily_return as number)
    const firstDate = groupSlices.reduce<string | null>(
      (current, slice) => (current == null || slice.as_of_date < current ? slice.as_of_date : current),
      null,
    )

    return {
      ...line,
      periodReturn: compoundReturn(returns),
      annualizedVolatility: annualizedVolatility(riskReturns, returnDates, firstDate),
      returnObservationCount: riskReturns.length,
    } satisfies AssetContributionRow
  })
}

function buildMonthlyBuckets(dailySeries: PortfolioDailyPerformancePoint[]) {
  const orderedKeys: string[] = []
  const buckets = new Map<
    string,
    {
      bucket_key: string
      start_date: string
      end_date: string
      coverage_state: PortfolioPerformanceCoverageState
      observation_count: number
      start_nav: number | null
      end_nav: number | null
      net_external_inflow: number
      absolute_change: number
      delta: number
      absolute_change_complete: boolean
      delta_complete: boolean
      growth_index: number
      has_return: boolean
      seen_complete: boolean
      seen_partial: boolean
      seen_unavailable: boolean
    }
  >()

  dailySeries.forEach((point) => {
    const bucketKey = point.as_of_date.slice(0, 7)
    let bucket = buckets.get(bucketKey)
    if (!bucket) {
      bucket = {
        bucket_key: bucketKey,
        start_date: point.as_of_date,
        end_date: point.as_of_date,
        coverage_state: 'unavailable',
        observation_count: 0,
        start_nav: point.beginning_nav,
        end_nav: point.ending_nav,
        net_external_inflow: 0,
        absolute_change: 0,
        delta: 0,
        absolute_change_complete: true,
        delta_complete: true,
        growth_index: 1,
        has_return: false,
        seen_complete: false,
        seen_partial: false,
        seen_unavailable: false,
      }
      buckets.set(bucketKey, bucket)
      orderedKeys.push(bucketKey)
    }

    bucket.end_date = point.as_of_date
    if (bucket.start_nav == null) {
      bucket.start_nav = point.beginning_nav
    }
    bucket.end_nav = point.ending_nav
    bucket.net_external_inflow += point.net_external_inflow

    if (point.absolute_change == null) {
      bucket.absolute_change_complete = false
    } else {
      bucket.absolute_change += point.absolute_change
    }

    if (point.delta == null) {
      bucket.delta_complete = false
    } else {
      bucket.delta += point.delta
    }

    if (point.daily_ttwror != null) {
      bucket.growth_index *= 1 + point.daily_ttwror
      bucket.has_return = true
      bucket.observation_count += 1
    }

    if (point.coverage_state === 'complete') {
      bucket.seen_complete = true
    } else if (point.coverage_state === 'partial') {
      bucket.seen_partial = true
    } else {
      bucket.seen_unavailable = true
    }
  })

  return orderedKeys.map((bucketKey) => {
    const bucket = buckets.get(bucketKey)!
    let coverageState: PortfolioPerformanceCoverageState = 'unavailable'
    if (bucket.seen_complete) {
      coverageState = bucket.seen_partial || bucket.seen_unavailable ? 'partial' : 'complete'
    } else if (bucket.seen_partial) {
      coverageState = 'partial'
    }

    return {
      bucket_key: bucket.bucket_key,
      start_date: bucket.start_date,
      end_date: bucket.end_date,
      coverage_state: coverageState,
      observation_count: bucket.observation_count,
      start_nav: bucket.start_nav,
      end_nav: bucket.end_nav,
      net_external_inflow: bucket.net_external_inflow,
      absolute_change: bucket.absolute_change_complete ? bucket.absolute_change : null,
      delta: bucket.delta_complete ? bucket.delta : null,
      cumulative_ttwror: bucket.has_return ? bucket.growth_index - 1 : null,
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

export default function PerformancePage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspace, setWorkspace] = useState<PortfolioPerformanceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [calculationWorkspace, setCalculationWorkspace] = useState<PortfolioPerformanceCalculationResponse | null>(null)
  const [calculationLoading, setCalculationLoading] = useState(false)
  const [calculationError, setCalculationError] = useState<string | null>(null)
  const [contributionWorkspace, setContributionWorkspace] = useState<PortfolioContributionReportResponse | null>(null)
  const [contributionLoading, setContributionLoading] = useState(false)
  const [contributionError, setContributionError] = useState<string | null>(null)
  const [assetContributionWorkspace, setAssetContributionWorkspace] =
    useState<PortfolioContributionReportResponse | null>(null)
  const [assetContributionLoading, setAssetContributionLoading] = useState(false)
  const [assetContributionError, setAssetContributionError] = useState<string | null>(null)
  const [boundaryWorkspace, setBoundaryWorkspace] = useState<PortfolioPeriodBoundaryHoldingsResponse | null>(null)
  const [boundaryLoading, setBoundaryLoading] = useState(false)
  const [boundaryError, setBoundaryError] = useState<string | null>(null)

  const appliedStartDate = searchParams.get('start_date') ?? ''
  const appliedEndDate = searchParams.get('end_date') ?? ''
  const defaultEndDate = useMemo(() => localDateIso(), [])
  const defaultStartDate = useMemo(
    () => shiftIsoDate(defaultEndDate, -(DEFAULT_PERFORMANCE_LOOKBACK_DAYS - 1)),
    [defaultEndDate],
  )
  const effectiveStartDate = appliedStartDate || defaultStartDate
  const effectiveEndDate = appliedEndDate || defaultEndDate
  const detailTab = (() => {
    const raw = searchParams.get('detail_tab')
    if (raw === 'calculation' || raw === 'contribution' || raw === 'boundary') {
      return raw
    }
    return 'daily'
  })() satisfies PerformanceDetailTab
  const contributionAxis = (() => {
    const raw = searchParams.get('contribution_axis')
    if (raw === 'account') {
      return raw
    }
    return 'instrument'
  })() satisfies Extract<PortfolioContributionAxis, 'instrument' | 'account'>

  const [draftStartDate, setDraftStartDate] = useState(effectiveStartDate)
  const [draftEndDate, setDraftEndDate] = useState(effectiveEndDate)

  useEffect(() => {
    setDraftStartDate(effectiveStartDate)
    setDraftEndDate(effectiveEndDate)
  }, [effectiveEndDate, effectiveStartDate])

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

  function updateWindowParams(startDate: string | null, endDate: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      const normalizedStartDate = startDate && startDate.trim() ? startDate : null
      const normalizedEndDate = endDate && endDate.trim() ? endDate : null

      if (normalizedStartDate) {
        next.set('start_date', normalizedStartDate)
      } else {
        next.delete('start_date')
      }

      if (normalizedEndDate) {
        next.set('end_date', normalizedEndDate)
      } else {
        next.delete('end_date')
      }

      return next
    })
  }

  function handleApplyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    updateWindowParams(draftStartDate || null, draftEndDate || null)
  }

  function handleResetFilters() {
    setDraftStartDate(defaultStartDate)
    setDraftEndDate(defaultEndDate)
    updateWindowParams(null, null)
  }

  useEffect(() => {
    if (!portfolioId) {
      setWorkspace(null)
      setLoading(false)
      setError(null)
      return
    }

    let cancelled = false
    setLoading(true)

    getPortfolioPerformance(portfolioId, {
      start_date: effectiveStartDate || undefined,
      end_date: effectiveEndDate || undefined,
    })
      .then((response) => {
        if (cancelled) {
          return
        }
        setWorkspace(response)
        setError(null)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : 'Failed to load performance workspace.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [effectiveEndDate, effectiveStartDate, portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setAssetContributionWorkspace(null)
      setAssetContributionLoading(false)
      setAssetContributionError(null)
      return
    }

    let cancelled = false
    setAssetContributionLoading(true)
    setAssetContributionError(null)

    getPortfolioPerformanceContribution(portfolioId, {
      start_date: effectiveStartDate || undefined,
      end_date: effectiveEndDate || undefined,
      axis: 'instrument',
    })
      .then((response) => {
        if (!cancelled) {
          setAssetContributionWorkspace(response)
          setAssetContributionError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setAssetContributionError(
            requestError instanceof Error ? requestError.message : 'Failed to load all-asset contribution.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAssetContributionLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [effectiveEndDate, effectiveStartDate, portfolioId])

  useEffect(() => {
    if (!portfolioId || detailTab !== 'calculation') {
      return
    }

    let cancelled = false
    setCalculationLoading(true)
    setCalculationError(null)

    getPortfolioPerformanceCalculation(portfolioId, {
      start_date: effectiveStartDate || undefined,
      end_date: effectiveEndDate || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setCalculationWorkspace(response)
          setCalculationError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setCalculationError(
            requestError instanceof Error ? requestError.message : 'Failed to load period calculation.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setCalculationLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [detailTab, effectiveEndDate, effectiveStartDate, portfolioId])

  useEffect(() => {
    if (!portfolioId || detailTab !== 'contribution') {
      return
    }

    let cancelled = false
    setContributionLoading(true)
    setContributionError(null)

    getPortfolioPerformanceContribution(portfolioId, {
      start_date: effectiveStartDate || undefined,
      end_date: effectiveEndDate || undefined,
      axis: contributionAxis,
    })
      .then((response) => {
        if (!cancelled) {
          setContributionWorkspace(response)
          setContributionError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setContributionError(
            requestError instanceof Error ? requestError.message : 'Failed to load contribution breakdown.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setContributionLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [contributionAxis, detailTab, effectiveEndDate, effectiveStartDate, portfolioId])

  useEffect(() => {
    if (!portfolioId || detailTab !== 'boundary') {
      return
    }

    let cancelled = false
    setBoundaryLoading(true)
    setBoundaryError(null)

    getPortfolioPerformanceBoundaryHoldings(portfolioId, {
      start_date: effectiveStartDate || undefined,
      end_date: effectiveEndDate || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setBoundaryWorkspace(response)
          setBoundaryError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setBoundaryError(
            requestError instanceof Error ? requestError.message : 'Failed to load period boundary holdings.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBoundaryLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [detailTab, effectiveEndDate, effectiveStartDate, portfolioId])

  const navChartPoints = useMemo(
    () =>
      (workspace?.daily_series ?? [])
        .filter((point) => point.ending_nav != null)
        .map((point) => ({
          date: point.as_of_date,
          value: point.ending_nav as number,
        })),
    [workspace],
  )
  const monthlyBuckets = useMemo(
    () => buildMonthlyBuckets(workspace?.daily_series ?? []),
    [workspace],
  )
  const recentMonthlyBuckets = useMemo(
    () => [...monthlyBuckets].reverse().slice(0, 12),
    [monthlyBuckets],
  )
  const recentDailyRows = useMemo(
    () => [...(workspace?.daily_series ?? [])].reverse().slice(0, 30),
    [workspace],
  )
  const rankedContributionLines = useMemo(
    () =>
      [...(contributionWorkspace?.lines ?? [])]
        .sort(
          (left, right) =>
            Math.abs(right.period_contribution ?? 0) - Math.abs(left.period_contribution ?? 0) ||
            Math.abs(right.total_pnl ?? 0) - Math.abs(left.total_pnl ?? 0),
        ),
    [contributionWorkspace],
  )
  const allAssetContributionRows = useMemo(
    () =>
      buildAssetContributionRows(
        assetContributionWorkspace?.lines ?? [],
        assetContributionWorkspace?.daily_slices ?? [],
      ),
    [assetContributionWorkspace],
  )

  const summary = workspace?.summary
  const baseCurrency = workspace?.base_currency ?? 'USD'

  const leftSummaryRows = [
    { label: 'Period', value: summary ? `${summary.start_date ?? '—'} to ${summary.end_date ?? '—'}` : '—' },
    { label: 'Coverage', value: summary ? formatLabel(summary.coverage_state) : '—' },
    { label: 'Start NAV', value: formatCurrency(summary?.start_nav, baseCurrency) },
    { label: 'End NAV', value: formatCurrency(summary?.end_nav, baseCurrency) },
    { label: 'Net External Inflow', value: formatSignedCurrency(summary?.net_external_inflow, baseCurrency) },
    {
      label: 'Absolute Change',
      value: formatSignedCurrency(summary?.absolute_change, baseCurrency),
      toneClassName: signedValueClass(summary?.absolute_change),
    },
    {
      label: 'P&L Ex Flows',
      value: formatSignedCurrency(summary?.delta, baseCurrency),
      toneClassName: signedValueClass(summary?.delta),
    },
    {
      label: 'Total P&L',
      value: formatSignedCurrency(summary?.total_pnl, baseCurrency),
      toneClassName: signedValueClass(summary?.total_pnl),
    },
  ]

  const rightSummaryRows = [
    {
      label: 'Cumulative TTWROR',
      value: signedPercent(summary?.cumulative_ttwror),
      toneClassName: signedValueClass(summary?.cumulative_ttwror),
    },
    {
      label: 'Annualized TTWROR',
      value: signedPercent(summary?.annualized_ttwror),
      toneClassName: signedValueClass(summary?.annualized_ttwror),
    },
    { label: 'IRR / MWROR', value: signedPercent(summary?.irr), toneClassName: signedValueClass(summary?.irr) },
    {
      label: 'Mean Daily Return',
      value: signedPercent(summary?.mean_daily_return, 3),
      toneClassName: signedValueClass(summary?.mean_daily_return),
    },
    { label: 'Annualized Volatility', value: signedPercent(summary?.annualized_volatility) },
    { label: 'Sharpe Ratio', value: formatNumber(summary?.sharpe_ratio, 2) },
    { label: 'Sortino Ratio', value: formatNumber(summary?.sortino_ratio, 2) },
    { label: 'Max Drawdown', value: signedPercent(summary?.max_drawdown), toneClassName: signedValueClass(summary?.max_drawdown) },
  ]

  return (
    <PortfolioWorkspaceLayout activeSection="Performance" toolbarLabel="View: Total Return">
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Performance</div>
        </div>

        <form className="performance-filter-bar" onSubmit={handleApplyFilters}>
          <div className="performance-filter-group">
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
          </div>
          <div className="performance-filter-actions">
            <button type="submit">Apply Window</button>
            <button type="button" onClick={handleResetFilters}>
              Reset
            </button>
          </div>
        </form>

        {!appliedStartDate && !appliedEndDate ? (
          <div className="inline-notice">
            Default window: trailing {DEFAULT_PERFORMANCE_LOOKBACK_DAYS} calendar days ending {effectiveEndDate}.
          </div>
        ) : null}

        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}
        {loading && workspace ? <div className="inline-notice">Refreshing derived performance window…</div> : null}

        {loading && !workspace ? (
          <CalculationStatus label="Building performance workspace from portfolio transactions and shared prices…" />
        ) : null}

        {!loading && !workspace && !error ? <div className="empty-state">No performance data is available.</div> : null}

        {workspace ? (
          <>
            <div className="performance-summary-grid">
              <div className="table-shell">
                <table className="performance-summary-table">
                  <thead>
                    <tr>
                      <th colSpan={2}>Period Summary</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leftSummaryRows.map((row) => (
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
                      <th colSpan={2}>Return And Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rightSummaryRows.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label}</th>
                        <td className={row.toneClassName}>{row.value}</td>
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
                    {summary?.latest_complete_as_of_date
                      ? `Latest complete valuation ${summary.latest_complete_as_of_date}`
                      : 'No complete valuation date'}
                  </div>
                </div>
                <PerformanceNavChart points={navChartPoints} currency={baseCurrency} />
              </section>

              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Monthly Return Table</div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Bucket</th>
                        <th>Coverage</th>
                        <th>Observations</th>
                        <th>Start NAV</th>
                        <th>End NAV</th>
                        <th>Net Flow</th>
                        <th>P&amp;L Ex Flows</th>
                        <th>Monthly TTWROR</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentMonthlyBuckets.length ? (
                        recentMonthlyBuckets.map((bucket) => (
                          <tr key={bucket.bucket_key}>
                            <td>{bucket.bucket_key}</td>
                            <td>
                              <span className={`coverage-pill ${coverageClassName(bucket.coverage_state)}`}>
                                {formatLabel(bucket.coverage_state)}
                              </span>
                            </td>
                            <td>{bucket.observation_count}</td>
                            <td>{formatCurrency(bucket.start_nav, baseCurrency)}</td>
                            <td>{formatCurrency(bucket.end_nav, baseCurrency)}</td>
                            <td>{formatSignedCurrency(bucket.net_external_inflow, baseCurrency)}</td>
                            <td className={signedValueClass(bucket.delta)}>
                              {formatSignedCurrency(bucket.delta, baseCurrency)}
                            </td>
                            <td className={signedValueClass(bucket.cumulative_ttwror)}>
                              {signedPercent(bucket.cumulative_ttwror)}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow colSpan={8} label="No monthly return buckets available." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                <div className="panel-title">All-Asset Return Contribution</div>
                <div className="portfolio-detail-meta">
                  {assetContributionWorkspace?.summary.start_date && assetContributionWorkspace.summary.end_date
                    ? `${assetContributionWorkspace.summary.start_date} to ${assetContributionWorkspace.summary.end_date}; ${allAssetContributionRows.length} assets`
                    : 'Held or traded assets inside the selected window'}
                </div>
              </div>
              {assetContributionLoading && !assetContributionWorkspace ? (
                <CalculationStatus label="Building all-asset contribution rows…" />
              ) : null}
              {assetContributionError ? (
                <div className="inline-notice inline-notice-error">{assetContributionError}</div>
              ) : null}
              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th>Asset</th>
                      <th>Start Value</th>
                      <th>End Value</th>
                      <th>Avg Weight</th>
                      <th>End Weight</th>
                      <th>Interval Return</th>
                      <th>Total P&amp;L</th>
                      <th>Contribution</th>
                    </tr>
                  </thead>
                  <tbody>
                    {allAssetContributionRows.length ? (
                      allAssetContributionRows.map((line) => (
                        <tr key={`asset-contribution:${line.group_key}`}>
                          <td>{line.group_label}</td>
                          <td>{formatCurrency(line.start_value_base, assetContributionWorkspace?.base_currency ?? baseCurrency)}</td>
                          <td>{formatCurrency(line.end_value_base, assetContributionWorkspace?.base_currency ?? baseCurrency)}</td>
                          <td>{formatPercent(line.average_weight)}</td>
                          <td>{formatPercent(line.ending_weight)}</td>
                          <td
                            className={signedValueClass(line.periodReturn)}
                            title={`${line.returnObservationCount} return observations`}
                          >
                            {signedPercent(line.periodReturn)}
                          </td>
                          <td className={signedValueClass(line.total_pnl)}>
                            {formatSignedCurrency(line.total_pnl, assetContributionWorkspace?.base_currency ?? baseCurrency)}
                          </td>
                          <td className={signedValueClass(line.period_contribution)}>
                            {signedPercent(line.period_contribution)}
                          </td>
                        </tr>
                      ))
                    ) : assetContributionLoading ? (
                      <TableStatusRow colSpan={8} label="Loading all-asset contribution rows…" />
                    ) : assetContributionError ? (
                      <TableStatusRow colSpan={8} label={assetContributionError} tone="error" />
                    ) : (
                      <TableStatusRow colSpan={8} label="No held or traded assets are available in this window." />
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <div className="holdings-detail-tabbar">
              {[
                { key: 'daily', label: 'Daily Series', meta: `${workspace.daily_series.length} rows` },
                { key: 'calculation', label: 'Calculation', meta: 'Period waterfall lines' },
                { key: 'contribution', label: 'Contribution', meta: `${contributionAxis} view` },
                { key: 'boundary', label: 'Boundary Holdings', meta: 'Start / end composition' },
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

            {detailTab === 'daily' ? (
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Daily Series</div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Coverage</th>
                        <th>Ending NAV</th>
                        <th>Net Flow</th>
                        <th>Daily Return</th>
                        <th>Cumulative Return</th>
                        <th>Drawdown</th>
                        <th>P&amp;L Ex Flows</th>
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
                            <td>{formatSignedCurrency(point.net_external_inflow, baseCurrency)}</td>
                            <td className={signedValueClass(point.daily_ttwror)}>
                              {signedPercent(point.daily_ttwror, 3)}
                            </td>
                            <td className={signedValueClass(point.cumulative_ttwror)}>
                              {signedPercent(point.cumulative_ttwror)}
                            </td>
                            <td className={signedValueClass(point.drawdown)}>
                              {signedPercent(point.drawdown)}
                            </td>
                            <td className={signedValueClass(point.delta)}>
                              {formatSignedCurrency(point.delta, baseCurrency)}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow colSpan={8} label="No daily performance rows available." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            {detailTab === 'calculation' ? (
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Period Calculation</div>
                </div>
                {calculationLoading && !calculationWorkspace ? <CalculationStatus label="Building period calculation lines…" /> : null}
                {calculationError ? <div className="inline-notice inline-notice-error">{calculationError}</div> : null}
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
                      ) : calculationLoading ? (
                        <TableStatusRow colSpan={3} label="Loading period calculation…" />
                      ) : calculationError ? (
                        <TableStatusRow colSpan={3} label={calculationError} tone="error" />
                      ) : (
                        <TableStatusRow colSpan={3} label="No period calculation lines available." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            {detailTab === 'contribution' ? (
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Contribution</div>
                </div>
                <div className="performance-inline-tabs">
                  {[
                    { key: 'instrument', label: 'Instrument' },
                    { key: 'account', label: 'Account' },
                  ].map((axis) => (
                    <button
                      key={axis.key}
                      type="button"
                      className={`performance-inline-tab ${contributionAxis === axis.key ? 'performance-inline-tab-active' : ''}`}
                      onClick={() => updateSearchParam('contribution_axis', axis.key)}
                    >
                      {axis.label}
                    </button>
                  ))}
                </div>
                {contributionLoading && !contributionWorkspace ? <CalculationStatus label="Building contribution ranking…" /> : null}
                {contributionError ? <div className="inline-notice inline-notice-error">{contributionError}</div> : null}
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Group</th>
                        <th>Avg Weight</th>
                        <th>End Weight</th>
                        <th>Realized P&amp;L</th>
                        <th>Unrealized Change</th>
                        <th>Total P&amp;L</th>
                        <th>Contribution</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rankedContributionLines.length ? (
                        rankedContributionLines.map((line) => (
                          <tr key={`${line.group_key}:${line.group_label}`}>
                            <td>{line.group_label}</td>
                            <td>{formatPercent(line.average_weight)}</td>
                            <td>{formatPercent(line.ending_weight)}</td>
                            <td className={signedValueClass(line.realized_pnl)}>
                              {formatSignedCurrency(line.realized_pnl, contributionWorkspace?.base_currency ?? baseCurrency)}
                            </td>
                            <td className={signedValueClass(line.unrealized_pnl_change)}>
                              {formatSignedCurrency(
                                line.unrealized_pnl_change,
                                contributionWorkspace?.base_currency ?? baseCurrency,
                              )}
                            </td>
                            <td className={signedValueClass(line.total_pnl)}>
                              {formatSignedCurrency(line.total_pnl, contributionWorkspace?.base_currency ?? baseCurrency)}
                            </td>
                            <td className={signedValueClass(line.period_contribution)}>
                              {signedPercent(line.period_contribution)}
                            </td>
                          </tr>
                        ))
                      ) : contributionLoading ? (
                        <TableStatusRow colSpan={7} label="Loading contribution lines…" />
                      ) : contributionError ? (
                        <TableStatusRow colSpan={7} label={contributionError} tone="error" />
                      ) : (
                        <TableStatusRow colSpan={7} label="No contribution lines available." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            {detailTab === 'boundary' ? (
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Boundary Holdings</div>
                </div>
                {boundaryLoading && !boundaryWorkspace ? <CalculationStatus label="Resolving period boundary holdings…" /> : null}
                {boundaryError ? <div className="inline-notice inline-notice-error">{boundaryError}</div> : null}
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
                        {boundaryWorkspace?.start_positions.length ? (
                          boundaryWorkspace.start_positions.map((position) => (
                            <tr key={`start:${position.position_id}`}>
                              <td>{position.instrument_ref.asset_name}</td>
                              <td>{formatQuantity(position.quantity)}</td>
                              <td>{formatCurrency(position.market_value_base, boundaryWorkspace.base_currency)}</td>
                              <td>{formatPercent(position.portfolio_weight)}</td>
                            </tr>
                          ))
                        ) : boundaryLoading ? (
                          <TableStatusRow colSpan={4} label="Loading start boundary…" />
                        ) : boundaryError ? (
                          <TableStatusRow colSpan={4} label={boundaryError} tone="error" />
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
                        {boundaryWorkspace?.end_positions.length ? (
                          boundaryWorkspace.end_positions.map((position) => (
                            <tr key={`end:${position.position_id}`}>
                              <td>{position.instrument_ref.asset_name}</td>
                              <td>{formatQuantity(position.quantity)}</td>
                              <td>{formatCurrency(position.market_value_base, boundaryWorkspace.base_currency)}</td>
                              <td>{formatPercent(position.portfolio_weight)}</td>
                            </tr>
                          ))
                        ) : boundaryLoading ? (
                          <TableStatusRow colSpan={4} label="Loading end boundary…" />
                        ) : boundaryError ? (
                          <TableStatusRow colSpan={4} label={boundaryError} tone="error" />
                        ) : (
                          <TableStatusRow colSpan={4} label="No end-boundary positions available." />
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>
            ) : null}
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
