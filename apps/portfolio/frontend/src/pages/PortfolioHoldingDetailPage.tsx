import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import '../holding-detail.css'

import InstrumentPriceChart from '../components/InstrumentPriceChart'
import DerivativeHoldingOverview from '../components/DerivativeHoldingOverview'
import CashHoldingOverview from '../components/CashHoldingOverview'
import SecurityLinkedOptionsPanel from '../components/SecurityLinkedOptionsPanel'
import FcnLifecyclePanel from '../components/FcnLifecyclePanel'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import HoldingPeriodPanel from '../components/HoldingPeriodPanel'
import HoldingEventsPanel from '../components/HoldingEventsPanel'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
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
  getPortfolioDerivativeContracts,
  getPortfolioInstrumentPriceChart,
  getPortfolioOptionDeliveryLinks,
  getPortfolioOptionObligations,
  getPortfolioPositionLots,
  getPortfolioTransactions,
  type PortfolioDerivativeContractRecord,
  type PortfolioDerivativeContractsResponse,
  type PortfolioInstrumentChartRangeKey,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioOptionDeliveryLinksResponse,
  type PortfolioOptionObligationsResponse,
  type PortfolioPositionHoldingRow,
  type PortfolioPositionHoldingProjectionResponse,
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

type HoldingDetailTab = 'overview' | 'pnl' | 'transactions' | 'lots' | 'events'
type PortfolioDetailKind = 'security' | 'fcn' | 'option' | 'cash' | 'settlement'

const CASH_HOLDING_KINDS = new Set(['settled_cash', 'restricted_cash'])
const SETTLEMENT_HOLDING_KINDS = new Set([
  'pending_subscription',
  'settlement_receivable',
  'settlement_payable',
  'position_recognition_adjustment',
])

function portfolioDetailKind(
  row: PortfolioPositionHoldingRow | null,
  contract: PortfolioDerivativeContractRecord | null,
  referenceId: string,
): PortfolioDetailKind {
  if (SETTLEMENT_HOLDING_KINDS.has(row?.holding_kind ?? '') || referenceId.startsWith('pending:')) {
    return 'settlement'
  }
  if (
    CASH_HOLDING_KINDS.has(row?.holding_kind ?? '') ||
    row?.instrument_core?.instrument_type === 'cash' || referenceId.startsWith('cash:')
  ) {
    return 'cash'
  }
  if (contract?.contract_type === 'fcn') {
    return 'fcn'
  }
  if (contract?.contract_type === 'option') {
    return 'option'
  }
  return 'security'
}

function isMonetaryDetail(kind: PortfolioDetailKind) {
  return kind === 'cash' || kind === 'settlement'
}

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

