import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import AssetPriceChart from '../components/AssetPriceChart'
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
  getHoldingsWorkspace,
  getPortfolioAssetPriceChart,
  getPortfolioPositionLots,
  getPortfolioTransactions,
  type HoldingsWorkspaceResponse,
  type PortfolioAssetChartRangeKey,
  type PortfolioAssetPriceChartResponse,
  type PortfolioHoldingRow,
  type PortfolioPositionLotListResponse,
  type PortfolioPositionLotRecord,
  type PortfolioTransactionListResponse,
  type PortfolioTransactionRecord,
} from '../lib/api'
import {
  buildPortfolioSectionPath,
  buildWatchlistInstrumentDetailUrl,
} from '../lib/navigation'

type SecurityDetailTab = 'overview' | 'transactions' | 'lots' | 'realizations'

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.asset_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.asset_core.identifiers[0]?.identifier_value ??
    row.asset_core.asset_id
  )
}

function parseChartRange(value: string | null): PortfolioAssetChartRangeKey {
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

function chunkMetrics<T>(items: T[], size: number) {
  const rows: T[][] = []
  for (let index = 0; index < items.length; index += size) {
    rows.push(items.slice(index, index + size))
  }
  return rows
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
  const { portfolioId = '', assetId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspace, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [positionLotsWorkspace, setPositionLotsWorkspace] = useState<PortfolioPositionLotListResponse | null>(null)
  const [positionLotsLoading, setPositionLotsLoading] = useState(false)
  const [positionLotsError, setPositionLotsError] = useState<string | null>(null)
  const [transactionsWorkspace, setTransactionsWorkspace] = useState<PortfolioTransactionListResponse | null>(null)
  const [transactionsLoading, setTransactionsLoading] = useState(false)
  const [transactionsError, setTransactionsError] = useState<string | null>(null)
  const [assetChartWorkspace, setAssetChartWorkspace] = useState<PortfolioAssetPriceChartResponse | null>(null)
  const [assetChartLoading, setAssetChartLoading] = useState(false)
  const [assetChartError, setAssetChartError] = useState<string | null>(null)

  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const selectedPositionLotId = searchParams.get('position_lot_id')
  const detailTab = parseDetailTab(searchParams.get('detail_tab'))
  const chartRangeKey = parseChartRange(searchParams.get('chart_range'))
  const selectedRow = workspace?.rows.find((row) => row.asset_core.asset_id === assetId) ?? null
  const selectedPositionLots = positionLotsWorkspace?.position_lots ?? []
  const selectedTransactions = transactionsWorkspace?.transactions ?? []
  const selectedPositionLot =
    selectedPositionLots.find((positionLot) => positionLot.position_lot_id === selectedPositionLotId) ??
    selectedPositionLots[0] ??
    null
  const resolvedAsOfDate = workspace?.as_of_date ?? requestedAsOfDate
  const baseCurrency = workspace?.base_currency ?? selectedRow?.asset_core.currency ?? assetChartWorkspace?.currency ?? 'USD'
  const selectedRowIdentifier = selectedRow ? primaryIdentifier(selectedRow) : assetId
  const selectedRowUnrealizedBase =
    selectedRow?.market_value_base != null && selectedRow.cost_basis_base != null
      ? selectedRow.market_value_base - selectedRow.cost_basis_base
      : null
  const selectedRowUnrealizedLocal =
    selectedRow?.market_value != null && selectedRow.cost_basis != null
      ? selectedRow.market_value - selectedRow.cost_basis
      : null
  const heroMarketValue = selectedRow?.market_value_base ?? selectedRow?.market_value
  const heroMarketCurrency = selectedRow?.market_value_base != null ? baseCurrency : selectedRow?.asset_core.currency ?? baseCurrency
  const heroUnrealizedValue = selectedRowUnrealizedBase ?? selectedRowUnrealizedLocal
  const heroUnrealizedCurrency = selectedRowUnrealizedBase != null ? baseCurrency : selectedRow?.asset_core.currency ?? baseCurrency

  const detailTabs = [
    { key: 'overview', label: 'Overview', meta: selectedRow ? selectedRow.asset_core.asset_type : 'Asset' },
    {
      key: 'transactions',
      label: 'Transactions',
      meta: transactionsLoading ? '...' : transactionsWorkspace ? String(transactionsWorkspace.summary.total_transactions) : '0',
    },
    {
      key: 'lots',
      label: 'PositionLots',
      meta: positionLotsLoading ? '...' : positionLotsWorkspace ? String(positionLotsWorkspace.summary.position_lot_count) : '0',
    },
    {
      key: 'realizations',
      label: 'Realizations',
      meta: positionLotsLoading ? '...' : selectedPositionLot ? String(selectedPositionLot.realization_count) : '0',
    },
  ] as const

  const backToHoldingsPath = useMemo(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('detail_tab')
    next.delete('chart_range')
    next.delete('position_lot_id')
    if (assetId) {
      next.set('asset_id', assetId)
    }
    const query = next.toString()
    return `${buildPortfolioSectionPath(portfolioId, '/holdings')}${query ? `?${query}` : ''}`
  }, [assetId, portfolioId, searchParams])

  const watchlistDetailUrl = buildWatchlistInstrumentDetailUrl(assetId, {
    source: 'portfolio',
    portfolio_id: portfolioId,
    as_of_date: resolvedAsOfDate,
    return_to: typeof window === 'undefined' ? null : `${window.location.pathname}${window.location.search}`,
  })

  const accountSlices = useMemo(() => {
    const buckets = new Map<
      string,
      {
        accountId: string
        quantity: number
        remainingCost: number
        marketValue: number | null
        openPositionLotCount: number
      }
    >()
    selectedPositionLots.forEach((positionLot) => {
      const current = buckets.get(positionLot.account_id) ?? {
        accountId: positionLot.account_id,
        quantity: 0,
        remainingCost: 0,
        marketValue: 0,
        openPositionLotCount: 0,
      }
      current.quantity += positionLot.remaining_quantity
      current.remainingCost += positionLot.remaining_cost_basis
      current.marketValue =
        current.marketValue == null || positionLot.current_market_value == null
          ? current.marketValue == null
            ? positionLot.current_market_value ?? null
            : current.marketValue
          : current.marketValue + positionLot.current_market_value
      if (positionLot.status === 'open') {
        current.openPositionLotCount += 1
      }
      buckets.set(positionLot.account_id, current)
    })
    return Array.from(buckets.values()).sort((left, right) => {
      const rightMarketValue = right.marketValue ?? Number.NEGATIVE_INFINITY
      const leftMarketValue = left.marketValue ?? Number.NEGATIVE_INFINITY
      return rightMarketValue - leftMarketValue || left.accountId.localeCompare(right.accountId)
    })
  }, [selectedPositionLots])

  const detailSummaryMetrics = selectedRow
    ? [
        {
          label: 'Quantity',
          value: formatQuantity(selectedRow.quantity),
        },
        {
          label: `Market Value (${selectedRow.asset_core.currency})`,
          value: formatCurrency(selectedRow.market_value, selectedRow.asset_core.currency),
        },
        {
          label: `Market Value (${baseCurrency})`,
          value: formatCurrency(selectedRow.market_value_base ?? null, baseCurrency),
        },
        {
          label: `Unrealized P/L (${selectedRow.asset_core.currency})`,
          value: formatSignedCurrency(selectedRowUnrealizedLocal, selectedRow.asset_core.currency),
          toneClassName: signedValueClass(selectedRowUnrealizedLocal),
        },
        {
          label: `Unrealized P/L (${baseCurrency})`,
          value: formatSignedCurrency(selectedRowUnrealizedBase, baseCurrency),
          toneClassName: signedValueClass(selectedRowUnrealizedBase),
        },
        {
          label: 'Accounts / Lots',
          value: `${formatNumber(selectedRow.account_count ?? 0, 0)} / ${formatNumber(selectedRow.open_position_lot_count ?? 0, 0)}`,
        },
      ]
    : []
  const detailSummaryRows = chunkMetrics(detailSummaryMetrics, 2)

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
    if (!portfolioId) {
      setWorkspace(null)
      setWorkspaceLoading(false)
      setWorkspaceError('Portfolio id is required.')
      return
    }

    let cancelled = false
    setWorkspaceLoading(true)

    getHoldingsWorkspace(portfolioId, { as_of_date: requestedAsOfDate || undefined })
      .then((response) => {
        if (!cancelled) {
          setWorkspace(response)
          setWorkspaceError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setWorkspaceError(requestError instanceof Error ? requestError.message : 'Failed to load holdings workspace.')
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
  }, [portfolioId, requestedAsOfDate])

  useEffect(() => {
    if (!portfolioId || !assetId || !resolvedAsOfDate) {
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
      asset_id: assetId,
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
  }, [assetId, portfolioId, resolvedAsOfDate])

  useEffect(() => {
    if (!portfolioId || !assetId || !resolvedAsOfDate) {
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
      asset_id: assetId,
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
  }, [assetId, portfolioId, resolvedAsOfDate])

  useEffect(() => {
    if (!portfolioId || !assetId || !resolvedAsOfDate) {
      setAssetChartWorkspace(null)
      setAssetChartError(null)
      setAssetChartLoading(false)
      return
    }

    let cancelled = false
    setAssetChartWorkspace((current) => (current?.asset_core.asset_id === assetId ? current : null))
    setAssetChartError(null)
    setAssetChartLoading(true)

    getPortfolioAssetPriceChart(portfolioId, assetId, {
      as_of_date: resolvedAsOfDate,
      range: chartRangeKey,
    })
      .then((response) => {
        if (!cancelled) {
          setAssetChartWorkspace(response)
          setAssetChartError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setAssetChartError(requestError instanceof Error ? requestError.message : 'Failed to load price chart.')
          setAssetChartWorkspace(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAssetChartLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [assetId, chartRangeKey, portfolioId, resolvedAsOfDate])

  return (
    <PortfolioWorkspaceLayout activeSection="Holdings" toolbarLabel={workspace?.view_label ?? 'View: Holdings'}>
      <section className="portfolio-detail-surface portfolio-security-detail">
        <div className="portfolio-security-detail-nav">
          <Link className="table-inline-link" to={backToHoldingsPath}>
            Back to Holdings
          </Link>
          <a className="table-inline-link" href={watchlistDetailUrl}>
            Open Asset Detail
          </a>
        </div>

        <div className="portfolio-security-hero">
          <div className="portfolio-security-title-stack">
            <span className="portfolio-detail-meta">Portfolio Security Detail</span>
            <h1>{selectedRow?.asset_core.asset_name ?? assetChartWorkspace?.asset_core.asset_name ?? assetId}</h1>
            <div className="portfolio-security-meta-row">
              <span className="ticker-pill">{selectedRowIdentifier}</span>
              <span>{selectedRow ? formatLabel(selectedRow.asset_core.asset_type) : 'Asset'}</span>
              <span>{selectedRow?.asset_core.currency ?? assetChartWorkspace?.currency ?? '—'}</span>
              <span>As of {resolvedAsOfDate || '—'}</span>
            </div>
          </div>
          <div className="portfolio-security-hero-metrics">
            <div>
              <span>Weight</span>
              <strong>{formatPercent(selectedRow?.allocation)}</strong>
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

        {workspaceLoading ? <CalculationStatus label="Loading security detail..." /> : null}
        {workspaceError ? <div className="error-state">{workspaceError}</div> : null}
        {!workspaceLoading && !workspaceError && workspace && !selectedRow ? (
          <div className="inline-notice inline-notice-warning">
            This asset is not present in current holdings for the selected as-of date. Asset price history may still be available.
          </div>
        ) : null}

        <div className="portfolio-security-chart-layout">
          <div className="portfolio-security-chart-main">
            <AssetPriceChart
              chart={assetChartWorkspace}
              loading={assetChartLoading}
              error={assetChartError}
              rangeKey={chartRangeKey}
              onRangeChange={(rangeKey) => updateSearchParam('chart_range', rangeKey)}
              variant="instrument"
            />
          </div>
          <aside className="portfolio-security-chart-facts">
            <div className="portfolio-security-fact">
              <span>Latest Price</span>
              <strong>{formatUnitPrice(selectedRow?.last_price ?? assetChartWorkspace?.points[assetChartWorkspace.points.length - 1]?.value, selectedRow?.asset_core.currency ?? assetChartWorkspace?.currency)}</strong>
              <em>{assetChartWorkspace?.chart_basis ?? 'Asset market data'}</em>
            </div>
            <div className="portfolio-security-fact">
              <span>Chart Coverage</span>
              <strong>{assetChartWorkspace ? `${assetChartWorkspace.summary.point_count} points` : '—'}</strong>
              <em>{assetChartWorkspace?.metric_family ? formatLabel(assetChartWorkspace.metric_family) : 'Shared asset facts'}</em>
            </div>
            <div className="portfolio-security-fact">
              <span>Holding Coverage</span>
              <strong>{selectedRow ? formatLabel(selectedRow.coverage_status) : 'Not held'}</strong>
              <em>{selectedRow ? `${formatNumber(selectedRow.account_count ?? 0, 0)} accounts` : 'No position row'}</em>
            </div>
          </aside>
        </div>

        <div className="holdings-detail-tabbar portfolio-security-tabs">
          {detailTabs.map((tab) => {
            const isActive = detailTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
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
          <div className="holdings-detail-grid portfolio-security-overview-grid">
            <section className="holdings-detail-panel">
              <div className="portfolio-detail-toolbar holdings-side-toolbar">
                <div className="panel-title">Statement Summary</div>
                <div className="portfolio-detail-meta">
                  {selectedRow?.asset_core.currency ?? 'Asset'} / {baseCurrency}
                </div>
              </div>
              <div className="table-shell">
                <table className="holdings-table holdings-summary-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Value</th>
                      <th>Metric</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedRow ? (
                      detailSummaryRows.map((metrics, index) => (
                        <tr key={`summary-row-${index}`}>
                          <th scope="row">{metrics[0]?.label ?? '—'}</th>
                          <td className={metrics[0]?.toneClassName}>{metrics[0]?.value ?? '—'}</td>
                          <th scope="row">{metrics[1]?.label ?? '—'}</th>
                          <td className={metrics[1]?.toneClassName}>{metrics[1]?.value ?? '—'}</td>
                        </tr>
                      ))
                    ) : (
                      <TableStatusRow colSpan={4} label="No holding row exists for this as-of date." />
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="holdings-detail-panel">
              <div className="portfolio-detail-toolbar holdings-side-toolbar">
                <div className="panel-title">Account Slices</div>
                <div className="portfolio-detail-meta">
                  {positionLotsLoading ? 'Refreshing...' : `${accountSlices.length} accounts`}
                </div>
              </div>
              <div className="table-shell">
                <table className="holdings-table holdings-side-table">
                  <thead>
                    <tr>
                      <th>Account</th>
                      <th>Qty</th>
                      <th>Market Value</th>
                      <th>Remaining Cost</th>
                      <th>Open Lots</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positionLotsLoading ? (
                      <TableStatusRow colSpan={5} label="Loading account slices..." />
                    ) : positionLotsError ? (
                      <TableStatusRow colSpan={5} label={positionLotsError} tone="error" />
                    ) : accountSlices.length ? (
                      accountSlices.map((slice) => (
                        <tr key={slice.accountId}>
                          <td>{slice.accountId}</td>
                          <td>{formatQuantity(slice.quantity)}</td>
                          <td>{formatCurrency(slice.marketValue, selectedRow?.asset_core.currency ?? baseCurrency)}</td>
                          <td>{formatCurrency(slice.remainingCost, selectedRow?.asset_core.currency ?? baseCurrency)}</td>
                          <td>{formatNumber(slice.openPositionLotCount, 0)}</td>
                        </tr>
                      ))
                    ) : (
                      <TableStatusRow colSpan={5} label="No account slices for the selected security." />
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        ) : null}

        {detailTab === 'transactions' ? (
          <>
            <div className="portfolio-detail-toolbar holdings-detail-toolbar">
              <div>
                <div className="panel-title">Linked Transactions</div>
                <div className="portfolio-detail-meta">
                  All recorded transactions for {selectedRowIdentifier} up to {resolvedAsOfDate || '—'}
                </div>
              </div>
              {transactionsLoading ? (
                <div className="portfolio-detail-meta">Refreshing...</div>
              ) : transactionsWorkspace ? (
                <div className="portfolio-detail-meta">{transactionsWorkspace.summary.total_transactions} facts</div>
              ) : null}
            </div>
            <div className="table-shell">
              <table className="holdings-table holdings-side-table">
                <thead>
                  <tr>
                    <th>Trade Date</th>
                    <th>Type</th>
                    <th>Account</th>
                    <th>Quantity</th>
                    <th>Price</th>
                    <th>Gross Amount</th>
                    <th>Fees</th>
                    <th>Taxes</th>
                  </tr>
                </thead>
                <tbody>
                  {transactionsLoading ? (
                    <TableStatusRow colSpan={8} label="Loading linked transactions..." />
                  ) : transactionsError ? (
                    <TableStatusRow colSpan={8} label={transactionsError} tone="error" />
                  ) : selectedTransactions.length ? (
                    selectedTransactions.map((transaction) => (
                      <tr key={transaction.transaction_id}>
                        <td>{transaction.trade_date}</td>
                        <td>{formatLabel(transaction.transaction_type)}</td>
                        <td>{transactionAccountLabel(transaction)}</td>
                        <td>{formatQuantity(transaction.quantity)}</td>
                        <td>{formatUnitPrice(transaction.price, transaction.currency)}</td>
                        <td>{formatCurrency(transaction.gross_amount, transaction.currency)}</td>
                        <td>{formatCurrency(transaction.fees, transaction.currency)}</td>
                        <td>{formatCurrency(transaction.taxes, transaction.currency)}</td>
                      </tr>
                    ))
                  ) : (
                    <TableStatusRow colSpan={8} label="No linked transactions exist for this security as of the selected date." />
                  )}
                </tbody>
              </table>
            </div>
          </>
        ) : null}

        {detailTab === 'lots' ? (
          <>
            <div className="portfolio-detail-toolbar holdings-detail-toolbar">
              <div>
                <div className="panel-title">PositionLots</div>
                <div className="portfolio-detail-meta">{selectedRow?.asset_core.asset_name ?? assetId}</div>
              </div>
              {positionLotsLoading ? (
                <div className="portfolio-detail-meta">Refreshing...</div>
              ) : positionLotsWorkspace ? (
                <div className="portfolio-detail-meta">
                  {positionLotsWorkspace.summary.open_position_lot_count} open / {positionLotsWorkspace.summary.closed_position_lot_count} closed
                </div>
              ) : null}
            </div>
            <div className="table-shell">
              <table className="holdings-table position-lots-table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Status</th>
                    <th>Opened</th>
                    <th>Closed</th>
                    <th>Method</th>
                    <th>Entry Qty</th>
                    <th>Remaining Qty</th>
                    <th>Entry Cost</th>
                    <th>Remaining Cost</th>
                    <th>Income</th>
                    <th>Realized P/L</th>
                    <th>Unrealized P/L</th>
                    <th># Realizations</th>
                    <th>Holding Days</th>
                    <th># Txns</th>
                  </tr>
                </thead>
                <tbody>
                  {positionLotsLoading ? (
                    <TableStatusRow colSpan={15} label="Loading position lots..." />
                  ) : positionLotsError ? (
                    <TableStatusRow colSpan={15} label={positionLotsError} tone="error" />
                  ) : selectedPositionLots.length ? (
                    selectedPositionLots.map((positionLot) => (
                      <tr
                        key={positionLot.position_lot_id}
                        className={selectedPositionLot?.position_lot_id === positionLot.position_lot_id ? 'position-lots-row holdings-row-active' : 'position-lots-row'}
                        onClick={() => updateSearchParam('position_lot_id', positionLot.position_lot_id)}
                      >
                        <td>{positionLot.account_id}</td>
                        <td>
                          <span className={`coverage-pill ${lotStatusClass(positionLot)}`}>
                            {formatLabel(positionLot.status)}
                          </span>
                        </td>
                        <td>{positionLot.opened_at}</td>
                        <td>{positionLot.closed_at ?? '—'}</td>
                        <td>{formatLabel(positionLot.cost_basis_method)}</td>
                        <td>{formatQuantity(positionLot.entry_quantity)}</td>
                        <td>{formatQuantity(positionLot.remaining_quantity)}</td>
                        <td>{formatCurrency(positionLot.entry_cost_basis, positionLot.currency)}</td>
                        <td>{formatCurrency(positionLot.remaining_cost_basis, positionLot.currency)}</td>
                        <td>{formatCurrency(positionLot.income_cash_amount, positionLot.currency)}</td>
                        <td className={signedValueClass(positionLot.realized_pnl)}>
                          {formatSignedCurrency(positionLot.realized_pnl, positionLot.currency)}
                        </td>
                        <td className={signedValueClass(positionLot.unrealized_pnl)}>
                          {formatSignedCurrency(positionLot.unrealized_pnl, positionLot.currency)}
                        </td>
                        <td>{formatNumber(positionLot.realization_count, 0)}</td>
                        <td>{formatNumber(positionLot.holding_period_days)}</td>
                        <td>{formatNumber(positionLot.linked_transaction_count)}</td>
                      </tr>
                    ))
                  ) : (
                    <TableStatusRow colSpan={15} label="No position lots derived for this security yet." />
                  )}
                </tbody>
              </table>
            </div>
          </>
        ) : null}

        {detailTab === 'realizations' ? (
          <>
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
                {positionLotsLoading
                  ? 'Refreshing...'
                  : selectedPositionLot
                    ? `${selectedPositionLot.realization_count} matched exits`
                    : '0 matched exits'}
              </div>
            </div>
            <div className="table-shell">
              <table className="holdings-table position-lot-realizations-table">
                <thead>
                  <tr>
                    <th>Trade Date</th>
                    <th>Type</th>
                    <th>Quantity</th>
                    <th>Price</th>
                    <th>Proceeds</th>
                    <th>Cost Released</th>
                    <th>Realized P/L</th>
                    <th>Remaining Qty After</th>
                    <th>Remaining Cost After</th>
                    <th>Status After</th>
                  </tr>
                </thead>
                <tbody>
                  {positionLotsLoading ? (
                    <TableStatusRow colSpan={10} label="Loading realizations..." />
                  ) : positionLotsError ? (
                    <TableStatusRow colSpan={10} label={positionLotsError} tone="error" />
                  ) : selectedPositionLot?.realizations.length ? (
                    selectedPositionLot.realizations.map((realization) => (
                      <tr key={realization.realization_id}>
                        <td>{realization.trade_date}</td>
                        <td>{formatLabel(realization.transaction_type)}</td>
                        <td>{formatQuantity(realization.quantity)}</td>
                        <td>{formatUnitPrice(realization.price, selectedPositionLot.currency)}</td>
                        <td>{formatCurrency(realization.proceeds, selectedPositionLot.currency)}</td>
                        <td>{formatCurrency(realization.cost_basis_released, selectedPositionLot.currency)}</td>
                        <td className={signedValueClass(realization.realized_pnl)}>
                          {formatSignedCurrency(realization.realized_pnl, selectedPositionLot.currency)}
                        </td>
                        <td>{formatQuantity(realization.remaining_quantity_after)}</td>
                        <td>{formatCurrency(realization.remaining_cost_basis_after, selectedPositionLot.currency)}</td>
                        <td>
                          <span className={`coverage-pill ${realization.status_after === 'open' ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
                            {formatLabel(realization.status_after)}
                          </span>
                        </td>
                      </tr>
                    ))
                  ) : selectedPositionLot ? (
                    <TableStatusRow colSpan={10} label="No realized exits on this PositionLot yet." />
                  ) : (
                    <TableStatusRow colSpan={10} label="No PositionLot is available for this security." />
                  )}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
