import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatUnitPrice,
} from '../lib/format'
import {
  getHoldingsWorkspace,
  type HoldingsWorkspaceResponse,
  type PortfolioHoldingRow,
  type SparklinePoint,
} from '../lib/api'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'

type HoldingsSortField =
  | 'ticker'
  | 'name'
  | 'asset_type'
  | 'quantity'
  | 'last_price'
  | 'market_value'
  | 'cost_basis'
  | 'allocation'
  | 'day_change'
  | 'unrealized'
  | 'accounts'
  | 'lots'
  | 'coverage'

type HoldingsSortDirection = 'asc' | 'desc'

const HOLDINGS_SORT_FIELDS: HoldingsSortField[] = [
  'ticker',
  'name',
  'asset_type',
  'quantity',
  'last_price',
  'market_value',
  'cost_basis',
  'allocation',
  'day_change',
  'unrealized',
  'accounts',
  'lots',
  'coverage',
]

const TEXT_HOLDINGS_SORT_FIELDS = new Set<HoldingsSortField>(['ticker', 'name', 'asset_type', 'coverage'])

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.asset_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.asset_core.identifiers[0]?.identifier_value ??
    row.asset_core.asset_id
  )
}

function parseHoldingsSortField(value: string | null): HoldingsSortField {
  return HOLDINGS_SORT_FIELDS.includes(value as HoldingsSortField) ? (value as HoldingsSortField) : 'market_value'
}

function parseHoldingsSortDirection(value: string | null, field: HoldingsSortField): HoldingsSortDirection {
  if (value === 'asc' || value === 'desc') {
    return value
  }
  return TEXT_HOLDINGS_SORT_FIELDS.has(field) ? 'asc' : 'desc'
}

function nullableNumber(value: number | null | undefined) {
  return value ?? Number.NEGATIVE_INFINITY
}

function unrealizedBaseValue(row: PortfolioHoldingRow) {
  if (row.market_value_base != null && row.cost_basis_base != null) {
    return row.market_value_base - row.cost_basis_base
  }
  if (row.market_value != null && row.cost_basis != null) {
    return row.market_value - row.cost_basis
  }
  return null
}

function compareText(left: string, right: string, direction: HoldingsSortDirection) {
  const result = left.localeCompare(right, 'zh-Hans-CN')
  return direction === 'asc' ? result : -result
}

function compareNumber(left: number | null | undefined, right: number | null | undefined, direction: HoldingsSortDirection) {
  const result = nullableNumber(left) - nullableNumber(right)
  return direction === 'asc' ? result : -result
}