function parseDetailTab(value: string | null): HoldingDetailTab {
  if (value === 'transactions' || value === 'lots' || value === 'pnl' || value === 'events') {
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

function transactionPrimaryIdentifier(transaction: PortfolioTransactionRecord) {
  return (
    transaction.instrument_ref?.identifiers.find((item) => item.is_primary)?.identifier_value ??
    transaction.instrument_ref?.identifiers[0]?.identifier_value ??
    transaction.instrument_id ??
    ''
  )
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

export default function PortfolioHoldingDetailPage() {
  const { t } = useLanguage()
  const { portfolioId = '', holdingId = '' } = useParams()
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
  const [derivativeContractsResponse, setDerivativeContracts] = useState<PortfolioDerivativeContractsResponse | null>(null)
  const [deliveryLinksResponse, setDeliveryLinks] = useState<PortfolioOptionDeliveryLinksResponse | null>(null)
  const [optionObligationsResponse, setOptionObligations] = useState<PortfolioOptionObligationsResponse | null>(null)
  const [relatedDataLoading, setRelatedDataLoading] = useState(false)
  const [relatedDataResolved, setRelatedDataResolved] = useState(false)
  const [relatedDataError, setRelatedDataError] = useState<string | null>(null)
  const [lotFilter, setLotFilter] = useState('all')

  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const requestedHoldingLineId = searchParams.get('holding_line_id')
  const selectedPositionLotId = searchParams.get('position_lot_id')
  const detailTab = parseDetailTab(searchParams.get('detail_tab'))
  const chartRangeKey = parseChartRange(searchParams.get('chart_range'))
  const workspace =
    workspaceResponse?.portfolio_id === portfolioId &&
    (!requestedAsOfDate || workspaceResponse.as_of_date === requestedAsOfDate)
      ? workspaceResponse
      : null
  const positionLotsWorkspace =
    positionLotsResponse?.portfolio_id === portfolioId
      ? positionLotsResponse
      : null
  const transactionsWorkspace =
    transactionsResponse?.portfolio_id === portfolioId
      ? transactionsResponse
      : null
  const selectedRows = (workspace?.rows ?? []).filter(
    (row) => holdingReferenceId(row) === holdingId,
  )
  const selectedRow =
    selectedRows.find((row) => row.line_id === requestedHoldingLineId) ??
    selectedRows.find((row) => row.holding_kind === 'position') ??
    selectedRows[0] ??
    null
  const derivativeContracts = derivativeContractsResponse?.portfolio_id === portfolioId ? derivativeContractsResponse.derivative_contracts : []
  const selectedContract =
    selectedRow?.derivative_contract ??
    derivativeContracts.find((contract) => contract.derivative_contract_id === holdingId) ??
    null
  const detailKind = portfolioDetailKind(selectedRow, selectedContract, holdingId)
  const monetaryDetail = isMonetaryDetail(detailKind)
  const monetaryAccountId = selectedRow?.account_ids?.[0]
    ?? (holdingId.startsWith('cash:') ? holdingId.split(':').slice(2).join(':') : null)
  const activeDetailTab: HoldingDetailTab =
    monetaryDetail && !['overview', 'transactions'].includes(detailTab) ? 'overview' : detailTab
  const linkedOptionContracts = selectedContract
    ? []
    : derivativeContracts.filter(
        (contract): contract is Extract<PortfolioDerivativeContractRecord, { contract_type: 'option' }> =>
          contract.contract_type === 'option' &&
          contract.terms.underlying_instrument_id === holdingId,
      )
  const linkedOptionContractIdList = linkedOptionContracts.map(
    (contract) => contract.derivative_contract_id,
  )
  const linkedOptionContractIds = new Set(linkedOptionContractIdList)
  const relevantPositionReferences = new Set([
    holdingId,
    ...linkedOptionContractIds,
  ])
  const relevantPositionLots = (positionLotsWorkspace?.position_lots ?? []).filter(
    (positionLot) => relevantPositionReferences.has(positionLot.position_reference_id),
  )
  const ownPositionLots = relevantPositionLots.filter(
    (positionLot) => positionLot.position_reference_id === holdingId,
  )
  const linkedOptionPositionLots = relevantPositionLots.filter(
    (positionLot) => linkedOptionContractIds.has(positionLot.position_reference_id),
  )
  const selectedPositionLots = selectedRow && isOptionObligationHolding(selectedRow)
    ? []
    : ownPositionLots
  const deliveryLinks = deliveryLinksResponse?.links ?? []
  const optionTransactionIdsForContract = new Set(
    (transactionsWorkspace?.transactions ?? [])
      .filter((transaction) => transaction.derivative_contract_id === holdingId)
      .map((transaction) => transaction.transaction_id),
  )
  const pairedTransactionIds = new Set(
    deliveryLinks
      .filter(
        (link) =>
          link.underlying_instrument_id === holdingId ||
          optionTransactionIdsForContract.has(link.option_transaction_id),
      )
      .flatMap((link) => [link.option_transaction_id, link.stock_transaction_id]),
  )
  const selectedTransactions = (transactionsWorkspace?.transactions ?? []).filter(
    (transaction) =>
      (monetaryDetail
        ? detailKind === 'cash' || (selectedRow?.transaction_ids ?? []).includes(transaction.transaction_id)
        : transaction.instrument_id === holdingId ||
          transaction.derivative_contract_id === holdingId ||
          linkedOptionContractIds.has(transaction.derivative_contract_id ?? '') ||
          pairedTransactionIds.has(transaction.transaction_id)),
  )
  const selectedOptionObligations = (optionObligationsResponse?.obligations ?? []).filter(
    (obligation) =>
      obligation.derivative_contract_id === holdingId ||
      linkedOptionContractIds.has(obligation.derivative_contract_id),
  )
  const ownOptionObligations = selectedOptionObligations.filter((obligation) => obligation.derivative_contract_id === holdingId)
  const writtenLotsOnly = Boolean(selectedRow && isOptionObligationHolding(selectedRow)) || (!selectedRow && !ownPositionLots.length && ownOptionObligations.length > 0)
  const deliveryLinkByTransactionId = new Map(
    deliveryLinks.flatMap((link) => [
      [link.option_transaction_id, link] as const,
      [link.stock_transaction_id, link] as const,
    ]),
  )
  const selectedTransactionById = new Map(
    selectedTransactions.map((transaction) => [transaction.transaction_id, transaction]),
  )
  const displayedSelectedTransactions = selectedTransactions.filter((transaction) => {
    const deliveryLink = deliveryLinkByTransactionId.get(transaction.transaction_id)
    return !(
      deliveryLink &&
      deliveryLink.stock_transaction_id === transaction.transaction_id &&
      selectedTransactionById.has(deliveryLink.option_transaction_id)
    )
  }).sort((left, right) => right.trade_date.localeCompare(left.trade_date) || (right.trade_time ?? '').localeCompare(left.trade_time ?? '') || right.transaction_id.localeCompare(left.transaction_id, undefined, { numeric: true }))
  const visiblePositionLots = selectedPositionLots.filter((lot) => lotFilter === 'all' || lot.status === lotFilter)
  const selectedPositionLot =
    visiblePositionLots.find((positionLot) => positionLot.position_lot_id === selectedPositionLotId) ??
    visiblePositionLots[0] ??
    null
  const resolvedAsOfDate = workspace?.as_of_date || requestedAsOfDate
  const instrumentChartWorkspace =
    instrumentChartResponse?.portfolio_id === portfolioId &&
    instrumentChartResponse.instrument_core.instrument_id === holdingId &&
    instrumentChartResponse.range_key === chartRangeKey &&
    (!resolvedAsOfDate || instrumentChartResponse.as_of_date === resolvedAsOfDate)
      ? instrumentChartResponse
      : null
  const baseCurrency = workspace?.base_currency ?? (selectedRow ? holdingCurrency(selectedRow) : null) ?? instrumentChartWorkspace?.currency ?? 'USD'
  const localCurrency = (selectedRow ? holdingCurrency(selectedRow) : null) || selectedContract?.currency || ownPositionLots[0]?.currency || instrumentChartWorkspace?.currency || baseCurrency
  const securityCore = selectedRow?.instrument_core ?? ownPositionLots[0]?.instrument_ref ?? instrumentChartWorkspace?.instrument_core
  const selectedRowIdentifier = selectedRow ? primaryIdentifier(selectedRow) : securityCore?.identifiers.find((identifier) => identifier.is_primary)?.identifier_value ?? holdingId
  const selectedRowUsesEventValuation = selectedRow
    ? holdingUsesEventValuation(selectedRow)
    : Boolean(selectedContract)
  const selectedRowUnrealizedBase =
    !selectedRowUsesEventValuation &&
    selectedRow?.unrealized_pnl_base != null
      ? selectedRow.unrealized_pnl_base
      : null
  const selectedRowUnrealizedLocal =
    !selectedRowUsesEventValuation &&
    selectedRow?.unrealized_price_pnl != null
      ? selectedRow.unrealized_price_pnl
      : null
  const heroMarketValue = selectedRowUsesEventValuation
    ? selectedRow?.carrying_value_base ?? selectedRow?.carrying_value
    : selectedRow?.market_value_base ?? selectedRow?.market_value
  const heroMarketCurrency = selectedRowUsesEventValuation
    ? selectedRow?.carrying_value_base != null
      ? baseCurrency
      : localCurrency
    : selectedRow?.market_value_base != null
      ? baseCurrency
      : localCurrency
  const heroUnrealizedValue = selectedRowUnrealizedBase ?? selectedRowUnrealizedLocal
  const heroUnrealizedCurrency = selectedRowUnrealizedBase != null ? baseCurrency : localCurrency
  const valuationMetricName = valuationQuoteLabel(
    selectedRow?.quote_basis ?? selectedRow?.quote_metric_family,
  )
  const performanceMetricName = performanceSeriesLabel(
    instrumentChartWorkspace?.chart_basis ?? instrumentChartWorkspace?.metric_family,
  )
  const positionLotsPending =
    !monetaryDetail &&
    (
      workspaceLoading ||
      positionLotsLoading ||
      relatedDataLoading ||
      Boolean(resolvedAsOfDate && !positionLotsWorkspace && !positionLotsError)
    )
  const transactionsPending =
    workspaceLoading ||
    transactionsLoading ||
    relatedDataLoading ||
    Boolean(resolvedAsOfDate && (!monetaryDetail || monetaryAccountId) && !transactionsWorkspace && !transactionsError)
  const instrumentChartPending =
    activeDetailTab === 'overview' &&
    detailKind === 'security' &&
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
      .map((positionLot) => positionLot.acquisition_date ?? positionLot.opened_at)
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right))[0] ??
    null
  const heroCostBasis = selectedRow?.cost_basis_historical_base ?? selectedRow?.cost_basis_base ?? selectedRow?.cost_basis
  const heroCostCurrency =
    (selectedRow?.cost_basis_historical_base ?? selectedRow?.cost_basis_base) != null
      ? baseCurrency
      : localCurrency
  const detailTabs: Array<{
    key: HoldingDetailTab
    label: string
    meta: string
  }> = [
    {
      key: 'overview',
      label: 'Overview',
      meta:
        !monetaryDetail ? '' : detailKind === 'settlement'
                ? 'Settlement'
                : 'Cash & FX',
    },
    ...(!monetaryDetail ? [{ key: 'pnl' as const, label: 'Period P/L', meta: baseCurrency }] : []),
    {
      key: 'transactions',
      label: 'Transactions',
      meta: transactionsPending
        ? 'Loading'
        : transactionsWorkspace
          ? String(displayedSelectedTransactions.length)
          : '—',
    },
    ...(!monetaryDetail
      ? [{
          key: 'lots' as const,
          label: 'Position Lots',
          meta: positionLotsPending
            ? 'Loading'
            : positionLotsWorkspace
              ? String(selectedPositionLots.length + ownOptionObligations.length)
              : '—',
        }]
      : []),
    ...(!monetaryDetail ? [{ key: 'events' as const, label: 'Income and events', meta: '' }] : []),
  ]

  const backToHoldingsPath = useMemo(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('detail_tab')
    next.delete('chart_range')
    next.delete('position_lot_id')
    next.delete('holding_line_id')
    next.delete('pnl_period')
    next.delete('pnl_start')
    next.delete('pnl_end')
    if (holdingId) {
      next.set('position_reference_id', holdingId)
    }
    const query = next.toString()
    return `${buildPortfolioSectionPath(portfolioId, '/holdings')}${query ? `?${query}` : ''}`
  }, [holdingId, portfolioId, searchParams])

  const watchlistDetailUrl = buildWatchlistInstrumentDetailUrl(holdingId, {
    source: 'portfolio',
    portfolio_id: portfolioId,
    as_of_date: resolvedAsOfDate,
    return_to: typeof window === 'undefined' ? null : `${window.location.pathname}${window.location.search}`,
  })
  const filteredTransactionsPath = useMemo(() => {
    const next = new URLSearchParams()
    const accountId = monetaryDetail ? monetaryAccountId : null
    if (accountId) {
      next.set('account_id', accountId)
    } else {
      next.set('position_reference_id', holdingId)
    }
    if (resolvedAsOfDate) {
      next.set('end_date', resolvedAsOfDate)
    }
    return `${buildPortfolioSectionPath(portfolioId, '/transactions')}?${next.toString()}`
  }, [holdingId, monetaryDetail, monetaryAccountId, portfolioId, resolvedAsOfDate])

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
    if (!portfolioId || !holdingId) {
      setWorkspace(null)
      setWorkspaceLoading(false)
      setWorkspaceError('Portfolio and instrument ids are required.')
      return
    }

    let cancelled = false
    setWorkspace(null)
    setWorkspaceError(null)
    setWorkspaceLoading(true)

    getPortfolioPositionHoldingProjection(portfolioId, holdingId, {
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
  }, [holdingId, portfolioId, requestedAsOfDate])

  useEffect(() => {
    if (
      !portfolioId ||
      !holdingId ||
      !resolvedAsOfDate ||
      monetaryDetail ||
      !relatedDataResolved
    ) {
      setPositionLotsWorkspace(null)
      setPositionLotsError(null)
      setPositionLotsLoading(false)
      return
    }

    let cancelled = false
    setPositionLotsWorkspace(null)
    setPositionLotsError(null)
    setPositionLotsLoading(true)

    const positionReferenceIds = [...new Set([holdingId, ...linkedOptionContractIdList])]
    Promise.all(
      positionReferenceIds.map((positionReferenceId) =>
        getPortfolioPositionLots(portfolioId, {
          as_of_date: resolvedAsOfDate,
          position_reference_id: positionReferenceId,
        }),
      ),
    )
      .then((responses) => {
        if (!cancelled) {
          const positionLots = Array.from(
            new Map(
              responses
                .flatMap((response) => response.position_lots)
                .map((positionLot) => [positionLot.position_lot_id, positionLot]),
            ).values(),
          )
          setPositionLotsWorkspace({
            portfolio_id: portfolioId,
            summary: {
              position_lot_count: positionLots.length,
              open_position_lot_count: positionLots.filter((item) => item.status === 'open').length,
              closed_position_lot_count: positionLots.filter((item) => item.status === 'closed').length,
              realized_pnl: positionLots.reduce((total, item) => total + item.realized_pnl, 0),
            },
            position_lots: positionLots,
          })
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
  }, [
    relatedDataResolved,
    holdingId,
    linkedOptionContractIdList.join('|'),
    monetaryDetail,
    portfolioId,
    resolvedAsOfDate,
  ])

  useEffect(() => {
    if (!portfolioId || !resolvedAsOfDate || monetaryDetail) {
      setDerivativeContracts(null)
      setDeliveryLinks(null)
      setOptionObligations(null)
      setRelatedDataError(null)
      setRelatedDataLoading(false)
      setRelatedDataResolved(false)
      return
    }

    let cancelled = false
    setDerivativeContracts(null)
    setDeliveryLinks(null)
    setOptionObligations(null)
    setRelatedDataError(null)
    setRelatedDataLoading(true)
    setRelatedDataResolved(false)

    Promise.allSettled([
      getPortfolioDerivativeContracts(portfolioId),
      getPortfolioOptionDeliveryLinks(portfolioId),
      getPortfolioOptionObligations(portfolioId, { as_of_date: resolvedAsOfDate }),
    ])
      .then(([contracts, links, obligations]) => {
        if (!cancelled) {
          if (contracts.status === 'fulfilled') setDerivativeContracts(contracts.value)
          if (links.status === 'fulfilled') setDeliveryLinks(links.value)
          if (obligations.status === 'fulfilled') setOptionObligations(obligations.value)
          const failed = [contracts, links, obligations].filter((result) => result.status === 'rejected')
          if (failed.length) setRelatedDataError('Some linked contract data is unavailable. The instrument ledger is still available.')
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setRelatedDataError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load linked option activity.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRelatedDataLoading(false)
          setRelatedDataResolved(true)
        }
      })

    return () => {
      cancelled = true
    }
  }, [monetaryDetail, portfolioId, resolvedAsOfDate])

  useEffect(() => {
    if (
      !portfolioId ||
      !holdingId ||
      !resolvedAsOfDate ||
      (monetaryDetail && !monetaryAccountId) ||
      (!monetaryDetail && !relatedDataResolved)
    ) {
      setTransactionsWorkspace(null)
      setTransactionsError(null)
      setTransactionsLoading(false)
      return
    }

    let cancelled = false
    setTransactionsWorkspace(null)
    setTransactionsError(null)
    setTransactionsLoading(true)

    const selectedOptionUnderlyingId =
      selectedContract?.contract_type === 'option'
        ? selectedContract.terms.underlying_instrument_id
        : null
    const positionReferenceIds = monetaryDetail
      ? []
      : [...new Set([
          holdingId,
          ...linkedOptionContractIdList,
          ...(selectedOptionUnderlyingId ? [selectedOptionUnderlyingId] : []),
        ])]
    const requests = monetaryDetail
      ? [getPortfolioTransactions(portfolioId, {
          end_date: resolvedAsOfDate,
          account_id: monetaryAccountId ?? undefined,
        })]
      : positionReferenceIds.map((positionReferenceId) =>
          getPortfolioTransactions(portfolioId, {
            end_date: resolvedAsOfDate,
            position_reference_id: positionReferenceId,
          }),
        )

    Promise.all(requests)
      .then((responses) => {
        if (!cancelled) {
          const firstResponse = responses[0]
          const transactions = Array.from(
            new Map(
              responses
                .flatMap((response) => response.transactions)
                .map((transaction) => [transaction.transaction_id, transaction]),
            ).values(),
          )
          setTransactionsWorkspace({
            ...firstResponse,
            portfolio_id: portfolioId,
            transactions,
          })
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
  }, [
    relatedDataResolved,
    holdingId,
    linkedOptionContractIdList.join('|'),
    monetaryDetail,
    portfolioId,
    resolvedAsOfDate,
    selectedContract,
    monetaryAccountId,
  ])

  useEffect(() => {
    if (
      activeDetailTab !== 'overview' ||
      !portfolioId ||
      !holdingId ||
      !resolvedAsOfDate ||
      !relatedDataResolved ||
      detailKind !== 'security' ||
      selectedContract ||
      Boolean(selectedRow && !selectedRow.instrument_core) ||
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

    getPortfolioInstrumentPriceChart(portfolioId, holdingId, {
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
    relatedDataResolved,
    activeDetailTab,
    holdingId,
    portfolioId,
    resolvedAsOfDate,
    selectedContract,
    selectedRow,
    selectedRowUsesEventValuation,
  ])

  return (
    <PortfolioWorkspaceLayout
      activeSection="Holdings"
      busy={workspaceLoading || positionLotsPending || transactionsPending || instrumentChartPending || relatedDataLoading}
    >
      <section
        className="portfolio-detail-surface portfolio-security-detail"
        data-detail-kind={detailKind}
        aria-busy={workspaceLoading || positionLotsPending || transactionsPending || instrumentChartPending || relatedDataLoading}
      >
        <header className="portfolio-security-header">
          <div className="portfolio-security-detail-nav">
            <Link className="portfolio-security-back-link" to={backToHoldingsPath}>
              <span aria-hidden="true">←</span>
              Holdings
            </Link>
            <label className="holding-detail-asof">{t('As of date')}<input type="date" aria-label={t('Holding date')} value={resolvedAsOfDate} onChange={(event) => { if (event.target.value) updateSearchParam('as_of_date', event.target.value) }} /></label>
            <QualityWarningsNotice warnings={workspace?.quality_warnings} />
            {detailKind === 'security' && selectedRow?.instrument_core ? (
              <a data-workspace-link className="portfolio-security-secondary-link" href={watchlistDetailUrl}>
                View instrument research
              </a>
            ) : null}
          </div>

          <div className="portfolio-security-hero">
            <div className="portfolio-security-title-stack">
              <span className="portfolio-security-eyebrow">
                {detailKind === 'security'
                  ? 'Security position'
                  : detailKind === 'fcn'
                    ? 'FCN contract'
                    : detailKind === 'option'
                      ? 'Option contract'
                      : detailKind === 'settlement'
                        ? 'Settlement exposure'
                        : 'Cash balance'}
              </span>
              <h1 translate="no">{selectedRow ? holdingName(selectedRow) : selectedContract?.contract_name ?? securityCore?.instrument_name ?? holdingId}</h1>
              <div className="portfolio-security-meta-row">
                <span className="ticker-pill">{selectedRowIdentifier}</span>
                <span>
                  {detailKind === 'settlement'
                    ? formatLabel(selectedRow?.holding_kind ?? 'settlement')
                    : detailKind === 'cash'
                      ? formatLabel(selectedRow?.holding_kind ?? 'cash')
                      : selectedRow
                        ? formatLabel(holdingAssetType(selectedRow))
                        : selectedContract
                          ? formatLabel(selectedContract.contract_type)
                          : formatLabel(securityCore?.instrument_type ?? 'instrument')}
                </span>
                <span>{selectedRow ? holdingCurrency(selectedRow) : selectedContract?.currency ?? instrumentChartWorkspace?.currency ?? '—'}</span>
                <span>As of {resolvedAsOfDate || '—'}</span>
              </div>
            </div>
            <div className="portfolio-security-hero-metrics">
              {detailKind === 'settlement' ? (
                <>
                  <div>
                    <span>Amount due</span>
                    <strong>{formatCurrency(selectedRow?.settlement_amount ?? selectedRow?.market_value, selectedRow ? holdingCurrency(selectedRow) : baseCurrency)}</strong>
                  </div>
                  <div>
                    <span>Reporting value</span>
                    <strong>{formatCurrency(selectedRow?.settlement_amount_base ?? selectedRow?.market_value_base, baseCurrency)}</strong>
                  </div>
                  <div>
                    <span>Settlement</span>
                    <strong>{selectedRow?.settlement_date ?? '—'}</strong>
                  </div>
                  <div>
                    <span>Status</span>
                    <strong>{formatLabel(selectedRow?.pending_status ?? 'pending')}</strong>
                  </div>
                </>
              ) : detailKind === 'cash' ? (
                <>
                  <div>
                    <span>Local balance</span>
                    <strong>{formatCurrency(selectedRow?.market_value, selectedRow ? holdingCurrency(selectedRow) : baseCurrency)}</strong>
                  </div>
                  <div>
                    <span>Reporting value</span>
                    <strong>{formatCurrency(selectedRow?.market_value_base, baseCurrency)}</strong>
                  </div>
                  <div>
                    <span>Unrealized FX P/L</span>
                    <strong className={signedValueClass(selectedRow?.unrealized_fx_pnl_base)}>
                      {formatSignedCurrency(selectedRow?.unrealized_fx_pnl_base, baseCurrency)}
                    </strong>
                  </div>
                  <div>
                    <span>Available</span>
                    <strong>{selectedRow?.available_for_trading ? 'For trading' : 'Unavailable'}</strong>
                  </div>
                </>
              ) : selectedContract?.contract_type === 'option' ? (
                <>
                  <div>
                    <span>Side / type</span>
                    <strong>{selectedRow ? `${isOptionObligationHolding(selectedRow) || selectedRow.quantity < 0 ? 'Written' : 'Long'} ` : ''}{formatLabel(selectedContract.terms.option_type)}</strong>
                  </div>
                  <div>
                    <span>Open contracts</span>
                    <strong>{formatQuantity(selectedRow
                      ? selectedRow.open_contract_quantity ?? Math.abs(selectedRow.quantity)
                      : workspace && !workspaceLoading ? 0 : null)}</strong>
                  </div>
                  <div>
                    <span>Underlying equivalent</span>
                    <strong>{formatQuantity(selectedRow ? selectedRow.required_underlying_quantity ?? Math.abs(selectedRow.quantity) * Number(selectedContract.terms.contract_multiplier) : null)}</strong>
                  </div>
                  <div>
                    <span>Expiry</span>
                    <strong>{selectedContract.terms.expiry_date}</strong>
                  </div>
                </>
              ) : selectedContract?.contract_type === 'fcn' ? (
                <>
                  <div>
                    <span>Notional</span>
                    <strong>{formatCurrency(selectedContract.terms.notional, selectedContract.currency)}</strong>
                  </div>
                  <div>
                    <span>Annual coupon</span>
                    <strong>{selectedContract.terms.annual_coupon_rate_pct == null ? '—' : `${formatNumber(selectedContract.terms.annual_coupon_rate_pct, 2)}%`}</strong>
                  </div>
                  <div>
                    <span>Maturity</span>
                    <strong>{selectedContract.terms.maturity_date}</strong>
                  </div>
                  <div>
                    <span>Carrying value</span>
                    <strong>{formatCurrency(heroMarketValue, heroMarketCurrency)}</strong>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <span>Portfolio weight</span>
                    <strong>{formatPercent(selectedRow?.allocation)}</strong>
                  </div>
                  <div>
                    <span>Quantity</span>
                    <strong>{formatQuantity(selectedRow?.quantity)}</strong>
                  </div>
                  <div>
                    <span>Market value</span>
                    <strong>{formatCurrency(heroMarketValue, heroMarketCurrency)}</strong>
                  </div>
                  <div>
                    <span>Unrealized P/L</span>
                    <strong className={signedValueClass(heroUnrealizedValue)}>
                      {formatSignedCurrency(heroUnrealizedValue, heroUnrealizedCurrency)}
                    </strong>
                  </div>
                </>
              )}
            </div>
          </div>

          {selectedRows.length > 1 && selectedContract?.contract_type === 'option' ? (
            <div className="holding-detail-segments" aria-label={t('Position side')}>
              {selectedRows.map((row) => <button key={row.line_id} type="button" aria-pressed={selectedRow?.line_id === row.line_id} onClick={() => updateSearchParam('holding_line_id', row.line_id)}>{t(isOptionObligationHolding(row) ? 'Written obligation' : 'Long option asset')}</button>)}
            </div>
          ) : null}
        </header>

        {workspaceLoading ? <CalculationStatus /> : null}
        {workspaceError ? <div className="error-state">{workspaceError}</div> : null}
        {relatedDataError ? <div className="error-state">{t(relatedDataError)}</div> : null}
        {!workspaceLoading && !workspaceError && workspace && !selectedRows.length ? (
          <div className="empty-state" role="status">Not held as of selected date.</div>
        ) : null}

        <div className="holdings-detail-tabbar portfolio-security-tabs" role="tablist" aria-label="Instrument detail">
          {detailTabs.map((tab) => {
            const isActive = activeDetailTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                id={`portfolio-security-tab-${tab.key}`}
                role="tab"
                aria-label={`${t(tab.label)} ${t(tab.meta)}`.trim()}
                aria-selected={isActive}
                aria-controls={`portfolio-security-panel-${tab.key}`}
                tabIndex={isActive ? 0 : -1}
                className={`holdings-detail-tab ${isActive ? 'holdings-detail-tab-active' : ''}`}
                onClick={() => updateSearchParam('detail_tab', tab.key)}
                onKeyDown={(event) => {
                  const index = detailTabs.findIndex((item) => item.key === activeDetailTab)
                  const nextIndex = event.key === 'ArrowRight' ? (index + 1) % detailTabs.length : event.key === 'ArrowLeft' ? (index + detailTabs.length - 1) % detailTabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? detailTabs.length - 1 : null
                  if (nextIndex != null) {
                    event.preventDefault()
                    updateSearchParam('detail_tab', detailTabs[nextIndex].key)
                    document.getElementById(`portfolio-security-tab-${detailTabs[nextIndex].key}`)?.focus()
                  }
                }}
              >
                <span className="holdings-detail-tab-label">{t(tab.label)}</span>
                <span className="holdings-detail-tab-meta">{t(tab.meta)}</span>
              </button>
            )
          })}
        </div>

        {activeDetailTab === 'pnl' && resolvedAsOfDate && !monetaryDetail ? (
          <div id="portfolio-security-panel-pnl" className="portfolio-security-tab-panel" role="tabpanel" aria-labelledby="portfolio-security-tab-pnl">
            <HoldingPeriodPanel key={`${holdingId}:${resolvedAsOfDate}`} portfolioId={portfolioId} holdingId={holdingId} asOfDate={resolvedAsOfDate} eventValued={selectedRowUsesEventValuation} />
          </div>
        ) : null}
        {activeDetailTab === 'events' && resolvedAsOfDate && !monetaryDetail ? (
          <div id="portfolio-security-panel-events" className="portfolio-security-tab-panel" role="tabpanel" aria-labelledby="portfolio-security-tab-events">
            <HoldingEventsPanel key={`${holdingId}:${resolvedAsOfDate}`} portfolioId={portfolioId} holdingId={holdingId} asOfDate={resolvedAsOfDate} currency={localCurrency} security={detailKind === 'security'} transactions={selectedTransactions} lots={ownPositionLots} loading={transactionsPending || positionLotsPending} error={transactionsError ?? positionLotsError} />
          </div>
        ) : null}

        {activeDetailTab === 'overview' && resolvedAsOfDate && !monetaryDetail && relatedDataResolved && (detailKind === 'fcn' || detailKind === 'security') ? (
          <FcnLifecyclePanel key={`${holdingId}:${resolvedAsOfDate}`} portfolioId={portfolioId} positionReferenceId={holdingId} asOfDate={resolvedAsOfDate} />
        ) : null}
        {activeDetailTab === 'overview' && workspace && !workspaceLoading && (monetaryDetail || relatedDataResolved) ? (
          <div
            id="portfolio-security-panel-overview"
            className="portfolio-security-tab-panel portfolio-security-overview"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-overview"
          >
            {monetaryDetail && selectedRow ? (
              <CashHoldingOverview
                portfolioId={portfolioId}
                asOfDate={resolvedAsOfDate}
                baseCurrency={baseCurrency}
                holding={selectedRow}
                accountNames={accountNameById}
              />
            ) : monetaryDetail ? (
              <p className="empty-state">No monetary balance at this date. Use Transactions to review account activity.</p>
            ) : selectedContract ? (
              <DerivativeHoldingOverview
                key={`${holdingId}:${resolvedAsOfDate}`}
                portfolioId={portfolioId}
                asOfDate={resolvedAsOfDate}
                baseCurrency={baseCurrency}
                holding={selectedRow}
                contract={selectedContract}
                transactions={selectedTransactions}
                transactionsLoading={transactionsPending}
                transactionsError={transactionsError}
                rangeKey={chartRangeKey}
                onRangeChange={(rangeKey) => updateSearchParam('chart_range', rangeKey)}
              />
            ) : (
              <>
            <div className="portfolio-security-overview-workbench">
              <section className="portfolio-security-chart-panel">
                <div className="portfolio-security-section-head"><div><span className="portfolio-security-section-kicker">{t('Market performance')}</span><h2>{t(['public_fund', 'private_fund'].includes(securityCore?.instrument_type ?? '') ? 'Fund NAV history' : 'Price history')}</h2></div><span>{localCurrency}</span></div>
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
                <dl className="portfolio-security-position-facts holding-position-facts">
                  <div><dt>{t('Book average cost')}</dt><dd>{formatUnitPrice(selectedRow?.cost_basis != null && selectedRow.quantity > 0 ? selectedRow.cost_basis / selectedRow.quantity : null, localCurrency)}</dd></div>
                  <div><dt>{t('Break-even price')}</dt><dd>{formatUnitPrice(selectedRow?.break_even_price, localCurrency)}</dd></div>
                  <div><dt>{t('Price P/L')} · {localCurrency}</dt><dd className={signedValueClass(selectedRowUnrealizedLocal)}>{formatSignedCurrency(selectedRowUnrealizedLocal, localCurrency)}</dd></div>
                  <div><dt>{t('FX P/L')} · {baseCurrency}</dt><dd className={signedValueClass(selectedRow?.unrealized_fx_pnl_base)}>{formatSignedCurrency(selectedRow?.unrealized_fx_pnl_base, baseCurrency)}</dd></div>
                  <div><dt>{t('Held since')}</dt><dd>{holdingSinceDate ?? '—'}</dd></div>
                  <div><dt>{t('Accounts / open lots')}</dt><dd>{selectedRow ? `${selectedRow.account_count ?? '—'} / ${selectedRow.open_position_lot_count ?? '—'}` : '—'}</dd></div>
                </dl>
                <button className="holding-detail-primary-action" type="button" onClick={() => updateSearchParam('detail_tab', 'pnl')}>{t('Review period P/L')} <span aria-hidden="true">↗</span></button>
                <details className="holding-detail-disclosure">
                  <summary>{t('Valuation and cost basis')}</summary>
                  <dl className="portfolio-security-position-facts">
                    <div><dt>{t('Open cost basis')} · {baseCurrency}</dt><dd>{formatCurrency(heroCostBasis, heroCostCurrency)}</dd></div>
                    <div><dt>{t('Cost method')}</dt><dd>{costBasisMethodLabel(selectedRow?.cost_basis_method)}</dd></div>
                    <div><dt>{t('Valuation')}</dt><dd>{formatUnitPrice(selectedRow?.last_price, localCurrency)}<small>{t(valuationMetricName)} · {selectedRow?.quote_as_of_date ?? '—'}</small></dd></div>
                    <div><dt>{t('Quote provider')}</dt><dd title={selectedRow?.quote_provider ?? undefined}>{quoteProviderLabel(selectedRow?.quote_provider)}</dd></div>
                    <div><dt>{t('Performance basis')}</dt><dd>{t(performanceMetricName)}</dd></div>
                    <div><dt>{t('Chart coverage')}</dt><dd>{instrumentChartWorkspace ? countLabel(instrumentChartWorkspace.summary.point_count, 'observation') : '—'}</dd></div>
                  </dl>
                  <p className="holding-detail-note">{t('Reporting cost uses historical FX. Price P/L is in instrument currency; FX P/L is in portfolio currency.')}</p>
                </details>
              </aside>
            </div>

            {linkedOptionContracts.length > 0 && !positionLotsPending && !positionLotsError && !relatedDataError ? <SecurityLinkedOptionsPanel
              portfolioId={portfolioId}
              asOfDate={resolvedAsOfDate}
              optionLots={linkedOptionPositionLots}
              optionObligations={selectedOptionObligations}
              optionContracts={linkedOptionContracts}
              transactions={selectedTransactions}
            /> : null}

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
                          {formatCurrency(slice.marketValue, localCurrency)}
                        </strong>
                      </div>
                      <dl>
                        <div><dt>Quantity</dt><dd>{formatQuantity(slice.quantity)}</dd></div>
                        <div><dt>Remaining cost</dt><dd>{formatCurrency(slice.remainingCost, localCurrency)}</dd></div>
                        <div>
                          <dt>Unrealized P/L</dt>
                          <dd className={selectedRowUsesEventValuation ? undefined : signedValueClass(sliceUnrealized)}>
                            {selectedRowUsesEventValuation
                              ? 'N/A'
                              : formatSignedCurrency(
                                  sliceUnrealized,
                                  localCurrency,
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
              </>
            )}
          </div>
        ) : null}

        {activeDetailTab === 'transactions' ? (
          <section
            id="portfolio-security-panel-transactions"
            className="portfolio-security-tab-panel"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-transactions"
          >
            <div className="portfolio-security-panel-head">
              <div>
                <span className="portfolio-security-section-kicker">
                  {monetaryDetail ? 'Cash and settlement ledger' : 'Instrument activity'}
                </span>
                <h2>{monetaryDetail ? 'Linked cash activity' : 'Transactions'}</h2>
                <p>
                  {monetaryDetail
                    ? `Related account transactions through ${resolvedAsOfDate || '—'}. Settlement dates determine when cash moves.`
                    : `Confirmed economic facts through ${resolvedAsOfDate || '—'}.`}
                </p>
              </div>
              <div className="portfolio-security-panel-actions">
                <span>
                  {transactionsPending
                    ? 'Loading'
                    : transactionsWorkspace
                      ? countLabel(displayedSelectedTransactions.length, 'activity', 'activities')
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
                    <th>{monetaryDetail ? 'Transaction amount' : 'Quantity / Price'}</th>
                    <th>{monetaryDetail ? 'Account cash movement' : 'Gross / Net Cash'}</th>
                    <th>Recognition</th>
                  </tr>
                </thead>
                <tbody>
                  {transactionsPending ? (
                    <TableStatusRow colSpan={6} label="Loading" />
                  ) : transactionsError ? (
                    <TableStatusRow colSpan={6} label={transactionsError} tone="error" />
                  ) : displayedSelectedTransactions.length ? (
                    displayedSelectedTransactions.map((transaction) => {
                      const activityLabel = transactionActivityLabel(
                        transaction.transaction_type,
                        transaction.instrument_ref?.instrument_type,
                        transaction.option_action,
                        transaction.lifecycle_event_type,
                      )
                      const deliveryLink = deliveryLinkByTransactionId.get(
                        transaction.transaction_id,
                      )
                      const stockDeliveryTransaction =
                        deliveryLink?.option_transaction_id === transaction.transaction_id
                          ? selectedTransactionById.get(deliveryLink.stock_transaction_id) ?? null
                          : null
                      const cashTransaction = stockDeliveryTransaction ?? transaction
                      const monetaryCurrency = selectedRow ? holdingCurrency(selectedRow) : holdingId.split(':')[1]
                      const monetaryCashEffect = cashTransaction.transaction_type === 'fx_conversion'
                        ? cashTransaction.counterparty_account_id === monetaryAccountId
                          ? cashTransaction.counter_amount
                          : -(cashTransaction.gross_amount + cashTransaction.fees + cashTransaction.taxes)
                        : cashTransaction.net_cash_effect
                      const settlementAccountLabel = stockDeliveryTransaction
                        ? [
                            `Delivery via ${stockDeliveryTransaction.account.account_name}`,
                            stockDeliveryTransaction.settlement_cash_account?.account_name,
                          ]
                            .filter(Boolean)
                            .join(' · ')
                        : transaction.transaction_type === 'fx_conversion'
                          ? `Convert to ${accountNameById.get(transaction.counterparty_account_id ?? '') ?? transaction.counterparty_account_id}`
                        : transaction.settlement_cash_account
                          ? `Settle via ${transaction.settlement_cash_account.account_name}`
                          : 'No settlement account'
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
                              <span className="holding-secondary" translate="no">
                                {linkedOptionContractIds.has(transaction.derivative_contract_id ?? '')
                                  ? `Linked option · ${transaction.derivative_contract?.contract_name ?? transaction.derivative_contract_id}`
                                  : transaction.transaction_id}
                              </span>
                              {stockDeliveryTransaction ? (
                                <span className="holding-secondary">
                                  Stock delivery · {formatLabel(stockDeliveryTransaction.transaction_type)}{' '}
                                  {formatQuantity(stockDeliveryTransaction.quantity)}{' '}
                                  {transactionPrimaryIdentifier(stockDeliveryTransaction)}
                                </span>
                              ) : null}
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span>{transactionAccountLabel(transaction)}</span>
                              <span className="holding-secondary">{settlementAccountLabel}</span>
                            </div>
                          </td>
                          <td>
                            {monetaryDetail ? (
                              <span>{formatCurrency(cashTransaction.gross_amount, cashTransaction.currency)}</span>
                            ) : <div className="holding-name-stack">
                              <span>{formatQuantity(transaction.quantity)}</span>
                              <span className="holding-secondary">{formatUnitPrice(transaction.price, transaction.currency)}</span>
                              {stockDeliveryTransaction ? (
                                <span className="holding-secondary">
                                  Delivery {formatQuantity(stockDeliveryTransaction.quantity)} @{' '}
                                  {formatUnitPrice(
                                    stockDeliveryTransaction.price,
                                    stockDeliveryTransaction.currency,
                                  )}
                                </span>
                              ) : null}
                            </div>}
                          </td>
                          <td>
                            {monetaryDetail ? (
                              <span className={signedValueClass(monetaryCashEffect)}>{formatSignedCurrency(monetaryCashEffect, monetaryCurrency)}</span>
                            ) : <div className="holding-name-stack">
                              <span>{formatCurrency(cashTransaction.gross_amount, cashTransaction.currency)}</span>
                              <span className={signedValueClass(cashTransaction.net_cash_effect)}>
                                Net {formatSignedCurrency(cashTransaction.net_cash_effect, cashTransaction.currency)}
                              </span>
                            </div>}
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span>
                                {cashTransaction.position_effective_date
                                  ? `Position EOD ${cashTransaction.position_effective_date}`
                                  : `Economic ${cashTransaction.economic_date}`}
                              </span>
                              <span className="holding-secondary">Settle {cashTransaction.settlement_date}</span>
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

        {activeDetailTab === 'lots' && !monetaryDetail ? (
          <section
            id="portfolio-security-panel-lots"
            className="portfolio-security-tab-panel"
            role="tabpanel"
            aria-labelledby="portfolio-security-tab-lots"
          >
            {ownOptionObligations.length > 0 ? <section className="holding-written-history">
              <div className="portfolio-security-panel-head"><div><span className="portfolio-security-section-kicker">{t('Premium ledger')}</span><h2>{t('Written position history')}</h2><p>{t('Written option batches, remaining obligations and matched closing results.')}</p></div></div>
              <div className="table-shell"><table className="holdings-table portfolio-security-transactions-table">
                <thead><tr><th>{t('Opened / Account')}</th><th>{t('Status')}</th><th>{t('Open contracts')}</th><th>{t('Remaining premium basis')}</th><th>{t('Carrying liability')}</th><th>{t('Realized P/L')}</th></tr></thead>
                <tbody>{ownOptionObligations.map((obligation) => <tr key={obligation.obligation_id}>
                  <td>{obligation.opened_at ?? '—'}<small translate="no">{accountNameById.get(obligation.account_id) ?? obligation.account_id}</small></td>
                  <td>{t(obligation.status === 'open' ? 'Outstanding' : formatLabel(obligation.status))}</td><td>{formatQuantity(obligation.open_contract_quantity)}</td>
                  <td>{formatCurrency(obligation.premium_basis_remaining, obligation.derivative_contract.currency)}</td><td>{formatCurrency(obligation.carrying_liability, obligation.derivative_contract.currency)}</td>
                  <td className={signedValueClass(obligation.realized_pnl)}>{formatSignedCurrency(obligation.realized_pnl, obligation.derivative_contract.currency)}</td>
                </tr>)}</tbody>
              </table></div>
              <p className="holding-detail-note">{t('Written realized P/L includes closing charges. Opening fees are expensed separately; see Period P/L for the net result.')}</p>
            </section> : null}
            {!writtenLotsOnly ? <>
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
                    ? `${selectedPositionLots.filter((positionLot) => positionLot.status === 'open').length} open · ${selectedPositionLots.filter((positionLot) => positionLot.status === 'closed').length} closed`
                    : '—'}
              </span>
            </div>

            <div className="portfolio-security-lot-summary">
              <div>
                <span>{selectedRowUsesEventValuation ? t('Carrying value') : t('Market value')}</span>
                <strong>
                  {formatCurrency(lotMarketValue, localCurrency)}
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
                <strong>{formatCurrency(remainingLotCost, localCurrency)}</strong>
                <em>Current cost basis</em>
              </div>
              <div>
                <span>Realized P/L</span>
                <strong className={signedValueClass(realizedLotPnl)}>
                  {formatSignedCurrency(realizedLotPnl, localCurrency)}
                </strong>
                <em>Through {resolvedAsOfDate || '—'}</em>
              </div>
            </div>

            <div className="holding-detail-segments holding-lot-filters" aria-label={t('Lot status')}>
              {['all', 'open', 'closed'].map((status) => <button type="button" key={status} aria-pressed={lotFilter === status} onClick={() => setLotFilter(status)}>{t(status === 'all' ? 'All lots' : status === 'open' ? 'Open lots' : 'Closed lots')}</button>)}
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
                    ) : visiblePositionLots.length ? (
                      visiblePositionLots.map((positionLot) => (
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
                              <strong title={positionLot.position_lot_id}>{t('Lot')} {selectedPositionLots.indexOf(positionLot) + 1}</strong>
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
                                  {t(positionLot.status === 'open' ? 'Open lot' : 'Closed lot')}
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
                      <span className={`coverage-pill ${lotStatusClass(selectedPositionLot)}`}>{t(selectedPositionLot.status === 'open' ? 'Open lot' : 'Closed lot')}</span>
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
            </> : null}
          </section>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
