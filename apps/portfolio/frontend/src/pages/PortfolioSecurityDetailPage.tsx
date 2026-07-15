import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import InstrumentPriceChart from '../components/InstrumentPriceChart'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
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
import {
  getPortfolioInstrumentHoldingProjection,
  getPortfolioInstrumentPriceChart,
  getPortfolioPositionLots,
  getPortfolioTransactions,
  type PortfolioInstrumentChartRangeKey,
  type PortfolioInstrumentHoldingRow,
  type PortfolioInstrumentHoldingProjectionResponse,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioPositionLotListResponse,
  type PortfolioPositionLotRecord,
  type PortfolioTransactionListResponse,
  type PortfolioTransactionRecord,
} from '../lib/api'
import {
  performanceSeriesLabel,
  valuationQuoteLabel,
} from '../lib/instrumentMetricLabels'
import { aggregatePositionLotAccountSlices } from '../lib/positionLotAggregation'
import {
  buildPortfolioSectionPath,
  buildWatchlistInstrumentDetailUrl,
} from '../lib/navigation'

type SecurityDetailTab = 'overview' | 'transactions' | 'lots' | 'realizations'

function primaryIdentifier(row: PortfolioInstrumentHoldingRow) {
  return (
    row.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.instrument_core.identifiers[0]?.identifier_value ??
    row.instrument_core.instrument_id
  )
}

function parseChartRange(value: string | null): PortfolioInstrumentChartRangeKey {
  if (value === '1m' || value === '3m' || value === '6m' || value === 'ytd' || value === '1y' || value === 'all') {
    return value
  }
  return '1y'
}

function parseDetailTab(value: string | null): SecurityDetailTab {
  if (value === 'transactions' || value === 'lots' || value === 'realizations') {
    return value
  }
  return 'overview'
}

function transactionAccountLabel(transaction: PortfolioTransactionRecord) {
  return transaction.account.account_name
}

