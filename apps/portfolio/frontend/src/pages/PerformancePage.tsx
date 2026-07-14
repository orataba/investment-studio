import { useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  clearPortfolioApiCache,
  getPortfolioPerformanceReport,
  type PortfolioDailyPublishedAttributionAxis,
  type PortfolioDailyPublishedCalendarFrequency,
  type PortfolioDailyPublishedDecimalMetric,
  type PortfolioDailyPublishedPerformanceReportResponse,
  type PortfolioDailyPublishedReturnCalendarBucket,
} from '../lib/api'
import { exactDecimalToDisplayNumber } from '../lib/exactDecimal'
import {
  formatCurrency,
  formatLabel,
  formatPercent,
  formatSignedCurrency,
  signedValueClass,
} from '../lib/format'
import {
  buildReturnCalendarMatrixRows,
  RETURN_CALENDAR_MONTH_LABELS,
} from '../lib/returnCalendarPresentation'

const DAILY_PAGE_SIZE = 100
const AXES: Array<{ value: PortfolioDailyPublishedAttributionAxis; label: string }> = [
  { value: 'instrument', label: 'Instrument' },
  { value: 'account', label: 'Account' },
  { value: 'currency', label: 'Currency' },
  { value: 'taxonomy', label: 'Taxonomy' },
]
const FREQUENCIES: Array<{
  value: PortfolioDailyPublishedCalendarFrequency
  label: string
}> = [
  { value: 'monthly', label: 'Monthly' },
  { value: 'weekly', label: 'Weekly' },
]

type MetricRow = {
  label: string
  value: string
  sourceValue?: string | null
  detail?: string
  tone?: string
}

function exactNumber(value: string | null | undefined) {
  return value == null ? null : exactDecimalToDisplayNumber(value)
}

function signedPercent(value: string | null | undefined, digits = 2) {
  const resolved = exactNumber(value)
  if (resolved == null) {
    return '—'
  }
  const rendered = formatPercent(Math.abs(resolved), digits)
  return resolved > 0 ? `+${rendered}` : resolved < 0 ? `-${rendered}` : rendered
}

function unsignedPercent(value: string | null | undefined, digits = 2) {
  return formatPercent(exactNumber(value), digits)
}

function metricDetail(metric: PortfolioDailyPublishedDecimalMetric | null) {
  if (metric == null) {
    return undefined
  }
  return `Method50 ${metric.method50}; published ${metric.published}; rounding adjustment ${metric.rounding_adjustment_exact}`
}

function reasonText(reasonCodes: readonly string[]) {
  return reasonCodes.length ? reasonCodes.map(formatLabel).join(' · ') : 'None'
}

