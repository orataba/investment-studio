import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'

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
  getPortfolioPositionHoldingProjection,
  getPortfolioInstrumentPriceChart,
  getPortfolioPositionLots,
  getPortfolioTransactions,
  type PortfolioInstrumentChartRangeKey,
  type PortfolioPositionHoldingRow,
  type PortfolioPositionHoldingProjectionResponse,
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
  holdingUsesEventValuation,
  isOptionObligationHolding,
} from '../lib/holdingPresentation'
import { transactionActivityLabel } from '../lib/transactionPresentation'
import {
  buildPortfolioSectionPath,
  buildWatchlistInstrumentDetailUrl,
} from '../lib/navigation'

type SecurityDetailTab = 'overview' | 'transactions' | 'lots'

function primaryIdentifier(row: PortfolioPositionHoldingRow) {
  if (!row.instrument_core) {
    return row.derivative_contract_id ?? row.position_reference_id ?? row.line_id
  }
  return (
    row.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.instrument_core.identifiers[0]?.identifier_value ??
    row.instrument_core.instrument_id
  )
}

function holdingReferenceId(row: PortfolioPositionHoldingRow) {
  return row.position_reference_id ?? row.derivative_contract_id ?? row.instrument_core?.instrument_id ?? row.line_id
}

function holdingName(row: PortfolioPositionHoldingRow) {
  return row.derivative_contract?.contract_name ?? row.instrument_core?.instrument_name ?? row.line_id
}

function holdingCurrency(row: PortfolioPositionHoldingRow) {
  return row.derivative_contract?.currency ?? row.instrument_core?.currency ?? ''
}

function holdingAssetType(row: PortfolioPositionHoldingRow) {
  return row.derivative_contract?.contract_type ?? row.instrument_core?.instrument_type ?? 'other'
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
  if (value === 'realizations') {
    return 'lots'
  }
  return 'overview'
}

function transactionAccountLabel(transaction: PortfolioTransactionRecord) {
  return transaction.account.account_name
}

function lotStatusClass(positionLot: PortfolioPositionLotRecord) {
  return positionLot.status === 'open' ? 'coverage-pill-live' : 'coverage-pill-warning'
}

function holdingPeriodLabel(positionLot: PortfolioPositionLotRecord) {
  if (positionLot.closed_at) {
    return `Closed ${positionLot.closed_at}`
  }
  if (positionLot.holding_period_days == null) {
    return 'Holding period unavailable'
  }
  return `${formatNumber(Math.max(0, Math.round(positionLot.holding_period_days)), 0)} days held`
}

function costBasisMethodLabel(method: string | null | undefined) {
  if (method === 'fifo') {
    return 'FIFO'
  }
  if (method === 'moving_average') {
    return 'Moving average'
  }
  return formatLabel(method ?? 'unknown')
}

function countLabel(count: number, singular: string, plural = `${singular}s`) {
  return `${formatNumber(count, 0)} ${count === 1 ? singular : plural}`
}