function lotStatusClass(positionLot: PortfolioPositionLotRecord) {
  return positionLot.status === 'open' ? 'coverage-pill-live' : 'coverage-pill-warning'
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

export default function PortfolioSecurityDetailPage() {
  const { portfolioId = '', instrumentId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspaceResponse, setWorkspace] = useState<PortfolioInstrumentHoldingProjectionResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [positionLotsResponse, setPositionLotsWorkspace] = useState<PortfolioPositionLotListResponse | null>(null)
  const [positionLotsLoading, setPositionLotsLoading] = useState(false)
  const [positionLotsError, setPositionLotsError] = useState<string | null>(null)
  const [transactionsResponse, setTransactionsWorkspace] = useState<PortfolioTransactionListResponse | null>(null)
  const [transactionsLoading, setTransactionsLoading] = useState(false)
  const [transactionsError, setTransactionsError] = useState<string | null>(null)
  const [instrumentChartResponse, setInstrumentChartWorkspace] = useState<PortfolioInstrumentPriceChartResponse | null>(null)
  const [instrumentChartLoading, setInstrumentChartLoading] = useState(false)
  const [instrumentChartError, setInstrumentChartError] = useState<string | null>(null)

  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const selectedPositionLotId = searchParams.get('position_lot_id')
  const detailTab = parseDetailTab(searchParams.get('detail_tab'))
  const chartRangeKey = parseChartRange(searchParams.get('chart_range'))
  const workspace =
    workspaceResponse?.portfolio_id === portfolioId &&
    (!workspaceResponse.row ||
      workspaceResponse.row.instrument_core.instrument_id === instrumentId) &&
    (!requestedAsOfDate || workspaceResponse.as_of_date === requestedAsOfDate)
      ? workspaceResponse
      : null
  const positionLotsWorkspace =
    positionLotsResponse?.portfolio_id === portfolioId &&
    positionLotsResponse.position_lots.every((positionLot) => positionLot.instrument_id === instrumentId)
      ? positionLotsResponse
      : null
  const transactionsWorkspace =
    transactionsResponse?.portfolio_id === portfolioId &&
    transactionsResponse.transactions.every((transaction) => transaction.instrument_id === instrumentId)
      ? transactionsResponse
      : null
  const selectedRow = workspace?.row ?? null
  const selectedPositionLots = positionLotsWorkspace?.position_lots ?? []
  const selectedTransactions = transactionsWorkspace?.transactions ?? []
  const selectedPositionLot =
    selectedPositionLots.find((positionLot) => positionLot.position_lot_id === selectedPositionLotId) ??
    selectedPositionLots[0] ??
    null
  const resolvedAsOfDate = workspace?.as_of_date || requestedAsOfDate
  const instrumentChartWorkspace =
    instrumentChartResponse?.portfolio_id === portfolioId &&
    instrumentChartResponse.instrument_core.instrument_id === instrumentId &&
    instrumentChartResponse.range_key === chartRangeKey &&
    (!resolvedAsOfDate || instrumentChartResponse.as_of_date === resolvedAsOfDate)
      ? instrumentChartResponse
      : null
  const baseCurrency = workspace?.base_currency ?? selectedRow?.instrument_core.currency ?? instrumentChartWorkspace?.currency ?? 'USD'
  const selectedRowIdentifier = selectedRow ? primaryIdentifier(selectedRow) : instrumentId
  const selectedRowUnrealizedBase =
    selectedRow?.market_value_base != null && selectedRow.cost_basis_base != null
      ? selectedRow.market_value_base - selectedRow.cost_basis_base
      : null
  const selectedRowUnrealizedLocal =
    selectedRow?.market_value != null && selectedRow.cost_basis != null
      ? selectedRow.market_value - selectedRow.cost_basis
      : null
  const heroMarketValue = selectedRow?.market_value_base ?? selectedRow?.market_value
  const heroMarketCurrency = selectedRow?.market_value_base != null ? baseCurrency : selectedRow?.instrument_core.currency ?? baseCurrency
  const heroUnrealizedValue = selectedRowUnrealizedBase ?? selectedRowUnrealizedLocal
  const heroUnrealizedCurrency = selectedRowUnrealizedBase != null ? baseCurrency : selectedRow?.instrument_core.currency ?? baseCurrency
  const valuationMetricName = valuationQuoteLabel(
    selectedRow?.quote_basis ?? selectedRow?.quote_metric_family,
  )
  const performanceMetricName = performanceSeriesLabel(
    instrumentChartWorkspace?.chart_basis ?? instrumentChartWorkspace?.metric_family,
  )
  const latestPerformancePoint = instrumentChartWorkspace?.points[instrumentChartWorkspace.points.length - 1] ?? null
  const positionLotsPending =
    workspaceLoading ||
    positionLotsLoading ||
    Boolean(resolvedAsOfDate && !positionLotsWorkspace && !positionLotsError)
  const transactionsPending =
    workspaceLoading ||
    transactionsLoading ||
    Boolean(resolvedAsOfDate && !transactionsWorkspace && !transactionsError)
  const instrumentChartPending =
    workspaceLoading ||
    instrumentChartLoading ||
    Boolean(resolvedAsOfDate && !instrumentChartWorkspace && !instrumentChartError)

  const detailTabs = [
    { key: 'overview', label: 'Overview', meta: selectedRow ? selectedRow.instrument_core.instrument_type : 'Instrument' },
    {
      key: 'transactions',
      label: 'Transactions',
      meta: transactionsPending
        ? 'Loading'
        : transactionsWorkspace
          ? String(transactionsWorkspace.summary.total_transactions)
          : '—',
    },
    {
      key: 'lots',
      label: 'PositionLots',
      meta: positionLotsPending
        ? 'Loading'
        : positionLotsWorkspace
          ? String(positionLotsWorkspace.summary.position_lot_count)
          : '—',
    },
    {
      key: 'realizations',
      label: 'Realizations',
      meta: positionLotsPending
        ? 'Loading'
        : positionLotsWorkspace
          ? String(selectedPositionLot?.realization_count ?? 0)
          : '—',
    },
  ] as const

  const backToHoldingsPath = useMemo(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('detail_tab')
    next.delete('chart_range')
    next.delete('position_lot_id')
    if (instrumentId) {
      next.set('instrument_id', instrumentId)
    }
    const query = next.toString()
    return `${buildPortfolioSectionPath(portfolioId, '/holdings')}${query ? `?${query}` : ''}`
  }, [instrumentId, portfolioId, searchParams])

  const watchlistDetailUrl = buildWatchlistInstrumentDetailUrl(instrumentId, {
    source: 'portfolio',
    portfolio_id: portfolioId,
    as_of_date: resolvedAsOfDate,
    return_to: typeof window === 'undefined' ? null : `${window.location.pathname}${window.location.search}`,
  })

  const accountSlices = useMemo(() => {
    return aggregatePositionLotAccountSlices(selectedPositionLots)
  }, [selectedPositionLots])

  const accountNameById = useMemo(() => {
    const entries = new Map<string, string>()
    selectedTransactions.forEach((transaction) => {
      entries.set(transaction.account.account_id, transaction.account.account_name)
      if (transaction.settlement_cash_account) {
        entries.set(
          transaction.settlement_cash_account.account_id,
          transaction.settlement_cash_account.account_name,
        )
      }
    })
    return entries
  }, [selectedTransactions])

  const holdingCurrencyMatchesBase = selectedRow?.instrument_core.currency === baseCurrency
  const detailSummaryMetrics = selectedRow
    ? [
        {
          label: 'Quantity',
          value: formatQuantity(selectedRow.quantity),
        },
        {
          label: `Market Value (${selectedRow.instrument_core.currency})`,
          value: formatCurrency(selectedRow.market_value, selectedRow.instrument_core.currency),
        },
        ...(
          holdingCurrencyMatchesBase
            ? []
            : [{
                label: `Market Value (${baseCurrency})`,
                value: formatCurrency(selectedRow.market_value_base ?? null, baseCurrency),
              }]
        ),
        {
          label: `Unrealized P/L (${selectedRow.instrument_core.currency})`,
          value: formatSignedCurrency(selectedRowUnrealizedLocal, selectedRow.instrument_core.currency),
          toneClassName: signedValueClass(selectedRowUnrealizedLocal),
        },
        ...(
          holdingCurrencyMatchesBase
            ? []
            : [{
                label: `Unrealized P/L (${baseCurrency})`,
                value: formatSignedCurrency(selectedRowUnrealizedBase, baseCurrency),
                toneClassName: signedValueClass(selectedRowUnrealizedBase),
              }]
        ),
        {
          label: 'Accounts / Lots',
          value: `${formatNumber(selectedRow.account_count ?? 0, 0)} / ${formatNumber(selectedRow.open_position_lot_count ?? 0, 0)}`,
        },
      ]
    : []
  function updateSearchParam(key: string, value: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      const normalizedValue = value && value.trim() ? value : null
      if (normalizedValue) {
        next.set(key, normalizedValue)
      } else {
        next.delete(key)
      }
      return next
    })
  }

  useEffect(() => {
    if (!portfolioId || !instrumentId) {
      setWorkspace(null)
      setWorkspaceLoading(false)
      setWorkspaceError('Portfolio and instrument ids are required.')
      return
    }

    let cancelled = false
    setWorkspace(null)
    setWorkspaceError(null)
    setWorkspaceLoading(true)

    getPortfolioInstrumentHoldingProjection(portfolioId, instrumentId, {
      as_of_date: requestedAsOfDate || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setWorkspace(response)
          setWorkspaceError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setWorkspaceError(requestError instanceof Error ? requestError.message : 'Failed to load instrument holding.')
          setWorkspace(null)
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
  }, [instrumentId, portfolioId, requestedAsOfDate])

  useEffect(() => {
    if (!portfolioId || !instrumentId || !resolvedAsOfDate) {
      setPositionLotsWorkspace(null)
      setPositionLotsError(null)
      setPositionLotsLoading(false)
      return
    }

    let cancelled = false
    setPositionLotsWorkspace(null)
    setPositionLotsError(null)
    setPositionLotsLoading(true)

    getPortfolioPositionLots(portfolioId, {
      instrument_id: instrumentId,
      as_of_date: resolvedAsOfDate,
    })
      .then((response) => {
        if (!cancelled) {
          setPositionLotsWorkspace(response)
          setPositionLotsError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPositionLotsError(requestError instanceof Error ? requestError.message : 'Failed to load position lots.')
          setPositionLotsWorkspace(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPositionLotsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [instrumentId, portfolioId, resolvedAsOfDate])

  useEffect(() => {
    if (!portfolioId || !instrumentId || !resolvedAsOfDate) {
      setTransactionsWorkspace(null)
      setTransactionsError(null)
      setTransactionsLoading(false)
      return
    }

    let cancelled = false
    setTransactionsWorkspace(null)
    setTransactionsError(null)
    setTransactionsLoading(true)

    getPortfolioTransactions(portfolioId, {
      instrument_id: instrumentId,
      end_date: resolvedAsOfDate,
    })
      .then((response) => {
        if (!cancelled) {
          setTransactionsWorkspace(response)
          setTransactionsError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setTransactionsError(requestError instanceof Error ? requestError.message : 'Failed to load linked transactions.')
          setTransactionsWorkspace(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setTransactionsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [instrumentId, portfolioId, resolvedAsOfDate])

  useEffect(() => {
    if (!portfolioId || !instrumentId || !resolvedAsOfDate) {
      setInstrumentChartWorkspace(null)
      setInstrumentChartError(null)
      setInstrumentChartLoading(false)
      return
    }

    let cancelled = false
    setInstrumentChartWorkspace(null)
    setInstrumentChartError(null)
    setInstrumentChartLoading(true)

    getPortfolioInstrumentPriceChart(portfolioId, instrumentId, {
      as_of_date: resolvedAsOfDate,
      range: chartRangeKey,
    })
      .then((response) => {
        if (!cancelled) {
          setInstrumentChartWorkspace(response)
          setInstrumentChartError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setInstrumentChartError(requestError instanceof Error ? requestError.message : 'Failed to load price chart.')
          setInstrumentChartWorkspace(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setInstrumentChartLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [instrumentId, chartRangeKey, portfolioId, resolvedAsOfDate])

  return (
    <PortfolioWorkspaceLayout
      activeSection="Holdings"
      toolbarLabel={workspace?.view_label ?? 'View: Holdings'}
      busy={workspaceLoading || positionLotsPending || transactionsPending || instrumentChartPending}
    >
      <section
        className="portfolio-detail-surface portfolio-security-detail"
        aria-busy={workspaceLoading || positionLotsPending || transactionsPending || instrumentChartPending}
      >
        <div className="portfolio-security-detail-nav">
          <Link className="table-inline-link" to={backToHoldingsPath}>
            Back to Holdings
          </Link>
          <a className="table-inline-link" href={watchlistDetailUrl}>
            Open Instrument Detail
          </a>
        </div>

        <div className="portfolio-security-hero">
          <div className="portfolio-security-title-stack">
            <span className="portfolio-detail-meta">Portfolio Instrument Detail</span>
            <h1>{selectedRow?.instrument_core.instrument_name ?? instrumentChartWorkspace?.instrument_core.instrument_name ?? instrumentId}</h1>
            <div className="portfolio-security-meta-row">
              <span className="ticker-pill">{selectedRowIdentifier}</span>
              <span>{selectedRow ? formatLabel(selectedRow.instrument_core.instrument_type) : 'Instrument'}</span>
              <span>{selectedRow?.instrument_core.currency ?? instrumentChartWorkspace?.currency ?? '—'}</span>
              <span>As of {resolvedAsOfDate || '—'}</span>
            </div>
          </div>
          <div className="portfolio-security-hero-metrics">
            <div>
              <span>Weight</span>
              <strong>{formatPercent(selectedRow?.allocation)}</strong>
            </div>
            <div>
              <span>{valuationMetricName}</span>
              <strong>{formatUnitPrice(selectedRow?.last_price, selectedRow?.instrument_core.currency)}</strong>
            </div>
            <div>
              <span>Market Value</span>
              <strong>{formatCurrency(heroMarketValue, heroMarketCurrency)}</strong>
            </div>
            <div>
              <span>Unrealized P/L</span>
              <strong className={signedValueClass(heroUnrealizedValue)}>
                {formatSignedCurrency(heroUnrealizedValue, heroUnrealizedCurrency)}
              </strong>
            </div>
          </div>
        </div>

        {workspaceLoading ? <CalculationStatus /> : null}
        {workspaceError ? <div className="error-state">{workspaceError}</div> : null}
        {!workspaceLoading && !workspaceError && workspace && !selectedRow ? (
          <div className="inline-notice inline-notice-warning">Not held as of selected date.</div>
        ) : null}

        <div className="portfolio-security-chart-layout">
          <div className="portfolio-security-chart-main">
            <InstrumentPriceChart
              chart={instrumentChartWorkspace}
              loading={instrumentChartPending}
              error={instrumentChartError}
              rangeKey={chartRangeKey}
              onRangeChange={(rangeKey) => updateSearchParam('chart_range', rangeKey)}
              variant="instrument"
            />
          </div>
          <aside className="portfolio-security-chart-facts">
            <div className="portfolio-security-fact">
              <span>Valuation quote</span>
              <strong>{formatUnitPrice(selectedRow?.last_price, selectedRow?.instrument_core.currency)}</strong>
              <em>{valuationMetricName} · {(selectedRow?.quote_as_of_date ?? resolvedAsOfDate) || '—'} · used for market value</em>
            </div>
            <div className="portfolio-security-fact">
              <span>Performance series</span>
              <strong>{latestPerformancePoint ? formatUnitPrice(latestPerformancePoint.value, instrumentChartWorkspace?.currency) : '—'}</strong>
              <em>{performanceMetricName} · performance history · not the valuation quote</em>
            </div>
            <div className="portfolio-security-fact">
              <span>Coverage</span>
              <strong>{instrumentChartWorkspace ? `${instrumentChartWorkspace.summary.point_count} points` : '—'}</strong>
              <em>{selectedRow ? `${formatLabel(selectedRow.coverage_status)} holding · ${formatNumber(selectedRow.account_count ?? 0, 0)} accounts` : 'No position row'}</em>
            </div>
          </aside>
        </div>

        <div className="holdings-detail-tabbar portfolio-security-tabs" role="tablist" aria-label="Instrument detail">
          {detailTabs.map((tab) => {
            const isActive = detailTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                id={`portfolio-security-tab-${tab.key}`}
                role="tab"
                aria-selected={isActive}
                aria-controls={`portfolio-security-panel-${tab.key}`}
                tabIndex={isActive ? 0 : -1}
                className={`holdings-detail-tab ${isActive ? 'holdings-detail-tab-active' : ''}`}
                onClick={() => updateSearchParam('detail_tab', tab.key)}
              >
                <span className="holdings-detail-tab-label">{tab.label}</span>
                <span className="holdings-detail-tab-meta">{tab.meta}</span>
              </button>
            )
          })}
        </div>

        {detailTab === 'overview' ? (
          <div
            id="portfolio-security-panel-overview"
            className="holdings-detail-grid portfolio-security-overview-grid"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-overview"
          >
            <section className="holdings-detail-panel">
              <div className="portfolio-detail-toolbar holdings-side-toolbar">
                <div>
                  <div className="panel-title">Position Snapshot</div>
                  <div className="portfolio-detail-meta">Valuation and cost basis</div>
                </div>
                <div className="portfolio-detail-meta">
                  {selectedRow?.instrument_core.currency ?? 'Instrument'} / {baseCurrency}
                </div>
              </div>
              <div className="portfolio-security-metric-grid">
                {selectedRow ? detailSummaryMetrics.map((metric) => (
                  <div key={metric.label}>
                    <span>{metric.label}</span>
                    <strong className={metric.toneClassName}>{metric.value}</strong>
                  </div>
                )) : <div className="empty-state">No holding.</div>}
              </div>
              <div className="portfolio-security-basis-note">
                <strong>{valuationMetricName}</strong>
                <span>
                  {formatUnitPrice(selectedRow?.last_price, selectedRow?.instrument_core.currency)} as of{' '}
                  {(selectedRow?.quote_as_of_date ?? resolvedAsOfDate) || '—'} drives market value. The chart uses{' '}
                  {performanceMetricName} for return analysis and is not substituted into valuation.
                </span>
              </div>
            </section>

            <section className="holdings-detail-panel">
              <div className="portfolio-detail-toolbar holdings-side-toolbar">
                <div className="panel-title">Account Slices</div>
                <div className="portfolio-detail-meta">
                  {positionLotsPending ? 'Loading' : `${accountSlices.length} accounts`}
                </div>
              </div>
              <div className="portfolio-security-account-list">
                {positionLotsPending ? (
                  <div className="empty-state">Loading account positions.</div>
                ) : positionLotsError ? (
                  <div className="empty-state table-status-cell-error">{positionLotsError}</div>
                ) : accountSlices.length ? accountSlices.map((slice) => (
                  <article key={slice.accountId}>
                    <div className="portfolio-security-account-head">
                      <div>
                        <strong>{accountNameById.get(slice.accountId) ?? slice.accountId}</strong>
                        <span>{formatNumber(slice.openPositionLotCount, 0)} open lots</span>
                      </div>
                      <strong>{formatCurrency(slice.marketValue, selectedRow?.instrument_core.currency ?? baseCurrency)}</strong>
                    </div>
                    <dl>
                      <div><dt>Quantity</dt><dd>{formatQuantity(slice.quantity)}</dd></div>
                      <div><dt>Remaining cost</dt><dd>{formatCurrency(slice.remainingCost, selectedRow?.instrument_core.currency ?? baseCurrency)}</dd></div>
                    </dl>
                  </article>
                )) : (
                  <div className="empty-state">No account positions.</div>
                )}
              </div>
            </section>
          </div>
        ) : null}

        {detailTab === 'transactions' ? (
          <div
            id="portfolio-security-panel-transactions"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-transactions"
          >
            <div className="portfolio-detail-toolbar holdings-detail-toolbar">
              <div>
                <div className="panel-title">Linked Transactions</div>
                <div className="portfolio-detail-meta">{resolvedAsOfDate || '—'}</div>
              </div>
              {transactionsPending ? (
                <div className="portfolio-detail-meta">Loading</div>
              ) : transactionsWorkspace ? (
                <div className="portfolio-detail-meta">{transactionsWorkspace.summary.total_transactions} facts</div>
              ) : null}
            </div>
            <div className="table-shell">
              <table className="holdings-table portfolio-security-transactions-table">
                <thead>
                  <tr>
                    <th>Trade / Settle</th>
                    <th>Type / Account</th>
                    <th>Units / Price</th>
                    <th>Gross / Net Cash</th>
                    <th>Note</th>
                  </tr>
                </thead>
                <tbody>
                  {transactionsPending ? (
                    <TableStatusRow colSpan={5} label="Loading" />
                  ) : transactionsError ? (
                    <TableStatusRow colSpan={5} label={transactionsError} tone="error" />
                  ) : selectedTransactions.length ? (
                    selectedTransactions.map((transaction) => (
                      <tr key={transaction.transaction_id}>
                        <td>
                          <div className="holding-name-stack">
                            <Link className="table-inline-link" to={`${buildPortfolioSectionPath(portfolioId, '/transactions')}?transaction_id=${encodeURIComponent(transaction.transaction_id)}`}>
                              {transaction.trade_date}
                            </Link>
                            <span className="holding-secondary">Settle {transaction.settlement_date}</span>
                          </div>
                        </td>
                        <td>
                          <div className="holding-name-stack">
                            <span className="transaction-type-pill">{formatLabel(transaction.transaction_type)}</span>
                            <span className="holding-secondary">{transactionAccountLabel(transaction)}</span>
                          </div>
                        </td>
                        <td>
                          <div className="holding-name-stack">
                            <span>{formatQuantity(transaction.quantity)}</span>
                            <span className="holding-secondary">{formatUnitPrice(transaction.price, transaction.currency)}</span>
                          </div>
                        </td>
                        <td>
                          <div className="holding-name-stack">
                            <span>{formatCurrency(transaction.gross_amount, transaction.currency)}</span>
                            <span className={signedValueClass(transaction.net_cash_effect)}>
                              Net {formatSignedCurrency(transaction.net_cash_effect, transaction.currency)}
                            </span>
                          </div>
                        </td>
                        <td className="transaction-note-cell">{transaction.note || '—'}</td>
                      </tr>
                    ))
                  ) : (
                    <TableStatusRow colSpan={5} label="No transactions." />
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {detailTab === 'lots' ? (
          <div
            id="portfolio-security-panel-lots"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-lots"
          >
            <div className="portfolio-detail-toolbar holdings-detail-toolbar">
              <div>
                <div className="panel-title">PositionLots</div>
                <div className="portfolio-detail-meta">{selectedRow?.instrument_core.instrument_name ?? instrumentId}</div>
              </div>
              {positionLotsPending ? (
                <div className="portfolio-detail-meta">Loading</div>
              ) : positionLotsWorkspace ? (
                <div className="portfolio-detail-meta">
                  {positionLotsWorkspace.summary.open_position_lot_count} open / {positionLotsWorkspace.summary.closed_position_lot_count} closed
                </div>
              ) : null}
            </div>
            <div className="position-lot-workbench">
              <div className="table-shell">
                <table className="holdings-table position-lots-table portfolio-security-lots-table">
                  <thead>
                    <tr>
                      <th>Account / Status</th>
                      <th>Life</th>
                      <th>Entry / Remaining Qty</th>
                      <th>Entry / Remaining Cost</th>
                      <th>Market Value</th>
                      <th>Realized / Unrealized P&amp;L</th>
                      <th>Activity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positionLotsPending ? (
                      <TableStatusRow colSpan={7} label="Loading" />
                    ) : positionLotsError ? (
                      <TableStatusRow colSpan={7} label={positionLotsError} tone="error" />
                    ) : selectedPositionLots.length ? (
                      selectedPositionLots.map((positionLot) => (
                        <tr
                          key={positionLot.position_lot_id}
                          tabIndex={0}
                          aria-selected={selectedPositionLot?.position_lot_id === positionLot.position_lot_id}
                          className={selectedPositionLot?.position_lot_id === positionLot.position_lot_id ? 'position-lots-row holdings-row-active' : 'position-lots-row'}
                          onClick={() => updateSearchParam('position_lot_id', positionLot.position_lot_id)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault()
                              updateSearchParam('position_lot_id', positionLot.position_lot_id)
                            }
                          }}
                        >
                          <td>
                            <div className="holding-name-stack">
                              <strong>{accountNameById.get(positionLot.account_id) ?? positionLot.account_id}</strong>
                              <span><span className={`coverage-pill ${lotStatusClass(positionLot)}`}>{formatLabel(positionLot.status)}</span></span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span>{positionLot.opened_at}</span>
                              <span className="holding-secondary">{positionLot.closed_at ? `Closed ${positionLot.closed_at}` : `${formatNumber(positionLot.holding_period_days)} days`}</span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack"><span>{formatQuantity(positionLot.entry_quantity)}</span><span className="holding-secondary">Remain {formatQuantity(positionLot.remaining_quantity)}</span></div>
                          </td>
                          <td>
                            <div className="holding-name-stack"><span>{formatCurrency(positionLot.entry_cost_basis, positionLot.currency)}</span><span className="holding-secondary">Remain {formatCurrency(positionLot.remaining_cost_basis, positionLot.currency)}</span></div>
                          </td>
                          <td>{formatCurrency(positionLot.current_market_value, positionLot.currency)}</td>
                          <td>
                            <div className="holding-name-stack">
                              <span className={signedValueClass(positionLot.realized_pnl)}>{formatSignedCurrency(positionLot.realized_pnl, positionLot.currency)}</span>
                              <span className={`${signedValueClass(positionLot.unrealized_pnl)} holding-secondary`}>Unrealized {formatSignedCurrency(positionLot.unrealized_pnl, positionLot.currency)}</span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack"><span>{positionLot.realization_count} exits</span><span className="holding-secondary">{positionLot.linked_transaction_count} facts</span></div>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <TableStatusRow colSpan={7} label="No lots." />
                    )}
                  </tbody>
                </table>
              </div>
              <aside className="position-lot-inspector">
                {selectedPositionLot ? (
                  <>
                    <div className="position-lot-inspector-head">
                      <div>
                        <span className="portfolio-detail-meta">Selected lot</span>
                        <strong>{selectedPositionLot.position_lot_id}</strong>
                      </div>
                      <span className={`coverage-pill ${lotStatusClass(selectedPositionLot)}`}>{formatLabel(selectedPositionLot.status)}</span>
                    </div>
                    <dl className="position-lot-inspector-metrics">
                      <div><dt>Cost method</dt><dd>{formatLabel(selectedPositionLot.cost_basis_method)}</dd></div>
                      <div><dt>Entry price</dt><dd>{formatUnitPrice(selectedPositionLot.entry_price, selectedPositionLot.currency)}</dd></div>
                      <div><dt>Income</dt><dd>{formatCurrency(selectedPositionLot.income_cash_amount, selectedPositionLot.currency)}</dd></div>
                      <div><dt>Expense</dt><dd>{formatCurrency(selectedPositionLot.expense_cash_amount, selectedPositionLot.currency)}</dd></div>
                      <div><dt>Transferred qty</dt><dd>{formatQuantity(selectedPositionLot.transferred_quantity)}</dd></div>
                      <div><dt>Realized qty</dt><dd>{formatQuantity(selectedPositionLot.realized_quantity)}</dd></div>
                    </dl>
                    <button type="button" className="toolbar-link" onClick={() => updateSearchParam('detail_tab', 'realizations')}>
                      View {selectedPositionLot.realization_count} realizations
                    </button>
                  </>
                ) : (
                  <div className="empty-state">Select a lot.</div>
                )}
              </aside>
            </div>
          </div>
        ) : null}

        {detailTab === 'realizations' ? (
          <div
            id="portfolio-security-panel-realizations"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-realizations"
          >
            <div className="portfolio-detail-toolbar holdings-detail-toolbar">
              <div>
                <div className="panel-title">Realizations</div>
                <div className="portfolio-detail-meta">
                  {selectedPositionLot
                    ? `${selectedPositionLot.position_lot_id} · ${selectedPositionLot.account_id} · ${formatLabel(selectedPositionLot.status)}`
                    : 'Select a PositionLot'}
                </div>
              </div>
              <div className="portfolio-detail-meta">
                {positionLotsPending
                  ? 'Loading'
                  : selectedPositionLot
                    ? `${selectedPositionLot.realization_count} matched exits`
                    : 'No lot'}
              </div>
            </div>
            <div className="table-shell">
              <table className="holdings-table position-lot-realizations-table">
                <thead>
                  <tr>
                    <th>Trade / Type</th>
                    <th>Quantity / Price</th>
                    <th>Proceeds / Cost Released</th>
                    <th>Realized P/L</th>
                    <th>Remaining Position</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {positionLotsPending ? (
                    <TableStatusRow colSpan={6} label="Loading" />
                  ) : positionLotsError ? (
                    <TableStatusRow colSpan={6} label={positionLotsError} tone="error" />
                  ) : selectedPositionLot?.realizations.length ? (
                    selectedPositionLot.realizations.map((realization) => (
                      <tr key={realization.realization_id}>
                        <td><div className="holding-name-stack"><span>{realization.trade_date}</span><span className="holding-secondary">{formatLabel(realization.transaction_type)}</span></div></td>
                        <td><div className="holding-name-stack"><span>{formatQuantity(realization.quantity)}</span><span className="holding-secondary">@ {formatUnitPrice(realization.price, selectedPositionLot.currency)}</span></div></td>
                        <td><div className="holding-name-stack"><span>{formatCurrency(realization.proceeds, selectedPositionLot.currency)}</span><span className="holding-secondary">Cost {formatCurrency(realization.cost_basis_released, selectedPositionLot.currency)}</span></div></td>
                        <td className={signedValueClass(realization.realized_pnl)}>
                          {formatSignedCurrency(realization.realized_pnl, selectedPositionLot.currency)}
                        </td>
                        <td><div className="holding-name-stack"><span>{formatQuantity(realization.remaining_quantity_after)}</span><span className="holding-secondary">{formatCurrency(realization.remaining_cost_basis_after, selectedPositionLot.currency)}</span></div></td>
                        <td>
                          <span className={`coverage-pill ${realization.status_after === 'open' ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
                            {formatLabel(realization.status_after)}
                          </span>
                        </td>
                      </tr>
                    ))
                  ) : selectedPositionLot ? (
                    <TableStatusRow colSpan={6} label="No realizations." />
                  ) : (
                    <TableStatusRow colSpan={6} label="No lot." />
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