function MetricTable({ title, rows }: { title: string; rows: MetricRow[] }) {
  return (
    <div className="table-shell">
      <table className="transactions-table performance-summary-table">
        <thead>
          <tr>
            <th colSpan={2}>{title}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} title={row.detail ?? row.sourceValue ?? undefined}>
              <th scope="row">{row.label}</th>
              <td className={row.tone}>{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ReturnCalendar({
  buckets,
  frequency,
}: {
  buckets: PortfolioDailyPublishedReturnCalendarBucket[]
  frequency: PortfolioDailyPublishedCalendarFrequency
}) {
  const monthlyRows = useMemo(
    () => buildReturnCalendarMatrixRows(frequency === 'monthly' ? buckets : []),
    [buckets, frequency],
  )
  if (!buckets.length) {
    return <div className="empty-state">No published return buckets for this window.</div>
  }
  if (frequency === 'weekly') {
    return (
      <div className="table-shell">
        <table className="transactions-table">
          <thead>
            <tr>
              <th>ISO Week</th>
              <th>Effective Period</th>
              <th className="performance-cell-number">Observations</th>
              <th className="performance-cell-number">TWR</th>
              <th className="performance-cell-number">Max Drawdown</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((bucket) => (
              <tr
                key={bucket.bucket_key}
                title={reasonText([...bucket.coverage_reason_codes, ...bucket.reason_codes])}
              >
                <th scope="row">{bucket.bucket_key}</th>
                <td>
                  {bucket.effective_return_start_date ?? '—'} –{' '}
                  {bucket.effective_return_end_date ?? '—'}
                </td>
                <td className="performance-cell-number">{bucket.observation_count}</td>
                <td className={`performance-cell-number ${signedValueClass(bucket.cumulative_twr?.method50)}`}>
                  {signedPercent(bucket.cumulative_twr?.method50)}
                </td>
                <td className="performance-cell-number negative-cell">
                  {signedPercent(bucket.max_drawdown?.method50)}
                </td>
                <td>{formatLabel(bucket.status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  return (
    <div className="table-shell">
      <table className="transactions-table return-calendar-table">
        <thead>
          <tr>
            <th>Year</th>
            {RETURN_CALENDAR_MONTH_LABELS.map((month) => (
              <th className="performance-cell-number" key={month}>{month}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {monthlyRows.map((row) => (
            <tr key={row.year}>
              <th scope="row">{row.year}</th>
              {row.months.map((bucket, monthIndex) => (
                <td
                  className={`performance-cell-number ${signedValueClass(bucket?.cumulative_twr?.method50)}`}
                  key={`${row.year}:${monthIndex}`}
                  title={
                    bucket
                      ? reasonText([...bucket.coverage_reason_codes, ...bucket.reason_codes])
                      : undefined
                  }
                >
                  {bucket ? signedPercent(bucket.cumulative_twr?.method50) : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function resolvedAxis(value: string | null): PortfolioDailyPublishedAttributionAxis {
  return AXES.some((option) => option.value === value)
    ? (value as PortfolioDailyPublishedAttributionAxis)
    : 'instrument'
}

function resolvedFrequency(value: string | null): PortfolioDailyPublishedCalendarFrequency {
  return value === 'weekly' ? 'weekly' : 'monthly'
}

export default function PerformancePage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const startDate = searchParams.get('start_date') ?? ''
  const endDate = searchParams.get('end_date') ?? ''
  const axis = resolvedAxis(searchParams.get('axis'))
  const frequency = resolvedFrequency(searchParams.get('frequency'))
  const [report, setReport] = useState<PortfolioDailyPublishedPerformanceReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [dailyPage, setDailyPage] = useState(0)

  const invalidWindow = Boolean(startDate && endDate && startDate > endDate)

  useEffect(() => {
    if (!portfolioId) {
      setReport(null)
      setError('Portfolio id is required.')
      setLoading(false)
      return
    }
    if (invalidWindow) {
      setReport(null)
      setError('Start date must not be later than end date.')
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    getPortfolioPerformanceReport(portfolioId, {
      start_date: startDate || undefined,
      end_date: endDate || undefined,
      axis,
      frequency,
    })
      .then((response) => {
        if (!cancelled) {
          setReport(response)
          setDailyPage(0)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setReport(null)
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load the published performance report.',
          )
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
  }, [axis, endDate, frequency, invalidWindow, portfolioId, refreshVersion, startDate])

  function updateParameter(name: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) {
      next.set(name, value)
    } else {
      next.delete(name)
    }
    setSearchParams(next, { replace: true })
  }

  const performance = report?.performance ?? null
  const bridge = report?.portfolio_bridge ?? null
  const statistics = report?.statistics ?? null
  const xirr = report?.xirr ?? null
  const navPoints = useMemo(
    () =>
      (report?.daily_series ?? []).map((point) => ({
        date: point.as_of_date,
        value: exactNumber(point.closing_nav),
      })),
    [report],
  )
  const wealthPoints = useMemo(
    () =>
      (report?.rebased_wealth_series ?? []).map((point) => ({
        date: point.as_of_date,
        value: exactNumber(point.wealth_index_method50),
      })),
    [report],
  )
  const drawdownPoints = useMemo(
    () =>
      (report?.rebased_wealth_series ?? []).map((point) => ({
        date: point.as_of_date,
        value: exactNumber(point.drawdown_method50),
      })),
    [report],
  )
  const dailyPageCount = Math.max(
    1,
    Math.ceil((report?.daily_series.length ?? 0) / DAILY_PAGE_SIZE),
  )
  const visibleDailyRows = useMemo(
    () =>
      (report?.daily_series ?? []).slice(
        dailyPage * DAILY_PAGE_SIZE,
        (dailyPage + 1) * DAILY_PAGE_SIZE,
      ),
    [dailyPage, report],
  )
  const attributionCalendarRows = useMemo(
    () =>
      (report?.attribution_calendar ?? []).flatMap((bucket) =>
        bucket.groups.map((group) => ({ bucket, group })),
      ),
    [report],
  )
  const allReasonCodes = useMemo(
    () =>
      Array.from(
        new Set([
          ...(report?.publication.reason_codes ?? []),
          ...(performance?.reason_codes ?? []),
          ...(statistics?.reason_codes ?? []),
          ...(xirr?.reason_codes ?? []),
          ...(report?.attribution.reason_codes ?? []),
        ]),
      ),
    [performance, report, statistics, xirr],
  )

  const returnRows: MetricRow[] = [
    {
      label: 'Reported-period TWR',
      value: signedPercent(performance?.cumulative_twr?.method50),
      sourceValue: performance?.cumulative_twr?.method50,
      detail: metricDetail(performance?.cumulative_twr ?? null),
      tone: signedValueClass(performance?.cumulative_twr?.method50),
    },
    {
      label: 'Annualized TWR',
      value: signedPercent(performance?.annualized_twr?.method50),
      sourceValue: performance?.annualized_twr?.method50,
      detail: metricDetail(performance?.annualized_twr ?? null),
      tone: signedValueClass(performance?.annualized_twr?.method50),
    },
    {
      label: 'XIRR / money-weighted return',
      value:
        xirr?.status !== 'ready'
          ? 'N/A'
          : xirr.annualized_headline_eligible
            ? signedPercent(xirr.rate?.method50)
            : 'N/A (<365 days)',
      sourceValue: xirr?.rate?.method50,
      detail: metricDetail(xirr?.rate ?? null),
      tone: xirr?.annualized_headline_eligible
        ? signedValueClass(xirr.rate?.method50)
        : undefined,
    },
    {
      label: 'Current drawdown',
      value: signedPercent(performance?.current_drawdown?.method50),
      sourceValue: performance?.current_drawdown?.method50,
      detail: metricDetail(performance?.current_drawdown ?? null),
      tone: 'negative-cell',
    },
    {
      label: 'Maximum drawdown',
      value: signedPercent(performance?.max_drawdown?.method50),
      sourceValue: performance?.max_drawdown?.method50,
      detail: metricDetail(performance?.max_drawdown ?? null),
      tone: 'negative-cell',
    },
  ]
  const riskRows: MetricRow[] = [
    {
      label: 'Annualized volatility',
      value: unsignedPercent(statistics?.annualized_volatility?.method50),
      sourceValue: statistics?.annualized_volatility?.method50,
      detail: metricDetail(statistics?.annualized_volatility ?? null),
    },
    {
      label: 'Annualized downside deviation',
      value: unsignedPercent(statistics?.annualized_downside_deviation?.method50),
      sourceValue: statistics?.annualized_downside_deviation?.method50,
      detail: metricDetail(statistics?.annualized_downside_deviation ?? null),
    },
    {
      label: 'Mean interval return',
      value: signedPercent(statistics?.mean_period_return?.method50, 4),
      sourceValue: statistics?.mean_period_return?.method50,
      detail: metricDetail(statistics?.mean_period_return ?? null),
      tone: signedValueClass(statistics?.mean_period_return?.method50),
    },
    {
      label: 'Frequency / observations',
      value: `${formatLabel(statistics?.frequency ?? frequency)} / ${statistics?.observation_count ?? 0}`,
      detail: statistics?.method_version,
    },
    {
      label: 'XNPV residual (exact)',
      value: xirr?.xnpv_residual_exact ?? '—',
      sourceValue: xirr?.xnpv_residual_exact,
      detail: xirr?.method_version,
    },
  ]
  const bridgeStartDate = performance?.effective_return_start_date ?? '—'
  const bridgeEndDate = performance?.effective_return_end_date ?? '—'
  const bridgePeriodDetail = `Measured return period ${bridgeStartDate} – ${bridgeEndDate}; this bridge is not the latest selected-date NAV.`
  const bridgeRows: MetricRow[] = [
    {
      label: `Opening NAV (${bridgeStartDate})`,
      value: formatCurrency(bridge?.opening_nav_exact, report?.base_currency),
      sourceValue: bridge?.opening_nav_exact,
      detail: bridgePeriodDetail,
    },
    {
      label: `Closing NAV (${bridgeEndDate})`,
      value: formatCurrency(bridge?.closing_nav_exact, report?.base_currency),
      sourceValue: bridge?.closing_nav_exact,
      detail: bridgePeriodDetail,
    },
    {
      label: 'External flow in',
      value: formatCurrency(bridge?.external_flow_in_exact, report?.base_currency),
      sourceValue: bridge?.external_flow_in_exact,
    },
    {
      label: 'External flow out',
      value: formatCurrency(bridge?.external_flow_out_exact, report?.base_currency),
      sourceValue: bridge?.external_flow_out_exact,
    },
    {
      label: 'Economic P&L',
      value: formatSignedCurrency(bridge?.economic_pnl_exact, report?.base_currency),
      sourceValue: bridge?.economic_pnl_exact,
      tone: signedValueClass(bridge?.economic_pnl_exact),
      detail: bridgePeriodDetail,
    },
    {
      label: 'Bridge closure residual',
      value: bridge?.closure_residual_exact ?? '—',
      sourceValue: bridge?.closure_residual_exact,
    },
  ]

  return (
    <PortfolioWorkspaceLayout activeSection="Performance" toolbarLabel="View: Published Performance">
      <section className="portfolio-detail-surface performance-surface">
        <div className="transaction-filter-bar performance-window-bar">
          <div className="performance-filter-group performance-window-group">
            <label>
              <span>Start Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={startDate}
                onChange={(event) => updateParameter('start_date', event.target.value)}
              />
            </label>
            <label>
              <span>End Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={endDate}
                onChange={(event) => updateParameter('end_date', event.target.value)}
              />
            </label>
            <label>
              <span>Attribution</span>
              <select
                className="transaction-filter-input"
                value={axis}
                onChange={(event) => updateParameter('axis', event.target.value)}
              >
                {AXES.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Calendar</span>
              <select
                className="transaction-filter-input"
                value={frequency}
                onChange={(event) => updateParameter('frequency', event.target.value)}
              >
                {FREQUENCIES.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="transaction-filter-actions">
            <button
              type="button"
              onClick={() => {
                const next = new URLSearchParams(searchParams)
                next.delete('start_date')
                next.delete('end_date')
                setSearchParams(next, { replace: true })
              }}
            >
              Full Published Range
            </button>
            <button
              type="button"
              onClick={() => {
                clearPortfolioApiCache()
                setRefreshVersion((value) => value + 1)
              }}
            >
              Refresh
            </button>
          </div>
        </div>

        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}
        {loading ? <CalculationStatus /> : null}
        {report?.publication.stale ? (
          <div className="inline-notice inline-notice-warning">
            This immutable publication is stale relative to generation {report.publication.current_generation}.
          </div>
        ) : null}
        {report?.publication.pending ? (
          <div className="inline-notice inline-notice-warning">
            A newer generation is pending; this page remains pinned to publication {report.publication.publication_id}.
          </div>
        ) : null}
        {!loading && !report && !error ? <div className="empty-state">No published performance data.</div> : null}

        {report && performance ? (
          <div className="performance-section-stack">
            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Published Return Series</div>
                  <div className="portfolio-detail-meta">
                    {performance.effective_return_start_date ?? '—'} – {performance.effective_return_end_date ?? '—'} ·{' '}
                    {performance.measured_return_count}/{performance.snapshot_count} measured · {report.base_currency}
                  </div>
                </div>
              </div>
              <PerformanceNavChart
                points={navPoints}
                twrPoints={wealthPoints}
                drawdownPoints={drawdownPoints}
                currency={report.base_currency}
                summary={{
                  start_date: performance.start_date,
                  end_date: performance.end_date,
                  cumulative_twr: exactNumber(performance.cumulative_twr?.method50),
                  absolute_change: exactNumber(bridge?.economic_pnl_exact),
                  current_drawdown: exactNumber(performance.current_drawdown?.method50),
                  max_drawdown: exactNumber(performance.max_drawdown?.method50),
                }}
                showRangeControls
              />
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Performance Measures</div>
                  <div className="portfolio-detail-meta">
                    Decimal50 method values; 18-place publication rounding and adjustments are exposed in tooltips.
                  </div>
                </div>
              </div>
              <div className="performance-summary-grid">
                <MetricTable title="Return & Drawdown" rows={returnRows} />
                <MetricTable title="Calendar-period Statistics" rows={riskRows} />
                <MetricTable title="Monetary Bridge" rows={bridgeRows} />
                <MetricTable
                  title="Coverage & Lineage"
                  rows={[
                    { label: 'Performance status', value: formatLabel(performance.status) },
                    { label: 'Coverage', value: formatLabel(performance.coverage_state) },
                    { label: 'Return chain', value: formatLabel(performance.return_chain_status ?? 'unavailable') },
                    { label: 'Publication', value: report.publication.publication_id },
                    { label: 'Methodology', value: report.publication.methodology_version },
                    { label: 'Reasons', value: reasonText(allReasonCodes) },
                  ]}
                />
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Frongello-linked Attribution</div>
                  <div className="portfolio-detail-meta">
                    {formatLabel(report.attribution.axis)} · {report.attribution.observation_count} daily observations ·{' '}
                    {report.attribution.method_version}
                  </div>
                </div>
              </div>
              <div className="table-shell">
                <table className="transactions-table performance-calculation-table">
                  <thead>
                    <tr>
                      <th>Group</th>
                      <th className="performance-cell-number">Opening NAV</th>
                      <th className="performance-cell-number">Closing NAV</th>
                      <th className="performance-cell-number">External In</th>
                      <th className="performance-cell-number">External Out</th>
                      <th className="performance-cell-number">Internal In</th>
                      <th className="performance-cell-number">Internal Out</th>
                      <th className="performance-cell-number">Economic P&amp;L</th>
                      <th className="performance-cell-number">Linked Contribution</th>
                      <th className="performance-cell-number">Closure</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.attribution_groups.length ? report.attribution_groups.map((group) => (
                      <tr key={`${group.axis}:${group.group_key}`}>
                        <th scope="row">{group.group_label}</th>
                        <td className="performance-cell-number" title={group.opening_nav_exact}>{formatCurrency(group.opening_nav_exact, report.base_currency)}</td>
                        <td className="performance-cell-number" title={group.closing_nav_exact}>{formatCurrency(group.closing_nav_exact, report.base_currency)}</td>
                        <td className="performance-cell-number" title={group.external_flow_in_exact}>{formatCurrency(group.external_flow_in_exact, report.base_currency)}</td>
                        <td className="performance-cell-number" title={group.external_flow_out_exact}>{formatCurrency(group.external_flow_out_exact, report.base_currency)}</td>
                        <td className="performance-cell-number" title={group.internal_flow_in_exact}>{formatCurrency(group.internal_flow_in_exact, report.base_currency)}</td>
                        <td className="performance-cell-number" title={group.internal_flow_out_exact}>{formatCurrency(group.internal_flow_out_exact, report.base_currency)}</td>
                        <td className={`performance-cell-number ${signedValueClass(group.economic_pnl_exact)}`} title={group.economic_pnl_exact}>{formatSignedCurrency(group.economic_pnl_exact, report.base_currency)}</td>
                        <td className={`performance-cell-number ${signedValueClass(group.linked_contribution_effective)}`} title={`Method50 ${group.linked_contribution_method50}; balancing adjustment ${group.linking_adjustment_exact}`}>{signedPercent(group.linked_contribution_effective, 4)}</td>
                        <td className="performance-cell-number" title={group.closure_residual_exact}>{group.closure_residual_exact}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan={10} className="empty-state-cell">{reasonText(report.attribution.reason_codes)}</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Return Calendar</div>
                  <div className="portfolio-detail-meta">Backend-linked {frequency} buckets use endpoint-owned valuation subperiods; coverage is disclosed explicitly.</div>
                </div>
              </div>
              <ReturnCalendar buckets={report.return_calendar} frequency={frequency} />
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Attribution Calendar</div>
                  <div className="portfolio-detail-meta">Decimal50 linked contributions plus explicit balancing by {formatLabel(axis)} and {frequency} bucket.</div>
                </div>
              </div>
              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th>Bucket</th>
                      <th>Group</th>
                      <th className="performance-cell-number">Economic P&amp;L</th>
                      <th className="performance-cell-number">Linked Contribution</th>
                      <th className="performance-cell-number">Closure</th>
                    </tr>
                  </thead>
                  <tbody>
                    {attributionCalendarRows.length ? attributionCalendarRows.map(({ bucket, group }) => (
                      <tr key={`${bucket.bucket_key}:${group.group_key}`} title={reasonText(bucket.summary.reason_codes)}>
                        <th scope="row">{bucket.bucket_key}</th>
                        <td>{group.group_label}</td>
                        <td className={`performance-cell-number ${signedValueClass(group.economic_pnl_exact)}`}>{formatSignedCurrency(group.economic_pnl_exact, report.base_currency)}</td>
                        <td className={`performance-cell-number ${signedValueClass(group.linked_contribution_effective)}`} title={`Method50 ${group.linked_contribution_method50}; balancing adjustment ${group.linking_adjustment_exact}`}>{signedPercent(group.linked_contribution_effective, 4)}</td>
                        <td className="performance-cell-number">{group.closure_residual_exact}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan={5} className="empty-state-cell">No measured attribution buckets.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Daily Published Audit Trail</div>
                  <div className="portfolio-detail-meta">
                    Page {dailyPage + 1}/{dailyPageCount} · canonical values from publication {report.publication.publication_id}
                  </div>
                </div>
                <div className="transaction-filter-actions">
                  <button type="button" disabled={dailyPage === 0} onClick={() => setDailyPage((value) => Math.max(0, value - 1))}>Previous</button>
                  <button type="button" disabled={dailyPage + 1 >= dailyPageCount} onClick={() => setDailyPage((value) => Math.min(dailyPageCount - 1, value + 1))}>Next</button>
                </div>
              </div>
              <div className="table-shell">
                <table className="transactions-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th className="performance-cell-number">Opening NAV</th>
                      <th className="performance-cell-number">Closing NAV</th>
                      <th className="performance-cell-number">Flow In</th>
                      <th className="performance-cell-number">Flow Out</th>
                      <th className="performance-cell-number">Economic P&amp;L</th>
                      <th className="performance-cell-number">Subperiod TWR</th>
                      <th className="performance-cell-number">Cumulative TWR</th>
                      <th className="performance-cell-number">Drawdown</th>
                      <th>Coverage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleDailyRows.map((row) => (
                      <tr key={row.as_of_date}>
                        <th scope="row">{row.as_of_date}</th>
                        <td className="performance-cell-number" title={row.opening_nav ?? undefined}>{formatCurrency(row.opening_nav, report.base_currency)}</td>
                        <td className="performance-cell-number" title={row.closing_nav ?? undefined}>{formatCurrency(row.closing_nav, report.base_currency)}</td>
                        <td className="performance-cell-number" title={row.external_flow_in ?? undefined}>{formatCurrency(row.external_flow_in, report.base_currency)}</td>
                        <td className="performance-cell-number" title={row.external_flow_out ?? undefined}>{formatCurrency(row.external_flow_out, report.base_currency)}</td>
                        <td className={`performance-cell-number ${signedValueClass(row.economic_pnl)}`} title={row.economic_pnl ?? undefined}>{formatSignedCurrency(row.economic_pnl, report.base_currency)}</td>
                        <td className={`performance-cell-number ${signedValueClass(row.subperiod_twr_method50)}`} title={row.subperiod_twr_method50 ?? undefined}>{signedPercent(row.subperiod_twr_method50, 4)}</td>
                        <td className={`performance-cell-number ${signedValueClass(row.cumulative_twr_method50)}`} title={row.cumulative_twr_method50 ?? undefined}>{signedPercent(row.cumulative_twr_method50, 4)}</td>
                        <td className="performance-cell-number negative-cell" title={row.drawdown_method50 ?? undefined}>{signedPercent(row.drawdown_method50, 4)}</td>
                        <td title={reasonText([...row.nav_reason_codes, ...row.return_reason_codes])}>{formatLabel(row.return_coverage_state)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