function compareHoldings(
  left: PortfolioHoldingRow,
  right: PortfolioHoldingRow,
  sortField: HoldingsSortField,
  sortDirection: HoldingsSortDirection,
) {
  switch (sortField) {
    case 'ticker':
      return compareText(primaryIdentifier(left), primaryIdentifier(right), sortDirection)
    case 'name':
      return compareText(left.asset_core.asset_name, right.asset_core.asset_name, sortDirection)
    case 'asset_type':
      return compareText(left.asset_core.asset_type, right.asset_core.asset_type, sortDirection)
    case 'quantity':
      return compareNumber(left.quantity, right.quantity, sortDirection)
    case 'last_price':
      return compareNumber(left.last_price, right.last_price, sortDirection)
    case 'cost_basis':
      return compareNumber(left.cost_basis_base ?? left.cost_basis, right.cost_basis_base ?? right.cost_basis, sortDirection)
    case 'allocation':
      return compareNumber(left.allocation, right.allocation, sortDirection)
    case 'day_change':
      return compareNumber(left.day_change_value, right.day_change_value, sortDirection)
    case 'unrealized':
      return compareNumber(unrealizedBaseValue(left), unrealizedBaseValue(right), sortDirection)
    case 'accounts':
      return compareNumber(left.account_count, right.account_count, sortDirection)
    case 'lots':
      return compareNumber(left.open_position_lot_count, right.open_position_lot_count, sortDirection)
    case 'coverage':
      return compareText(left.coverage_status, right.coverage_status, sortDirection)
    case 'market_value':
    default:
      return compareNumber(left.market_value_base ?? left.market_value, right.market_value_base ?? right.market_value, sortDirection)
  }
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

export default function PortfolioHomePage() {
  const navigate = useNavigate()
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspace, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const selectedAssetId = searchParams.get('asset_id')
  const holdingsSortField = parseHoldingsSortField(searchParams.get('holdings_sort_field') ?? searchParams.get('holdings_sort')?.replace(/_(asc|desc)$/, '') ?? null)
  const holdingsSortDirection = parseHoldingsSortDirection(
    searchParams.get('holdings_sort_direction') ?? searchParams.get('holdings_sort')?.match(/_(asc|desc)$/)?.[1] ?? null,
    holdingsSortField,
  )
  const sortedHoldingRows = useMemo(() => {
    const rows = workspace?.rows ?? []
    return rows.slice().sort((left, right) => {
      const primary = compareHoldings(left, right, holdingsSortField, holdingsSortDirection)
      return primary || primaryIdentifier(left).localeCompare(primaryIdentifier(right))
    })
  }, [workspace?.rows, holdingsSortField, holdingsSortDirection])

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

  function handleSelectAsset(assetId: string | null) {
    const normalizedAssetId = assetId?.trim() || null
    if (!normalizedAssetId || !portfolioId) {
      updateSearchParam('asset_id', null)
      return
    }
    const next = new URLSearchParams(searchParams)
    next.delete('asset_id')
    next.delete('position_lot_id')
    const query = next.toString()
    navigate(`${buildPortfolioHoldingDetailPath(portfolioId, normalizedAssetId)}${query ? `?${query}` : ''}`)
  }

  function handleHoldingsSort(field: HoldingsSortField) {
    const defaultDirection = TEXT_HOLDINGS_SORT_FIELDS.has(field) ? 'asc' : 'desc'
    const nextDirection =
      holdingsSortField === field ? (holdingsSortDirection === 'asc' ? 'desc' : 'asc') : defaultDirection
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('holdings_sort_field', field)
      next.set('holdings_sort_direction', nextDirection)
      next.delete('holdings_sort')
      return next
    })
  }

  function renderHoldingsSortHeader(field: HoldingsSortField, label: string) {
    const active = holdingsSortField === field
    return (
      <button
        type="button"
        className={`holdings-th-sortable ${active ? 'holdings-th-sortable-active' : ''}`}
        onClick={() => handleHoldingsSort(field)}
        aria-sort={active ? (holdingsSortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        <span>{label}</span>
        {active ? <span className="holdings-sort-indicator">{holdingsSortDirection === 'asc' ? '↑' : '↓'}</span> : null}
      </button>
    )
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
    if (!workspace || !selectedAssetId) {
      return
    }

    if (workspace.rows.some((row) => row.asset_core.asset_id === selectedAssetId)) {
      return
    }

    setSearchParams((current) => {
      if (current.get('asset_id') !== selectedAssetId) {
        return current
      }
      const next = new URLSearchParams(current)
      next.delete('asset_id')
      return next
    })
  }, [workspace, selectedAssetId, setSearchParams])

  const summaryCards = workspace?.summary_cards ?? []
  const selectedRow =
    selectedAssetId ? workspace?.rows.find((row) => row.asset_core.asset_id === selectedAssetId) ?? null : null

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
      <section className="portfolio-detail-surface holdings-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Holdings</div>
          {workspace ? <span className="portfolio-subhead-meta">As of {workspace.as_of_date}</span> : null}
        </div>
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
              <span>Open Asset</span>
              <select
                className="transaction-filter-input"
                value={selectedAssetId ?? ''}
                onChange={(event) => handleSelectAsset(event.target.value || null)}
                disabled={!workspace?.rows.length}
              >
                <option value="">Select a holding</option>
                {sortedHoldingRows.map((row) => (
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
                setSearchParams((current) => {
                  const next = new URLSearchParams(current)
                  next.delete('as_of_date')
                  next.delete('holdings_sort')
                  next.delete('holdings_sort_field')
                  next.delete('holdings_sort_direction')
                  next.delete('asset_id')
                  next.delete('position_lot_id')
                  return next
                })
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
                    <th>{renderHoldingsSortHeader('ticker', 'Ticker')}</th>
                    <th>{renderHoldingsSortHeader('name', 'Name')}</th>
                    <th>{renderHoldingsSortHeader('asset_type', 'Asset Type')}</th>
                    <th>{renderHoldingsSortHeader('quantity', 'Quantity')}</th>
                    <th>{renderHoldingsSortHeader('last_price', 'Last Price')}</th>
                    <th>{renderHoldingsSortHeader('market_value', 'Market Value')}</th>
                    <th>{renderHoldingsSortHeader('cost_basis', 'Cost Basis')}</th>
                    <th>{renderHoldingsSortHeader('allocation', 'Allocation')}</th>
                    <th>{renderHoldingsSortHeader('accounts', 'Accounts')}</th>
                    <th>{renderHoldingsSortHeader('lots', 'Open PositionLots')}</th>
                    <th>Price Chart</th>
                    <th>{renderHoldingsSortHeader('coverage', 'Coverage')}</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedHoldingRows.map((row) => {
                    const isActive = selectedRow?.asset_core.asset_id === row.asset_core.asset_id
                    return (
                      <tr
                        key={row.line_id}
                        className={isActive ? 'holdings-row-active' : undefined}
                        tabIndex={0}
                        onClick={() => handleSelectAsset(row.asset_core.asset_id)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            handleSelectAsset(row.asset_core.asset_id)
                          }
                        }}
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
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