function quoteProviderLabel(provider: string | null | undefined) {
  return provider?.split('|', 1)[0]?.trim() || '—'
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
  const [workspaceResponse, setWorkspace] = useState<PortfolioPositionHoldingProjectionResponse | null>(null)
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
    workspaceResponse.rows.every(
      (row) => holdingReferenceId(row) === instrumentId,
    ) &&
    (!requestedAsOfDate || workspaceResponse.as_of_date === requestedAsOfDate)
      ? workspaceResponse
      : null
  const positionLotsWorkspace =
    positionLotsResponse?.portfolio_id === portfolioId &&
    positionLotsResponse.position_lots.every((positionLot) => positionLot.position_reference_id === instrumentId)
      ? positionLotsResponse
      : null
  const transactionsWorkspace =
    transactionsResponse?.portfolio_id === portfolioId &&
    transactionsResponse.transactions.every(
      (transaction) =>
        (transaction.derivative_contract_id ?? transaction.instrument_id) === instrumentId,
    )
      ? transactionsResponse
      : null
  const selectedRows = workspace?.rows ?? []
  const selectedRow =
    selectedRows.find((row) => row.holding_kind === 'position') ??
    selectedRows[0] ??
    null
  const writtenOptionObligation =
    selectedRows.find((row) => isOptionObligationHolding(row)) ?? null
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
  const baseCurrency = workspace?.base_currency ?? (selectedRow ? holdingCurrency(selectedRow) : null) ?? instrumentChartWorkspace?.currency ?? 'USD'
  const selectedRowIdentifier = selectedRow ? primaryIdentifier(selectedRow) : instrumentId
  const selectedRowUsesEventValuation = selectedRow
    ? holdingUsesEventValuation(selectedRow)
    : false
  const selectedRowUnrealizedBase =
    !selectedRowUsesEventValuation &&
    selectedRow?.market_value_base != null && selectedRow.cost_basis_base != null
      ? selectedRow.market_value_base - selectedRow.cost_basis_base
      : null
  const selectedRowUnrealizedLocal =
    !selectedRowUsesEventValuation &&
    selectedRow?.market_value != null && selectedRow.cost_basis != null
      ? selectedRow.market_value - selectedRow.cost_basis
      : null
  const heroMarketValue = selectedRowUsesEventValuation
    ? selectedRow?.carrying_value_base ?? selectedRow?.carrying_value
    : selectedRow?.market_value_base ?? selectedRow?.market_value
  const heroMarketCurrency = selectedRowUsesEventValuation
    ? selectedRow?.carrying_value_base != null
      ? baseCurrency
      : selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency
    : selectedRow?.market_value_base != null
      ? baseCurrency
      : selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency
  const heroUnrealizedValue = selectedRowUnrealizedBase ?? selectedRowUnrealizedLocal
  const heroUnrealizedCurrency = selectedRowUnrealizedBase != null ? baseCurrency : selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency
  const valuationMetricName = valuationQuoteLabel(
    selectedRow?.quote_basis ?? selectedRow?.quote_metric_family,
  )
  const performanceMetricName = performanceSeriesLabel(
    instrumentChartWorkspace?.chart_basis ?? instrumentChartWorkspace?.metric_family,
  )
  const positionLotsPending =
    workspaceLoading ||
    positionLotsLoading ||
    Boolean(resolvedAsOfDate && !positionLotsWorkspace && !positionLotsError)
  const transactionsPending =
    workspaceLoading ||
    transactionsLoading ||
    Boolean(resolvedAsOfDate && !transactionsWorkspace && !transactionsError)
  const instrumentChartPending =
    detailTab === 'overview' &&
    (
      workspaceLoading ||
      (
        !selectedRowUsesEventValuation &&
        (
          instrumentChartLoading ||
          Boolean(selectedRow && resolvedAsOfDate && !instrumentChartWorkspace && !instrumentChartError)
        )
      )
    )
  const totalRealizationCount = selectedPositionLots.reduce(
    (total, positionLot) => total + positionLot.realization_count,
    0,
  )
  const hasPositionLotFacts = selectedPositionLots.length > 0
  const remainingLotQuantity = hasPositionLotFacts
    ? selectedPositionLots.reduce(
        (total, positionLot) => total + positionLot.remaining_quantity,
        0,
      )
    : null
  const remainingLotCost = hasPositionLotFacts
    ? selectedPositionLots.reduce(
        (total, positionLot) => total + positionLot.remaining_cost_basis,
        0,
      )
    : null
  const lotMarketValueComplete = selectedPositionLots.every(
    (positionLot) =>
      positionLot.current_market_value != null ||
      Math.abs(positionLot.remaining_quantity) <= 1e-9,
  )
  const lotMarketValue = hasPositionLotFacts && lotMarketValueComplete
    ? selectedPositionLots.reduce(
        (total, positionLot) => total + (positionLot.current_market_value ?? 0),
        0,
      )
    : null
  const realizedLotPnl = hasPositionLotFacts
    ? selectedPositionLots.reduce(
        (total, positionLot) => total + positionLot.realized_pnl,
        0,
      )
    : null
  const holdingSinceDate =
    selectedRow?.instrument_holding_start_date ??
    selectedPositionLots
      .filter((positionLot) => positionLot.status === 'open')
      .map((positionLot) => positionLot.opened_at)
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right))[0] ??
    null
  const heroCostBasis = selectedRow?.cost_basis_base ?? selectedRow?.cost_basis
  const heroCostCurrency =
    selectedRow?.cost_basis_base != null
      ? baseCurrency
      : selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency
  const obligationLiability =
    writtenOptionObligation?.liability_value_base ??
    writtenOptionObligation?.liability_value ??
    null
  const obligationLiabilityCurrency =
    writtenOptionObligation?.liability_value_base != null
      ? baseCurrency
      : writtenOptionObligation ? holdingCurrency(writtenOptionObligation) || baseCurrency : baseCurrency

  const detailTabs = [
    { key: 'overview', label: 'Overview', meta: 'Position' },
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
      label: 'Position Lots',
      meta: positionLotsPending
        ? 'Loading'
        : positionLotsWorkspace
          ? String(positionLotsWorkspace.summary.position_lot_count)
          : '—',
    },
  ] as const

  const backToHoldingsPath = useMemo(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('detail_tab')
    next.delete('chart_range')
    next.delete('position_lot_id')
    if (instrumentId) {
      next.set('position_reference_id', instrumentId)
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
  const filteredTransactionsPath = useMemo(() => {
    const next = new URLSearchParams()
    next.set('position_reference_id', instrumentId)
    if (resolvedAsOfDate) {
      next.set('end_date', resolvedAsOfDate)
    }
    return `${buildPortfolioSectionPath(portfolioId, '/transactions')}?${next.toString()}`
  }, [instrumentId, portfolioId, resolvedAsOfDate])

  const accountSlices = useMemo(() => {
    return aggregatePositionLotAccountSlices(
      selectedPositionLots.filter((positionLot) => positionLot.status === 'open'),
    )
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

    getPortfolioPositionHoldingProjection(portfolioId, instrumentId, {
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
      position_reference_id: instrumentId,
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
      position_reference_id: instrumentId,
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
    if (
      detailTab !== 'overview' ||
      !portfolioId ||
      !instrumentId ||
      !resolvedAsOfDate ||
      !selectedRow ||
      !selectedRow.instrument_core ||
      selectedRowUsesEventValuation
    ) {
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
  }, [
    chartRangeKey,
    detailTab,
    instrumentId,
    portfolioId,
    resolvedAsOfDate,
    selectedRow,
    selectedRowUsesEventValuation,
  ])

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
        <header className="portfolio-security-header">
          <div className="portfolio-security-detail-nav">
            <Link className="portfolio-security-back-link" to={backToHoldingsPath}>
              <span aria-hidden="true">←</span>
              Holdings
            </Link>
            {selectedRow?.instrument_core ? (
              <a className="portfolio-security-secondary-link" href={watchlistDetailUrl}>
                View instrument research
              </a>
            ) : null}
          </div>

          <div className="portfolio-security-hero">
            <div className="portfolio-security-title-stack">
              <span className="portfolio-security-eyebrow">Holding detail</span>
              <h1>{selectedRow ? holdingName(selectedRow) : instrumentChartWorkspace?.instrument_core.instrument_name ?? instrumentId}</h1>
              <div className="portfolio-security-meta-row">
                <span className="ticker-pill">{selectedRowIdentifier}</span>
                <span>{selectedRow ? formatLabel(holdingAssetType(selectedRow)) : 'Instrument'}</span>
                <span>{selectedRow ? holdingCurrency(selectedRow) : instrumentChartWorkspace?.currency ?? '—'}</span>
                <span>As of {resolvedAsOfDate || '—'}</span>
              </div>
            </div>
            <div className="portfolio-security-hero-metrics">
              <div>
                <span>Portfolio weight</span>
                <strong>{formatPercent(selectedRow?.allocation)}</strong>
              </div>
              <div>
                <span>Quantity</span>
                <strong>{formatQuantity(selectedRow?.quantity)}</strong>
              </div>
              <div>
                <span>{selectedRowUsesEventValuation ? 'Carrying value' : 'Market value'}</span>
                <strong>{formatCurrency(heroMarketValue, heroMarketCurrency)}</strong>
              </div>
              <div>
                <span>Unrealized P/L</span>
                <strong className={selectedRowUsesEventValuation ? undefined : signedValueClass(heroUnrealizedValue)}>
                  {selectedRowUsesEventValuation
                    ? 'N/A'
                    : formatSignedCurrency(heroUnrealizedValue, heroUnrealizedCurrency)}
                </strong>
              </div>
            </div>
          </div>

          <div className="portfolio-security-context-strip">
            <div>
              <span>Valuation</span>
              <strong>
                {selectedRowUsesEventValuation
                  ? 'N/A'
                  : formatUnitPrice(selectedRow?.last_price, selectedRow ? holdingCurrency(selectedRow) : '')}
              </strong>
              <em>
                {valuationMetricName} ·{' '}
                {selectedRowUsesEventValuation
                  ? 'N/A'
                  : (selectedRow?.quote_as_of_date ?? resolvedAsOfDate) || '—'}
              </em>
            </div>
            <div>
              <span>Open cost basis</span>
              <strong>{formatCurrency(heroCostBasis, heroCostCurrency)}</strong>
              <em>{costBasisMethodLabel(selectedRow?.cost_basis_method)} method</em>
            </div>
            <div>
              <span>Held since</span>
              <strong>{holdingSinceDate ?? '—'}</strong>
              <em>Current-position history</em>
            </div>
            <div>
              <span>Accounts / open lots</span>
              <strong>
                {formatNumber(selectedRow?.account_count ?? 0, 0)} /{' '}
                {formatNumber(selectedRow?.open_position_lot_count ?? 0, 0)}
              </strong>
              <em>{formatLabel(selectedRow?.coverage_status ?? 'unavailable')} coverage</em>
            </div>
          </div>
          {writtenOptionObligation ? (
            <div
              className="portfolio-security-context-strip"
              aria-label="Short option position"
            >
              <div>
                <span>Obligation status</span>
                <strong>
                  {writtenOptionObligation.obligation_status
                    ? formatLabel(writtenOptionObligation.obligation_status)
                    : 'N/A'}
                </strong>
                <em>
                  Short {formatLabel(writtenOptionObligation.option_type ?? 'option')}
                </em>
              </div>
              <div>
                <span>Open contracts</span>
                <strong>{formatQuantity(writtenOptionObligation.open_contract_quantity)}</strong>
                <em>Exchange contracts</em>
              </div>
              <div>
                <span>Underlying units at expiry</span>
                <strong>{formatQuantity(writtenOptionObligation.required_underlying_quantity)}</strong>
                <em>Contracts × multiplier</em>
              </div>
              <div>
                <span>Remaining premium / carrying liability</span>
                <strong>
                  {formatCurrency(
                    writtenOptionObligation.premium_basis_remaining,
                    holdingCurrency(writtenOptionObligation),
                  )}{' '}
                  / {formatCurrency(obligationLiability, obligationLiabilityCurrency)}
                </strong>
                <em>Premium basis / liability</em>
              </div>
            </div>
          ) : null}
        </header>

        {workspaceLoading ? <CalculationStatus /> : null}
        {workspaceError ? <div className="error-state">{workspaceError}</div> : null}
        {!workspaceLoading && !workspaceError && workspace && !selectedRows.length ? (
          <div className="empty-state" role="status">Not held as of selected date.</div>
        ) : null}

        <div className="holdings-detail-tabbar portfolio-security-tabs" role="tablist" aria-label="Instrument detail">
          {detailTabs.map((tab) => {
            const isActive = detailTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                id={`portfolio-security-tab-${tab.key}`}
                role="tab"
                aria-label={`${tab.label} ${tab.meta}`}
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
            className="portfolio-security-tab-panel portfolio-security-overview"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-overview"
          >
            <div className="portfolio-security-overview-workbench">
              <section className="portfolio-security-chart-panel">
                <InstrumentPriceChart
                  chart={instrumentChartWorkspace}
                  loading={instrumentChartPending}
                  error={instrumentChartError}
                  rangeKey={chartRangeKey}
                  onRangeChange={(rangeKey) => updateSearchParam('chart_range', rangeKey)}
                  variant="instrument"
                />
              </section>

              <aside className="portfolio-security-position-brief">
                <div className="portfolio-security-section-head">
                  <div>
                    <span className="portfolio-security-section-kicker">Position basis</span>
                    <h2>Current position</h2>
                  </div>
                  <span>{resolvedAsOfDate || '—'}</span>
                </div>
                <dl className="portfolio-security-position-facts">
                  <div>
                    <dt>Quantity</dt>
                    <dd>{formatQuantity(selectedRow?.quantity)}</dd>
                  </div>
                  <div>
                    <dt>Cost basis</dt>
                    <dd>{formatCurrency(heroCostBasis, heroCostCurrency)}</dd>
                  </div>
                  <div>
                    <dt>Valuation basis</dt>
                    <dd>{valuationMetricName}</dd>
                  </div>
                  <div>
                    <dt>Performance basis</dt>
                    <dd>{performanceMetricName}</dd>
                  </div>
                  <div>
                    <dt>Chart coverage</dt>
                    <dd>
                      {instrumentChartWorkspace
                        ? countLabel(instrumentChartWorkspace.summary.point_count, 'observation')
                        : '—'}
                    </dd>
                  </div>
                  <div>
                    <dt>Quote provider</dt>
                    <dd title={selectedRow?.quote_provider ?? undefined}>
                      {quoteProviderLabel(selectedRow?.quote_provider)}
                    </dd>
                  </div>
                </dl>
                <p className="portfolio-security-basis-note">
                  {selectedRowUsesEventValuation
                    ? 'This position uses event carrying basis; fair value and market-return analytics are unavailable.'
                    : `Market value uses ${valuationMetricName}. Return analysis uses ${performanceMetricName}; the performance series never replaces the valuation quote.`}
                </p>
              </aside>
            </div>

            <section className="portfolio-security-accounts">
              <div className="portfolio-security-section-head">
                <div>
                  <span className="portfolio-security-section-kicker">Custody</span>
                  <h2>Account positions</h2>
                </div>
                <span>{positionLotsPending ? 'Loading' : countLabel(accountSlices.length, 'account')}</span>
              </div>
              <div className="portfolio-security-account-list">
                {positionLotsPending ? (
                  <div className="empty-state">Loading account positions.</div>
                ) : positionLotsError ? (
                  <div className="empty-state table-status-cell-error">{positionLotsError}</div>
                ) : accountSlices.length ? accountSlices.map((slice) => {
                  const sliceUnrealized =
                    selectedRowUsesEventValuation || slice.marketValue == null
                      ? null
                      : slice.marketValue - slice.remainingCost
                  return (
                    <article key={slice.accountId}>
                      <div className="portfolio-security-account-head">
                        <div>
                          <strong>{accountNameById.get(slice.accountId) ?? slice.accountId}</strong>
                          <span>{countLabel(slice.openPositionLotCount, 'open lot')}</span>
                        </div>
                        <strong>
                          {formatCurrency(slice.marketValue, selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency)}
                        </strong>
                      </div>
                      <dl>
                        <div><dt>Quantity</dt><dd>{formatQuantity(slice.quantity)}</dd></div>
                        <div><dt>Remaining cost</dt><dd>{formatCurrency(slice.remainingCost, selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency)}</dd></div>
                        <div>
                          <dt>Unrealized P/L</dt>
                          <dd className={selectedRowUsesEventValuation ? undefined : signedValueClass(sliceUnrealized)}>
                            {selectedRowUsesEventValuation
                              ? 'N/A'
                              : formatSignedCurrency(
                                  sliceUnrealized,
                                  selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency,
                                )}
                          </dd>
                        </div>
                      </dl>
                    </article>
                  )
                }) : (
                  <div className="empty-state">No account positions.</div>
                )}
              </div>
            </section>
          </div>
        ) : null}

        {detailTab === 'transactions' ? (
          <section
            id="portfolio-security-panel-transactions"
            className="portfolio-security-tab-panel"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-transactions"
          >
            <div className="portfolio-security-panel-head">
              <div>
                <span className="portfolio-security-section-kicker">Instrument activity</span>
                <h2>Transactions</h2>
                <p>Confirmed economic facts through {resolvedAsOfDate || '—'}.</p>
              </div>
              <div className="portfolio-security-panel-actions">
                <span>
                  {transactionsPending
                    ? 'Loading'
                    : transactionsWorkspace
                      ? countLabel(transactionsWorkspace.summary.total_transactions, 'fact')
                      : '—'}
                </span>
                <Link className="portfolio-security-secondary-link" to={filteredTransactionsPath}>
                  Open transaction ledger
                </Link>
              </div>
            </div>
            <div className="table-shell portfolio-security-table-shell">
              <table className="holdings-table portfolio-security-transactions-table">
                <thead>
                  <tr>
                    <th>Trade</th>
                    <th>Activity</th>
                    <th>Account</th>
                    <th>Quantity / Price</th>
                    <th>Gross / Net Cash</th>
                    <th>Recognition</th>
                  </tr>
                </thead>
                <tbody>
                  {transactionsPending ? (
                    <TableStatusRow colSpan={6} label="Loading" />
                  ) : transactionsError ? (
                    <TableStatusRow colSpan={6} label={transactionsError} tone="error" />
                  ) : selectedTransactions.length ? (
                    selectedTransactions.map((transaction) => {
                      const activityLabel = transactionActivityLabel(
                        transaction.transaction_type,
                        transaction.instrument_ref?.instrument_type,
                        transaction.option_action,
                      )
                      return (
                        <tr key={transaction.transaction_id}>
                          <td>
                            <div className="holding-name-stack">
                              <Link className="table-inline-link" to={`${buildPortfolioSectionPath(portfolioId, '/transactions')}?transaction_id=${encodeURIComponent(transaction.transaction_id)}`}>
                                {transaction.trade_date}
                              </Link>
                              <span className="holding-secondary">
                                {transaction.trade_time}
                                {transaction.trade_time_is_estimated ? ' · estimated' : ''}
                              </span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span className="transaction-type-pill">{activityLabel}</span>
                              <span className="holding-secondary">{transaction.transaction_id}</span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span>{transactionAccountLabel(transaction)}</span>
                              <span className="holding-secondary">
                                {transaction.settlement_cash_account
                                  ? `Settle via ${transaction.settlement_cash_account.account_name}`
                                  : 'No settlement account'}
                              </span>
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
                          <td>
                            <div className="holding-name-stack">
                              <span>
                                {transaction.position_effective_date
                                  ? `Position EOD ${transaction.position_effective_date}`
                                  : `Economic ${transaction.economic_date}`}
                              </span>
                              <span className="holding-secondary">Settle {transaction.settlement_date}</span>
                            </div>
                          </td>
                        </tr>
                      )
                    })
                  ) : (
                    <TableStatusRow colSpan={6} label="No transactions." />
                  )}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {detailTab === 'lots' ? (
          <section
            id="portfolio-security-panel-lots"
            className="portfolio-security-tab-panel"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-lots"
          >
            <div className="portfolio-security-panel-head">
              <div>
                <span className="portfolio-security-section-kicker">Cost-basis ledger</span>
                <h2>Position lots</h2>
                <p>Acquisition batches and matched exits for the current instrument.</p>
              </div>
              <span>
                {positionLotsPending
                  ? 'Loading'
                  : positionLotsWorkspace
                    ? `${positionLotsWorkspace.summary.open_position_lot_count} open · ${positionLotsWorkspace.summary.closed_position_lot_count} closed`
                    : '—'}
              </span>
            </div>

            <div className="portfolio-security-lot-summary">
              <div>
                <span>Market value</span>
                <strong>
                  {formatCurrency(lotMarketValue, selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency)}
                </strong>
                <em>
                  {countLabel(selectedPositionLots.length, 'lot')} ·{' '}
                  {countLabel(totalRealizationCount, 'matched exit')}
                </em>
              </div>
              <div>
                <span>Remaining quantity</span>
                <strong>{formatQuantity(remainingLotQuantity)}</strong>
                <em>Across open lots</em>
              </div>
              <div>
                <span>Remaining cost</span>
                <strong>{formatCurrency(remainingLotCost, selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency)}</strong>
                <em>Current cost basis</em>
              </div>
              <div>
                <span>Realized P/L</span>
                <strong className={signedValueClass(realizedLotPnl)}>
                  {formatSignedCurrency(realizedLotPnl, selectedRow ? holdingCurrency(selectedRow) || baseCurrency : baseCurrency)}
                </strong>
                <em>Through {resolvedAsOfDate || '—'}</em>
              </div>
            </div>

            <div className="position-lot-workbench">
              <div className="table-shell portfolio-security-table-shell">
                <table className="holdings-table position-lots-table portfolio-security-lots-table">
                  <thead>
                    <tr>
                      <th>Lot / Account</th>
                      <th>Opened / Status</th>
                      <th>Remaining Position</th>
                      <th>Cost Basis</th>
                      <th>P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positionLotsPending ? (
                      <TableStatusRow colSpan={5} label="Loading" />
                    ) : positionLotsError ? (
                      <TableStatusRow colSpan={5} label={positionLotsError} tone="error" />
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
                              <strong>{positionLot.position_lot_id}</strong>
                              <span className="holding-secondary">
                                {accountNameById.get(positionLot.account_id) ?? positionLot.account_id}
                              </span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span>{positionLot.opened_at}</span>
                              <span>
                                <span className={`coverage-pill ${lotStatusClass(positionLot)}`}>
                                  {formatLabel(positionLot.status)}
                                </span>
                                <span className="holding-secondary portfolio-security-inline-meta">
                                  {holdingPeriodLabel(positionLot)}
                                </span>
                              </span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span>{formatQuantity(positionLot.remaining_quantity)}</span>
                              <span className="holding-secondary">
                                {formatCurrency(positionLot.current_market_value, positionLot.currency)}
                              </span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span>{formatCurrency(positionLot.remaining_cost_basis, positionLot.currency)}</span>
                              <span className="holding-secondary">
                                Entry {formatCurrency(positionLot.entry_cost_basis, positionLot.currency)}
                              </span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span className={selectedRowUsesEventValuation ? undefined : signedValueClass(positionLot.unrealized_pnl)}>
                                {selectedRowUsesEventValuation
                                  ? 'N/A'
                                  : formatSignedCurrency(positionLot.unrealized_pnl, positionLot.currency)}
                              </span>
                              <span className={`${signedValueClass(positionLot.realized_pnl)} holding-secondary`}>
                                Realized {formatSignedCurrency(positionLot.realized_pnl, positionLot.currency)}
                              </span>
                            </div>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <TableStatusRow colSpan={5} label="No lots." />
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
                    <div className="position-lot-inspector-primary">
                      <span>Remaining position</span>
                      <strong>{formatQuantity(selectedPositionLot.remaining_quantity)}</strong>
                      <em>{formatCurrency(selectedPositionLot.remaining_cost_basis, selectedPositionLot.currency)} cost basis</em>
                    </div>
                    <dl className="position-lot-inspector-metrics">
                      <div><dt>Cost method</dt><dd>{costBasisMethodLabel(selectedPositionLot.cost_basis_method)}</dd></div>
                      <div><dt>Entry price</dt><dd>{formatUnitPrice(selectedPositionLot.entry_price, selectedPositionLot.currency)}</dd></div>
                      <div><dt>Income</dt><dd>{formatCurrency(selectedPositionLot.income_cash_amount, selectedPositionLot.currency)}</dd></div>
                      <div><dt>Expense</dt><dd>{formatCurrency(selectedPositionLot.expense_cash_amount, selectedPositionLot.currency)}</dd></div>
                      <div><dt>Transferred qty</dt><dd>{formatQuantity(selectedPositionLot.transferred_quantity)}</dd></div>
                      <div><dt>Realized qty</dt><dd>{formatQuantity(selectedPositionLot.realized_quantity)}</dd></div>
                    </dl>
                    <Link
                      className="portfolio-security-secondary-link"
                      to={`${buildPortfolioSectionPath(portfolioId, '/transactions')}?transaction_id=${encodeURIComponent(selectedPositionLot.opened_by_transaction_id)}`}
                    >
                      Opening transaction
                    </Link>
                    <section className="position-lot-realizations">
                      <div className="position-lot-realizations-head">
                        <div>
                          <span>Matched exits</span>
                          <strong>{selectedPositionLot.realization_count}</strong>
                        </div>
                        <em>{formatSignedCurrency(selectedPositionLot.realized_pnl, selectedPositionLot.currency)}</em>
                      </div>
                      {selectedPositionLot.realizations.length ? (
                        <div className="position-lot-realizations-list">
                          {selectedPositionLot.realizations.map((realization) => (
                            <article key={realization.realization_id}>
                              <div>
                                <Link
                                  className="table-inline-link"
                                  to={`${buildPortfolioSectionPath(portfolioId, '/transactions')}?transaction_id=${encodeURIComponent(realization.transaction_id)}`}
                                >
                                  {realization.trade_date}
                                </Link>
                                <span>{formatLabel(realization.transaction_type)}</span>
                              </div>
                              <div>
                                <span>{formatQuantity(realization.quantity)}</span>
                                <strong className={signedValueClass(realization.realized_pnl)}>
                                  {formatSignedCurrency(realization.realized_pnl, selectedPositionLot.currency)}
                                </strong>
                              </div>
                            </article>
                          ))}
                        </div>
                      ) : (
                        <p>No matched exits for this lot.</p>
                      )}
                    </section>
                  </>
                ) : (
                  <div className="empty-state">Select a lot.</div>
                )}
              </aside>
            </div>
          </section>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
