import { useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

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
  type SparklinePoint,
} from '../lib/api'

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.asset_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.asset_core.identifiers[0]?.identifier_value ??
    row.asset_core.asset_id
  )
}

function MiniSparkline({ values }: { values: SparklinePoint[] }) {
  if (values.length < 2) {
    return <span className="sparkline-empty">—</span>
  }

  const width = 88
  const height = 24
  const min = Math.min(...values.map((point) => point.value))
  const max = Math.max(...values.map((point) => point.value))
  const span = max - min || 1
  const line = values
    .map((point, index) => {
      const x = (index / (values.length - 1)) * (width - 1)
      const y = height - ((point.value - min) / span) * (height - 6) - 2
      return `${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' L ')

  const area = `${line} L ${width - 1} ${height} L 0 ${height} Z`

  return (
    <svg className="mini-sparkline" viewBox="0 0 88 24" aria-hidden="true">
      <path d={`M ${line}`} fill="none" stroke="#ef4444" strokeWidth="1.8" />
      <path d={`M ${area}`} fill="rgba(239, 68, 68, 0.14)" />
    </svg>
  )
}

function lotStatusClass(positionLot: PortfolioPositionLotRecord) {
  return positionLot.status === 'open' ? 'coverage-pill-live' : 'coverage-pill-warning'
}

function transactionAccountLabel(transaction: PortfolioTransactionRecord) {
  return transaction.account.account_name
}

function chunkMetrics<T>(items: T[], size: number) {
  const rows: T[][] = []
  for (let index = 0; index < items.length; index += size) {
    rows.push(items.slice(index, index + size))
  }
  return rows
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

export default function PortfolioHomePage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspace, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
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
  const selectedAssetId = searchParams.get('asset_id')
  const selectedPositionLotId = searchParams.get('position_lot_id')
  const chartRangeKey = (() => {
    const raw = searchParams.get('chart_range')
    if (raw === '1m' || raw === '3m' || raw === '6m' || raw === 'ytd' || raw === '1y' || raw === 'all') {
      return raw
    }
    return '6m'
  })() satisfies PortfolioAssetChartRangeKey
  const detailTab = (() => {
    const raw = searchParams.get('detail_tab')
    if (raw === 'transactions' || raw === 'lots' || raw === 'realizations') {
      return raw
    }
    return 'overview'
  })()

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

  useEffect(() => {
    if (!portfolioId) {
      setWorkspace(null)
      setError(null)
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)

    getHoldingsWorkspace(portfolioId || undefined, { as_of_date: requestedAsOfDate || undefined })
      .then((response) => {
        if (cancelled) {
          return
        }
        setWorkspace(response)
        setError(null)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : 'Failed to load holdings workspace.')
          setWorkspace(null)
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
  }, [portfolioId, requestedAsOfDate])

  useEffect(() => {
    if (!workspace) {
      return
    }

    const firstAssetId = workspace.rows[0]?.asset_core.asset_id ?? null
    if (selectedAssetId && workspace.rows.some((row) => row.asset_core.asset_id === selectedAssetId)) {
      return
    }
    updateSearchParam('asset_id', firstAssetId)
  }, [workspace, selectedAssetId])

  useEffect(() => {
    if (!selectedAssetId) {
      setPositionLotsWorkspace(null)
      setPositionLotsError(null)
      setPositionLotsLoading(false)
      if (selectedPositionLotId) {
        updateSearchParam('position_lot_id', null)
      }
      return
    }

    if (!positionLotsWorkspace) {
      return
    }

    const firstPositionLotId = positionLotsWorkspace.position_lots[0]?.position_lot_id ?? null
    if (
      selectedPositionLotId &&
      positionLotsWorkspace.position_lots.some((positionLot) => positionLot.position_lot_id === selectedPositionLotId)
    ) {
      return
    }
    updateSearchParam('position_lot_id', firstPositionLotId)
  }, [selectedAssetId, selectedPositionLotId, positionLotsWorkspace])

  useEffect(() => {
    if (!portfolioId || !selectedAssetId || !workspace?.as_of_date) {
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
      asset_id: selectedAssetId,
      as_of_date: workspace?.as_of_date,
    })
      .then((response) => {
        if (!cancelled) {
          setPositionLotsWorkspace(response)
          setPositionLotsError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPositionLotsError(
            requestError instanceof Error ? requestError.message : 'Failed to load position lots.',
          )
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
  }, [portfolioId, selectedAssetId, workspace?.as_of_date])

  useEffect(() => {
    if (!portfolioId || !selectedAssetId || !workspace?.as_of_date) {
      setAssetChartWorkspace(null)
      setAssetChartError(null)
      setAssetChartLoading(false)
      return
    }

    let cancelled = false
    setAssetChartWorkspace((current) =>
      current?.asset_core.asset_id === selectedAssetId ? current : null,
    )
    setAssetChartError(null)
    setAssetChartLoading(true)

    getPortfolioAssetPriceChart(portfolioId, selectedAssetId, {
      as_of_date: workspace.as_of_date,
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
  }, [portfolioId, selectedAssetId, workspace?.as_of_date, chartRangeKey])

  useEffect(() => {
    if (!portfolioId || !selectedAssetId || !workspace?.as_of_date) {
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
      asset_id: selectedAssetId,
      end_date: workspace.as_of_date,
    })
      .then((response) => {
        if (!cancelled) {
          setTransactionsWorkspace(response)
          setTransactionsError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setTransactionsError(
            requestError instanceof Error ? requestError.message : 'Failed to load linked transactions.',
          )
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
  }, [portfolioId, selectedAssetId, workspace?.as_of_date])

  const summaryCards = workspace?.summary_cards ?? []
  const selectedRow =
    workspace?.rows.find((row) => row.asset_core.asset_id === selectedAssetId) ?? workspace?.rows[0] ?? null
  const selectedPositionLots = positionLotsWorkspace?.position_lots ?? []
  const selectedTransactions = transactionsWorkspace?.transactions ?? []
  const selectedPositionLot =
    selectedPositionLots.find((positionLot) => positionLot.position_lot_id === selectedPositionLotId) ??
    selectedPositionLots[0] ??
    null
  const selectedRowUnrealizedBase =
    selectedRow?.market_value_base != null && selectedRow.cost_basis_base != null
      ? selectedRow.market_value_base - selectedRow.cost_basis_base
      : null
  const selectedRowUnrealizedLocal =
    selectedRow?.market_value != null && selectedRow.cost_basis != null
      ? selectedRow.market_value - selectedRow.cost_basis
      : null

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
          label: `Market Value (${workspace?.base_currency ?? selectedRow.asset_core.currency})`,
          value: formatCurrency(selectedRow.market_value_base ?? null, workspace?.base_currency ?? selectedRow.asset_core.currency),
        },
        {
          label: `Unrealized P/L (${selectedRow.asset_core.currency})`,
          value: formatSignedCurrency(selectedRowUnrealizedLocal, selectedRow.asset_core.currency),
        },
        {
          label: `Unrealized P/L (${workspace?.base_currency ?? selectedRow.asset_core.currency})`,
          value: formatSignedCurrency(selectedRowUnrealizedBase, workspace?.base_currency ?? selectedRow.asset_core.currency),
        },
        {
          label: 'Accounts / Lots',
          value: `${formatNumber(selectedRow.account_count ?? 0, 0)} / ${formatNumber(selectedRow.open_position_lot_count ?? 0, 0)}`,
        },
      ]
    : []
  const detailSummaryRows = chunkMetrics(detailSummaryMetrics, 2)
  const detailTabs = [
    { key: 'overview', label: 'Overview', meta: selectedRow ? selectedRow.asset_core.asset_type : '' },
    {
      key: 'transactions',
      label: 'Transactions',
      meta: transactionsLoading ? '…' : transactionsWorkspace ? String(transactionsWorkspace.summary.total_transactions) : '0',
    },
    {
      key: 'lots',
      label: 'PositionLots',
      meta: positionLotsLoading ? '…' : positionLotsWorkspace ? String(positionLotsWorkspace.summary.position_lot_count) : '0',
    },
    {
      key: 'realizations',
      label: 'Realizations',
      meta: positionLotsLoading ? '…' : selectedPositionLot ? String(selectedPositionLot.realization_count) : '0',
    },
  ] as const

  return (
    <PortfolioWorkspaceLayout
      activeSection="Holdings"
      toolbarLabel={workspace?.view_label ?? 'View: Holdings'}
      controls={
        summaryCards.length ? (
          <div className="table-shell">
            <table className="holdings-table holdings-summary-matrix">
              <thead>
                <tr>
                  {summaryCards.map((card) => (
                    <th key={card.label} className={card.tone === 'warning' ? 'summary-matrix-cell-warning' : undefined}>
                      {card.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  {summaryCards.map((card) => (
                    <td key={card.label} className={card.tone === 'warning' ? 'summary-matrix-cell-warning' : undefined}>
                      {card.value}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        ) : undefined
      }
    >
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Holdings</div>
          <div className="portfolio-detail-meta">Canonical positions from transactions</div>
        </div>
        {workspace ? (
          <div className="holdings-meta-row">
            <p className="coverage-note">{workspace.coverage_note}</p>
            <span className="portfolio-subhead-meta">As of {workspace.as_of_date}</span>
          </div>
        ) : null}
        <div className="transaction-filter-bar holdings-filter-bar">
          <div className="transaction-filter-group holdings-filter-group">
            <label>
              <span>As Of Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={requestedAsOfDate || workspace?.as_of_date || ''}
                onChange={(event) => updateSearchParam('as_of_date', event.target.value || null)}
              />
            </label>
            <label>
              <span>Focus Asset</span>
              <select
                className="transaction-filter-input"
                value={selectedAssetId ?? ''}
                onChange={(event) => updateSearchParam('asset_id', event.target.value || null)}
                disabled={!workspace?.rows.length}
              >
                {workspace?.rows.map((row) => (
                  <option key={row.asset_core.asset_id} value={row.asset_core.asset_id}>
                    {primaryIdentifier(row)} · {row.asset_core.asset_name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="transaction-filter-actions">
            <button
              type="button"
              className="toolbar-link"
              onClick={() => {
                updateSearchParam('as_of_date', null)
                updateSearchParam('asset_id', workspace?.rows[0]?.asset_core.asset_id ?? null)
              }}
            >
              Reset View
            </button>
          </div>
        </div>
        {loading ? <CalculationStatus label={workspace ? 'Recalculating…' : 'Loading…'} /> : null}
        {error ? <div className="error-state">{error}</div> : null}
        {!loading && !error && workspace ? (
          <>
            <div className="table-shell">
              <table className="holdings-table holdings-main-table">
                <thead>
                  <tr>
                    <th />
                    <th>Ticker</th>
                    <th>Name</th>
                    <th>Asset Type</th>
                    <th>Quantity</th>
                    <th>Last Price</th>
                    <th>Market Value</th>
                    <th>Cost Basis</th>
                    <th>Allocation</th>
                    <th>Accounts</th>
                    <th>Open PositionLots</th>
                    <th>Price Chart</th>
                    <th>Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {workspace.rows.map((row) => {
                    const isActive = selectedRow?.asset_core.asset_id === row.asset_core.asset_id
                    return (
                      <tr
                        key={row.line_id}
                        className={isActive ? 'holdings-row-active' : undefined}
                        onClick={() => updateSearchParam('asset_id', row.asset_core.asset_id)}
                      >
                        <td>
                          <span className="row-select" />
                        </td>
                        <td>
                          <span className="ticker-pill">{primaryIdentifier(row)}</span>
                        </td>
                        <td className="holding-name-cell">
                          <div className="holding-name-stack">
                            <span>{row.asset_core.asset_name}</span>
                            <span className="holding-secondary">{row.asset_core.currency}</span>
                          </div>
                        </td>
                        <td>{formatLabel(row.asset_core.asset_type)}</td>
                        <td>{formatQuantity(row.quantity)}</td>
                        <td>{formatUnitPrice(row.last_price, row.asset_core.currency)}</td>
                        <td>{formatCurrency(row.market_value, row.asset_core.currency)}</td>
                        <td>{formatCurrency(row.cost_basis, row.asset_core.currency)}</td>
                        <td>{formatPercent(row.allocation)}</td>
                        <td>{formatNumber(row.account_count ?? 0)}</td>
                        <td>{formatNumber(row.open_position_lot_count ?? 0)}</td>
                        <td>
                          {row.price_chart.length ? (
                            <MiniSparkline values={row.price_chart} />
                          ) : (
                            <span className="sparkline-empty">—</span>
                          )}
                        </td>
                        <td>
                          <span
                            className={`coverage-pill ${
                              row.coverage_status === 'price-nav-fx'
                                ? 'coverage-pill-live'
                                : 'coverage-pill-warning'
                            }`}
                          >
                            {formatLabel(row.coverage_status)}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                  <tr className="total-row">
                    <td colSpan={6}>Portfolio Total ({workspace.base_currency})</td>
                    <td>{formatCurrency(workspace.totals.market_value, workspace.base_currency)}</td>
                    <td>{formatCurrency(workspace.totals.cost_basis, workspace.base_currency)}</td>
                    <td>{formatPercent(workspace.totals.allocation)}</td>
                    <td />
                    <td />
                    <td />
                    <td>Derived</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="portfolio-detail-toolbar holdings-detail-toolbar">
              <div>
                <div className="panel-title">Security Detail</div>
                <div className="portfolio-detail-meta">
                  {selectedRow
                    ? `${selectedRow.asset_core.asset_name} (${primaryIdentifier(selectedRow)})`
                    : 'Select a holding'}
                </div>
              </div>
              {selectedRow ? (
                <div className="portfolio-detail-meta">
                  {formatLabel(selectedRow.asset_core.asset_type)} · {selectedRow.asset_core.currency} ·{' '}
                  {formatPercent(selectedRow.allocation)} of portfolio
                </div>
              ) : null}
            </div>

            {selectedRow ? (
              <>
                <div className="holdings-detail-tabbar">
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
                  <>
                    <section className="holdings-detail-panel holdings-chart-panel">
                      <div className="portfolio-detail-toolbar holdings-side-toolbar">
                        <div className="panel-title">Price Trend</div>
                        <div className="portfolio-detail-meta">
                          {assetChartLoading
                            ? 'Refreshing…'
                            : assetChartWorkspace?.summary.point_count
                              ? `${assetChartWorkspace.summary.point_count} points`
                              : 'No chart history'}
                        </div>
                      </div>
                      <AssetPriceChart
                        chart={assetChartWorkspace}
                        loading={assetChartLoading}
                        error={assetChartError}
                        rangeKey={chartRangeKey}
                        onRangeChange={(rangeKey) => updateSearchParam('chart_range', rangeKey)}
                      />
                    </section>

                    <div className="holdings-detail-grid">
                      <section className="holdings-detail-panel">
                        <div className="portfolio-detail-toolbar holdings-side-toolbar">
                          <div className="panel-title">Statement Summary</div>
                          <div className="portfolio-detail-meta">
                            {selectedRow.asset_core.currency} / {workspace?.base_currency ?? selectedRow.asset_core.currency}
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
                              {detailSummaryRows.map((metrics, index) => (
                                <tr key={`summary-row-${index}`}>
                                  <th scope="row">{metrics[0]?.label ?? '—'}</th>
                                  <td>{metrics[0]?.value ?? '—'}</td>
                                  <th scope="row">{metrics[1]?.label ?? '—'}</th>
                                  <td>{metrics[1]?.value ?? '—'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </section>

                      <section className="holdings-detail-panel">
                        <div className="portfolio-detail-toolbar holdings-side-toolbar">
                          <div className="panel-title">Account Slices</div>
                          <div className="portfolio-detail-meta">
                            {positionLotsLoading ? 'Refreshing…' : `${accountSlices.length} accounts`}
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
                                <TableStatusRow colSpan={5} label="Loading account slices…" />
                              ) : positionLotsError ? (
                                <TableStatusRow colSpan={5} label={positionLotsError} tone="error" />
                              ) : accountSlices.length ? (
                                accountSlices.map((slice) => (
                                  <tr key={slice.accountId}>
                                    <td>{slice.accountId}</td>
                                    <td>{formatQuantity(slice.quantity)}</td>
                                    <td>{formatCurrency(slice.marketValue, selectedRow.asset_core.currency)}</td>
                                    <td>{formatCurrency(slice.remainingCost, selectedRow.asset_core.currency)}</td>
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
                  </>
                ) : null}
              </>
            ) : null}

            {detailTab === 'transactions' ? (
              <>
                <div className="portfolio-detail-toolbar holdings-detail-toolbar">
                  <div>
                    <div className="panel-title">Linked Transactions</div>
                    <div className="portfolio-detail-meta">
                      {selectedRow
                        ? `All recorded transactions for ${primaryIdentifier(selectedRow)} up to ${workspace.as_of_date}`
                        : 'Select a holding'}
                    </div>
                  </div>
                  {transactionsLoading ? (
                    <div className="portfolio-detail-meta">Refreshing…</div>
                  ) : transactionsWorkspace ? (
                    <div className="portfolio-detail-meta">
                      {transactionsWorkspace.summary.total_transactions} facts
                    </div>
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
                        <TableStatusRow colSpan={8} label="Loading linked transactions…" />
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
                        <TableStatusRow
                          colSpan={8}
                          label="No linked transactions exist for the selected holding as of this statement date."
                        />
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
                    <div className="portfolio-detail-meta">
                      {selectedRow
                        ? `${selectedRow.asset_core.asset_name} (${primaryIdentifier(selectedRow)})`
                        : 'Select a holding'}
                    </div>
                  </div>
                  {positionLotsLoading ? (
                    <div className="portfolio-detail-meta">Refreshing…</div>
                  ) : positionLotsWorkspace ? (
                    <div className="portfolio-detail-meta">
                      {positionLotsWorkspace.summary.open_position_lot_count} open /{' '}
                      {positionLotsWorkspace.summary.closed_position_lot_count} closed
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
                        <TableStatusRow colSpan={15} label="Loading position lots…" />
                      ) : positionLotsError ? (
                        <TableStatusRow colSpan={15} label={positionLotsError} tone="error" />
                      ) : selectedPositionLots.length ? (
                        selectedPositionLots.map((positionLot) => (
                          <tr
                            key={positionLot.position_lot_id}
                            className={
                              selectedPositionLot?.position_lot_id === positionLot.position_lot_id
                                ? 'position-lots-row holdings-row-active'
                                : 'position-lots-row'
                            }
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
                            <td>{formatSignedCurrency(positionLot.realized_pnl, positionLot.currency)}</td>
                            <td>{formatSignedCurrency(positionLot.unrealized_pnl, positionLot.currency)}</td>
                            <td>{formatNumber(positionLot.realization_count, 0)}</td>
                            <td>{formatNumber(positionLot.holding_period_days)}</td>
                            <td>{formatNumber(positionLot.linked_transaction_count)}</td>
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow colSpan={15} label="No position lots derived for the selected holding yet." />
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
                      ? 'Refreshing…'
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
                        <TableStatusRow colSpan={10} label="Loading realizations…" />
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
                            <td>{formatSignedCurrency(realization.realized_pnl, selectedPositionLot.currency)}</td>
                            <td>{formatQuantity(realization.remaining_quantity_after)}</td>
                            <td>{formatCurrency(realization.remaining_cost_basis_after, selectedPositionLot.currency)}</td>
                            <td>
                              <span
                                className={`coverage-pill ${
                                  realization.status_after === 'open'
                                    ? 'coverage-pill-live'
                                    : 'coverage-pill-warning'
                                }`}
                              >
                                {formatLabel(realization.status_after)}
                              </span>
                            </td>
                          </tr>
                        ))
                      ) : selectedPositionLot ? (
                        <TableStatusRow colSpan={10} label="No realized exits on this PositionLot yet." />
                      ) : (
                        <TableStatusRow colSpan={10} label="No PositionLot is available for the selected holding." />
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
