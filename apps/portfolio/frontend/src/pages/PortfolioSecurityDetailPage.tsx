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
  type PortfolioDailyPublishedLotListResponse,
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

type SecurityDetailTab = 'overview' | 'transactions' | 'lots'

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
  if (value === 'transactions' || value === 'lots') {
    return value
  }
  return 'overview'
}

function transactionAccountLabel(transaction: PortfolioTransactionRecord) {
  return transaction.account.account_name
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
  const [workspace, setWorkspace] = useState<PortfolioInstrumentHoldingProjectionResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [positionLotsWorkspace, setPositionLotsWorkspace] = useState<PortfolioDailyPublishedLotListResponse | null>(null)
  const [positionLotsLoading, setPositionLotsLoading] = useState(false)
  const [positionLotsError, setPositionLotsError] = useState<string | null>(null)
  const [transactionsWorkspace, setTransactionsWorkspace] = useState<PortfolioTransactionListResponse | null>(null)
  const [transactionsLoading, setTransactionsLoading] = useState(false)
  const [transactionsError, setTransactionsError] = useState<string | null>(null)
  const [instrumentChartWorkspace, setInstrumentChartWorkspace] = useState<PortfolioInstrumentPriceChartResponse | null>(null)
  const [instrumentChartLoading, setInstrumentChartLoading] = useState(false)
  const [instrumentChartError, setInstrumentChartError] = useState<string | null>(null)

  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const selectedPositionLotId = searchParams.get('position_lot_id')
  const detailTab = parseDetailTab(searchParams.get('detail_tab'))
  const chartRangeKey = parseChartRange(searchParams.get('chart_range'))
  const selectedRow = workspace?.row ?? null
  const selectedPositionLots = positionLotsWorkspace?.position_lots ?? []
  const selectedTransactions = transactionsWorkspace?.transactions ?? []
  const selectedPositionLot =
    selectedPositionLots.find((positionLot) => positionLot.lot_id === selectedPositionLotId) ??
    selectedPositionLots[0] ??
    null
  const resolvedAsOfDate = workspace?.as_of_date || requestedAsOfDate
  const baseCurrency = workspace?.base_currency ?? ''
  const baseCurrencyLabel = baseCurrency || 'Base currency unavailable'
  const selectedRowIdentifier = selectedRow ? primaryIdentifier(selectedRow) : instrumentId
  const selectedRowUnrealizedBase = selectedRow?.unrealized_pnl_base ?? null
  const selectedRowUnrealizedLocal = selectedRow?.unrealized_pnl ?? null
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

  const detailTabs = [
    { key: 'overview', label: 'Overview', meta: selectedRow ? selectedRow.instrument_core.instrument_type : 'Instrument' },
    {
      key: 'transactions',
      label: 'Transactions',
      meta: transactionsLoading ? '...' : transactionsWorkspace ? String(transactionsWorkspace.summary.total_transactions) : '0',
    },
    {
      key: 'lots',
      label: 'Open Lots',
      meta: positionLotsLoading ? '...' : positionLotsWorkspace ? String(positionLotsWorkspace.summary.lot_count) : '0',
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
                label: `Market Value (${baseCurrencyLabel})`,
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
                label: `Unrealized P/L (${baseCurrencyLabel})`,
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
    setInstrumentChartWorkspace((current) => (current?.instrument_core.instrument_id === instrumentId ? current : null))
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
    <PortfolioWorkspaceLayout activeSection="Holdings" toolbarLabel={workspace?.view_label ?? 'View: Holdings'}>
      <section className="portfolio-detail-surface portfolio-security-detail">
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
              loading={instrumentChartLoading}
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
                  {selectedRow?.instrument_core.currency ?? 'Instrument'} / {baseCurrencyLabel}
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
                  {positionLotsLoading ? 'Loading' : `${accountSlices.length} accounts`}
                </div>
              </div>
              <div className="portfolio-security-account-list">
                {positionLotsLoading ? (
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
                      <strong>
                        {formatCurrency(slice.costBasisLocalExact, selectedRow?.instrument_core.currency ?? baseCurrency)}
                      </strong>
                    </div>
                    <dl>
                      <div><dt>Open quantity</dt><dd>{formatQuantity(slice.quantityExact)}</dd></div>
                      <div><dt>Open local cost</dt><dd>{formatCurrency(slice.costBasisLocalExact, selectedRow?.instrument_core.currency ?? baseCurrency)}</dd></div>
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
              {transactionsLoading ? (
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
                  {transactionsLoading ? (
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
                <div className="panel-title">Published Open Lots</div>
                <div className="portfolio-detail-meta">
                  {selectedRow?.instrument_core.instrument_name ?? instrumentId} · {positionLotsWorkspace?.summary.as_of_date ?? resolvedAsOfDate}
                </div>
              </div>
              {positionLotsLoading ? (
                <div className="portfolio-detail-meta">Loading</div>
              ) : positionLotsWorkspace ? (
                <div className="portfolio-detail-meta">
                  {positionLotsWorkspace.summary.lot_count} open lots · publication {positionLotsWorkspace.publication.publication_id}
                </div>
              ) : null}
            </div>
            <div className="position-lot-workbench">
              <div className="table-shell">
                <table className="holdings-table position-lots-table portfolio-security-lots-table">
                  <thead>
                    <tr>
                      <th>Account</th>
                      <th>Acquisition</th>
                      <th>Open Quantity</th>
                      <th>Local Cost</th>
                      <th>Unit Cost</th>
                      <th>Base Cost</th>
                      <th>Source Fact</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positionLotsLoading ? (
                      <TableStatusRow colSpan={7} label="Loading" />
                    ) : positionLotsError ? (
                      <TableStatusRow colSpan={7} label={positionLotsError} tone="error" />
                    ) : selectedPositionLots.length ? (
                      selectedPositionLots.map((positionLot) => (
                        <tr
                          key={positionLot.lot_id}
                          tabIndex={0}
                          aria-selected={selectedPositionLot?.lot_id === positionLot.lot_id}
                          className={selectedPositionLot?.lot_id === positionLot.lot_id ? 'position-lots-row holdings-row-active' : 'position-lots-row'}
                          onClick={() => updateSearchParam('position_lot_id', positionLot.lot_id)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault()
                              updateSearchParam('position_lot_id', positionLot.lot_id)
                            }
                          }}
                        >
                          <td>
                            <div className="holding-name-stack">
                              <strong>{accountNameById.get(positionLot.account_id) ?? positionLot.account_id}</strong>
                              <span className={`coverage-pill ${positionLot.measured_base_cost ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
                                {formatLabel(positionLot.base_cost_coverage_state)}
                              </span>
                            </div>
                          </td>
                          <td>{positionLot.acquisition_date}</td>
                          <td>{formatQuantity(positionLot.open_quantity_exact)}</td>
                          <td>{formatCurrency(positionLot.cost_basis_local_exact, positionLot.currency)}</td>
                          <td>{formatUnitPrice(positionLot.unit_cost_local, positionLot.currency)}</td>
                          <td>{formatCurrency(positionLot.cost_basis_base_exact, baseCurrency)}</td>
                          <td>{positionLot.source_transaction_id}</td>
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
                        <strong>{selectedPositionLot.lot_id}</strong>
                      </div>
                      <span className={`coverage-pill ${selectedPositionLot.measured_base_cost ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
                        {formatLabel(selectedPositionLot.base_cost_coverage_state)}
                      </span>
                    </div>
                    <dl className="position-lot-inspector-metrics">
                      <div><dt>Source revision</dt><dd>v{selectedPositionLot.source_revision_number} · {selectedPositionLot.source_revision_id}</dd></div>
                      <div><dt>Custody revision</dt><dd>v{selectedPositionLot.custody_revision_number} · {selectedPositionLot.custody_revision_id}</dd></div>
                      <div><dt>Acquisition FX</dt><dd>{selectedPositionLot.acquisition_fx_rate_exact ?? '—'}</dd></div>
                      <div><dt>Local unit residual</dt><dd>{selectedPositionLot.unit_cost_local_rounding_residual_exact}</dd></div>
                      <div><dt>Base unit cost</dt><dd>{formatCurrency(selectedPositionLot.unit_cost_base, baseCurrency, 4)}</dd></div>
                      <div><dt>Coverage reasons</dt><dd>{selectedPositionLot.base_cost_reason_codes.map(formatLabel).join(' · ') || 'None'}</dd></div>
                    </dl>
                  </>
                ) : (
                  <div className="empty-state">Select a lot.</div>
                )}
              </aside>
            </div>
          </div>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
