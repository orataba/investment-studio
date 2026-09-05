import type { ReactNode } from 'react'
import { Link } from 'react-router'

import ConfigurableHoldingsSection, {
  type HoldingsSectionSystemView,
} from './ConfigurableHoldingsSection'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatUnitPrice,
} from '../lib/format'
import type { HoldingsWorkspaceResponse, PortfolioHoldingRow } from '../lib/api'
import { baseAmountForRow } from '../lib/holdingAmounts'
import { isOptionObligationHolding } from '../lib/holdingPresentation'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'
import { useHorizontalTablePan } from '../lib/useHorizontalTablePan'

type FixedColumn = {
  key: string
  label: string
  align?: 'right' | 'center'
  opensDetail?: boolean
  render: (row: PortfolioHoldingRow) => ReactNode
}

type HoldingsSectionTablesProps = {
  workspace: HoldingsWorkspaceResponse
  derivativeRows: PortfolioHoldingRow[]
  cashRows: PortfolioHoldingRow[]
  onSelectHolding: (row: PortfolioHoldingRow) => void
  onVisibleColumnsChange?: (sectionKey: string, columns: string[]) => void
}

export type HoldingsSectionKey = 'fcn' | 'options' | 'cash'

export type HoldingsSectionVisibleColumns = Record<HoldingsSectionKey, string[]>

export const HOLDINGS_SECTION_COLUMN_KEYS: HoldingsSectionVisibleColumns = {
  fcn: [
    'contract',
    'external_reference',
    'account',
    'currency',
    'notional',
    'underlying_terms',
    'risk',
    'coupon',
    'issue_date',
    'final_observation_date',
    'maturity_date',
    'days_to_maturity',
    'issuer',
    'counterparty',
    'remaining_basis',
    'signed_nav_amount_base',
    'carrying_historical_base',
    'carrying_fx_translation_base',
    'weight',
    'valuation_basis',
    'fair_value_status',
    'coverage',
  ],
  options: [
    'contract',
    'external_reference',
    'account',
    'side',
    'option_type',
    'underlying',
    'underlying_spot',
    'currency',
    'expiry_date',
    'days_to_expiry',
    'strike',
    'moneyness',
    'open_contracts',
    'multiplier',
    'underlying_equivalent',
    'basis_type',
    'remaining_basis',
    'signed_nav_amount_base',
    'carrying_historical_base',
    'carrying_fx_translation_base',
    'strike_notional_base',
    'backing',
    'risk',
    'weight',
    'status',
    'valuation_basis',
    'fair_value_status',
    'coverage',
  ],
  cash: [
    'description',
    'type',
    'currency',
    'account',
    'availability',
    'local_amount',
    'base_value',
    'fx_cost_basis_base',
    'monetary_fx_pnl_base',
    'weight',
    'settlement_date',
    'pending_until_date',
    'related_instrument',
    'status',
    'coverage',
  ],
}

export const DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS: HoldingsSectionVisibleColumns = {
  fcn: [
    'contract',
    'account',
    'notional',
    'underlying_terms',
    'risk',
    'coupon',
    'maturity_date',
    'days_to_maturity',
    'signed_nav_amount_base',
    'valuation_basis',
  ],
  options: [
    'contract',
    'account',
    'side',
    'option_type',
    'underlying',
    'underlying_spot',
    'strike',
    'moneyness',
    'expiry_date',
    'days_to_expiry',
    'open_contracts',
    'backing',
    'risk',
    'signed_nav_amount_base',
    'valuation_basis',
  ],
  cash: [
    'description',
    'type',
    'currency',
    'account',
    'availability',
    'local_amount',
    'base_value',
    'fx_cost_basis_base',
    'monetary_fx_pnl_base',
    'weight',
    'settlement_date',
    'pending_until_date',
    'related_instrument',
    'status',
    'coverage',
  ],
}

const FCN_SYSTEM_VIEWS: HoldingsSectionSystemView[] = [
  { id: 'position', name: 'Default', columns: DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.fcn },
  {
    id: 'terms',
    name: 'Terms & Events',
    columns: [
      'contract',
      'external_reference',
      'currency',
      'notional',
      'underlying_terms',
      'risk',
      'coupon',
      'issue_date',
      'final_observation_date',
      'maturity_date',
      'days_to_maturity',
      'issuer',
      'counterparty',
    ],
  },
  {
    id: 'valuation',
    name: 'Valuation',
    columns: [
      'contract',
      'account',
      'currency',
      'notional',
      'maturity_date',
      'days_to_maturity',
      'remaining_basis',
      'signed_nav_amount_base',
      'carrying_historical_base',
      'carrying_fx_translation_base',
      'weight',
      'valuation_basis',
      'fair_value_status',
      'coverage',
    ],
  },
]

const OPTION_SYSTEM_VIEWS: HoldingsSectionSystemView[] = [
  { id: 'position', name: 'Default', columns: DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.options },
  {
    id: 'contract',
    name: 'Contract Terms',
    columns: [
      'contract',
      'external_reference',
      'account',
      'side',
      'option_type',
      'underlying',
      'underlying_spot',
      'currency',
      'expiry_date',
      'days_to_expiry',
      'strike',
      'moneyness',
      'open_contracts',
      'multiplier',
      'underlying_equivalent',
      'strike_notional_base',
      'backing',
      'risk',
      'status',
      'coverage',
    ],
  },
  {
    id: 'valuation',
    name: 'Valuation',
    columns: [
      'contract',
      'account',
      'side',
      'underlying',
      'underlying_spot',
      'currency',
      'expiry_date',
      'open_contracts',
      'basis_type',
      'remaining_basis',
      'signed_nav_amount_base',
      'carrying_historical_base',
      'carrying_fx_translation_base',
      'strike_notional_base',
      'backing',
      'risk',
      'weight',
      'status',
      'valuation_basis',
      'fair_value_status',
      'coverage',
    ],
  },
]

const HOLDINGS_SECTION_COLUMN_WIDTHS: Record<string, number> = {
  contract: 240,
  external_reference: 170,
  description: 220,
  type: 130,
  account: 190,
  side: 90,
  option_type: 90,
  underlying: 170,
  underlying_spot: 150,
  currency: 90,
  notional: 150,
  underlying_terms: 400,
  risk: 220,
  coupon: 120,
  issue_date: 120,
  final_observation_date: 150,
  maturity_date: 130,
  expiry_date: 130,
  days_to_maturity: 70,
  days_to_expiry: 70,
  issuer: 150,
  counterparty: 160,
  strike: 140,
  moneyness: 120,
  open_contracts: 120,
  multiplier: 100,
  underlying_equivalent: 160,
  basis_type: 150,
  remaining_basis: 160,
  signed_nav_amount_base: 190,
  carrying_historical_base: 190,
  carrying_fx_translation_base: 190,
  strike_notional_base: 180,
  backing: 220,
  weight: 110,
  status: 140,
  valuation_basis: 140,
  fair_value_status: 140,
  coverage: 140,
  availability: 100,
  local_amount: 150,
  base_value: 170,
  fx_cost_basis_base: 180,
  monetary_fx_pnl_base: 190,
  settlement_date: 130,
  pending_until_date: 130,
  related_instrument: 180,
}

function holdingsSectionWidth(columnKeys: string[]) {
  return columnKeys.reduce(
    (total, columnKey) => total + (HOLDINGS_SECTION_COLUMN_WIDTHS[columnKey] ?? 140),
    0,
  )
}

const FCN_TABLE_MIN_WIDTH = Math.max(
  ...FCN_SYSTEM_VIEWS.map((view) => holdingsSectionWidth(view.columns)),
)
const OPTION_TABLE_MIN_WIDTH = Math.max(
  ...OPTION_SYSTEM_VIEWS.map((view) => holdingsSectionWidth(view.columns)),
)
const CASH_TABLE_MIN_WIDTH = holdingsSectionWidth(
  DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.cash,
)

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function sumComplete(
  rows: PortfolioHoldingRow[],
  accessor: (row: PortfolioHoldingRow) => number | null | undefined,
) {
  if (!rows.length) {
    return null
  }
  let total = 0
  for (const row of rows) {
    const value = finiteNumber(accessor(row))
    if (value == null) {
      return null
    }
    total += value
  }
  return total
}

function accountLabel(row: PortfolioHoldingRow) {
  const derivativeAccount = row.derivative_contract?.account_id?.trim()
  if (derivativeAccount) {
    return derivativeAccount
  }
  const accountIds = (row.account_ids ?? []).filter(Boolean)
  return accountIds.length ? accountIds.join(', ') : '—'
}

function holdingCurrency(row: PortfolioHoldingRow) {
  return row.derivative_contract?.currency ?? row.instrument_core?.currency ?? ''
}

function signedNavAmountBase(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  return baseAmountForRow(
    row,
    workspace.base_currency,
    row.market_value_base,
    row.market_value,
  )
}

function coveragePill(row: PortfolioHoldingRow) {
  const live = row.coverage_status === 'price-nav-fx' || row.coverage_status === 'cash'
  return (
    <span className={`coverage-pill ${live ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
      {formatLabel(row.coverage_status)}
    </span>
  )
}

function daysBetween(asOfDate: string, targetDate: string | null | undefined) {
  if (!targetDate) {
    return null
  }
  const start = Date.parse(`${asOfDate}T00:00:00Z`)
  const end = Date.parse(`${targetDate}T00:00:00Z`)
  return Number.isFinite(start) && Number.isFinite(end)
    ? Math.ceil((end - start) / 86_400_000)
    : null
}

function instrumentNamesById(workspace: HoldingsWorkspaceResponse) {
  const names = new Map<string, string>()
  workspace.rows.forEach((row) => {
    if (row.instrument_core) {
      names.set(row.instrument_core.instrument_id, row.instrument_core.instrument_name)
    }
    if (row.economic_instrument_id && row.economic_instrument_ref) {
      names.set(row.economic_instrument_id, row.economic_instrument_ref.instrument_name)
    }
  })
  return names
}

function holdingKindLabel(row: PortfolioHoldingRow) {
  switch (row.holding_kind) {
    case 'settled_cash':
      return 'Settled Cash'
    case 'restricted_cash':
      return 'Restricted Cash'
    case 'pending_subscription':
      return 'Pending Subscription'
    case 'settlement_receivable':
      return 'Settlement Receivable'
    case 'settlement_payable':
      return 'Settlement Payable'
    case 'position_recognition_adjustment':
      return 'Recognition Adjustment'
    default:
      return formatLabel(row.holding_kind ?? 'cash')
  }
}

function optionSide(row: PortfolioHoldingRow) {
  return isOptionObligationHolding(row) || row.quantity < 0 ? 'Written' : 'Long'
}

function optionRemainingBasis(row: PortfolioHoldingRow) {
  return isOptionObligationHolding(row) ? row.premium_basis_remaining : row.cost_basis
}

function holdingDetailPath(workspace: HoldingsWorkspaceResponse, instrumentId: string) {
  return `${buildPortfolioHoldingDetailPath(workspace.portfolio_id, instrumentId)}?as_of_date=${workspace.as_of_date}`
}

function linkedInstrumentName(
  workspace: HoldingsWorkspaceResponse,
  instrumentId: string,
  label: string,
) {
  return (
    <Link
      className="holding-instrument-link"
      draggable={false}
      to={holdingDetailPath(workspace, instrumentId)}
    >
      {label}
    </Link>
  )
}

function riskPill(label: string, warning: boolean) {
  return (
    <span className={`coverage-pill ${warning ? 'coverage-pill-warning' : 'coverage-pill-live'}`}>
      {label}
    </span>
  )
}

function optionRiskLabel(state: string) {
  switch (state) {
    case 'expired_unresolved':
      return 'Action required'
    case 'uncovered':
      return 'Backing shortfall'
    case 'in_the_money':
      return 'In the money'
    case 'quote_unavailable':
      return 'Quote unavailable'
    case 'open':
      return 'Open'
    default:
      return formatLabel(state)
  }
}

function fcnRiskLabel(state: string) {
  switch (state) {
    case 'current_price_at_or_below_knock_in':
      return 'Current price at/below KI'
    case 'terms_incomplete':
      return 'Terms incomplete'
    case 'quote_unavailable':
      return 'Quote unavailable'
    case 'knocked_in':
      return 'Knock-in recorded'
    case 'knocked_out':
      return 'Knock-out recorded'
    case 'matured':
      return 'Maturity recorded'
    case 'open':
      return 'Open'
    default:
      return formatLabel(state)
  }
}

function signedDistance(value: number | null | undefined, level: string) {
  if (value == null || !Number.isFinite(value)) {
    return null
  }
  const relation = value >= 0 ? 'above' : 'below'
  return `${formatPercent(Math.abs(value))} ${relation} ${level}`
}

function FixedHoldingsTable({
  ariaLabel,
  columns,
  rows,
  subtotalLabel,
  subtotalValues,
  minimumWidth,
  onSelectHolding,
}: {
  ariaLabel: string
  columns: FixedColumn[]
  rows: PortfolioHoldingRow[]
  subtotalLabel: string
  subtotalValues?: Record<string, ReactNode>
  minimumWidth: number
  onSelectHolding?: (row: PortfolioHoldingRow) => void
}) {
  const tablePan = useHorizontalTablePan()
  const contentWidth = columns.reduce(
    (total, column) => total + (HOLDINGS_SECTION_COLUMN_WIDTHS[column.key] ?? 140),
    0,
  )
  return (
    <div
      className={`table-shell holdings-section-table-shell ${tablePan.isPanning ? 'is-panning' : ''}`}
      ref={tablePan.ref}
      {...tablePan.handlers}
    >
      <table
        className="holdings-table holdings-section-table"
        aria-label={ariaLabel}
        style={{ minWidth: Math.max(minimumWidth, contentWidth) }}
      >
        <colgroup>
          {columns.map((column) => (
            <col
              key={column.key}
              data-column-key={column.key}
              style={{ width: HOLDINGS_SECTION_COLUMN_WIDTHS[column.key] ?? 140 }}
            />
          ))}
        </colgroup>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                data-column-key={column.key}
                className={
                  column.align === 'right'
                    ? 'numeric-cell'
                    : column.align === 'center'
                      ? 'center-cell'
                      : undefined
                }
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.line_id}>
              {columns.map((column) => (
                <td
                  key={column.key}
                  data-column-key={column.key}
                  className={
                    column.align === 'right'
                      ? 'numeric-cell'
                      : column.align === 'center'
                        ? 'center-cell'
                        : column.key === 'contract' || column.key === 'description'
                          ? 'holding-name-cell'
                          : undefined
                  }
                >
                  {column.opensDetail && onSelectHolding ? (
                    <button
                      type="button"
                      className="holding-instrument-link"
                      onClick={() => onSelectHolding(row)}
                    >
                      {column.render(row)}
                    </button>
                  ) : (
                    column.render(row)
                  )}
                </td>
              ))}
            </tr>
          ))}
          <tr className="total-row holdings-section-subtotal-row">
            {columns.map((column, index) => (
              <td
                key={column.key}
                data-column-key={column.key}
                className={
                  column.align === 'right'
                    ? 'numeric-cell'
                    : column.align === 'center'
                      ? 'center-cell'
                      : undefined
                }
              >
                {index === 0 ? <strong>{subtotalLabel}</strong> : subtotalValues?.[column.key] ?? ''}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export default function HoldingsSectionTables({
  workspace,
  derivativeRows,
  cashRows,
  onSelectHolding,
  onVisibleColumnsChange,
}: HoldingsSectionTablesProps) {
  const namesById = instrumentNamesById(workspace)
  const fcnRows = derivativeRows.filter(
    (row) => row.derivative_contract?.contract_type === 'fcn',
  )
  const optionRows = derivativeRows.filter(
    (row) => row.derivative_contract?.contract_type === 'option',
  )
  const fcnContractCount = new Set(
    fcnRows.map((row) => row.derivative_contract_id ?? row.line_id),
  ).size
  const optionContractCount = new Set(
    optionRows.map((row) => row.derivative_contract_id ?? row.line_id),
  ).size
  const unclassifiedDerivativeRows = derivativeRows.filter(
    (row) =>
      row.derivative_contract?.contract_type !== 'fcn' &&
      row.derivative_contract?.contract_type !== 'option',
  )

  const derivativeBaseValue = (row: PortfolioHoldingRow) => signedNavAmountBase(row, workspace)
  const fcnColumns: FixedColumn[] = [
    {
      key: 'contract',
      label: 'Contract',
      opensDetail: true,
      render: (row) => row.derivative_contract?.contract_name ?? row.line_id,
    },
    {
      key: 'external_reference',
      label: 'External Ref',
      render: (row) => row.derivative_contract?.external_reference ?? '—',
    },
    { key: 'account', label: 'Account', render: accountLabel },
    { key: 'currency', label: 'Currency', align: 'center', render: holdingCurrency },
    {
      key: 'notional',
      label: 'Notional',
      align: 'right',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn'
          ? formatCurrency(row.derivative_contract.terms.notional, holdingCurrency(row))
          : '—',
    },
    {
      key: 'underlying_terms',
      label: 'Underlying Terms',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn' ? (
          <div className="derivative-underlying-list">
            {row.derivative_contract.terms.underlyings.map((underlying) => {
              const current = row.fcn_risk?.underlyings.find(
                (item) => item.instrument_id === underlying.instrument_id,
              )
              const currency = current?.currency || ''
              const price = (value: number | null | undefined) =>
                currency ? formatUnitPrice(value, currency) : formatNumber(value, 4)
              const term = (
                label: string,
                value: number | null | undefined,
                level: number | null | undefined,
              ) => (
                <span>
                  <span>{label}</span>{' '}
                  {price(value)}
                  {level == null ? '' : ` (${formatNumber(level, 2)}%)`}
                </span>
              )
              return (
                <div key={underlying.instrument_id} className="derivative-underlying-item">
                  {linkedInstrumentName(
                    workspace,
                    underlying.instrument_id,
                    current?.instrument_name ??
                      namesById.get(underlying.instrument_id) ??
                      underlying.instrument_id,
                  )}
                  <small className="derivative-underlying-prices">
                    {term('Spot', current?.spot, null)}
                    {term('Strike', current?.strike_price, underlying.strike_level_pct)}
                    {term('KI', current?.knock_in_price, underlying.knock_in_level_pct)}
                    {term('KO', current?.knock_out_price, underlying.knock_out_level_pct)}
                  </small>
                  <small>
                    <span>{underlying.deliverable ? 'Deliverable' : 'Cash settled'}</span>
                    {' · '}
                    <span>Initial</span>{' '}
                    {price(current?.initial_reference_price ?? underlying.initial_reference_price)}
                  </small>
                </div>
              )
            })}
          </div>
        ) : (
          '—'
        ),
    },
    {
      key: 'risk',
      label: 'Current Risk',
      render: (row) => {
        const risk = row.fcn_risk
        if (!risk) {
          return '—'
        }
        const deliveryBuffer = risk.underlyings.find(
          (item) => item.instrument_id === risk.delivery_buffer_underlying_instrument_id,
        )
        const warning = !['open', 'knocked_out', 'matured'].includes(risk.risk_state)
        return (
          <div className="holding-risk-cell">
            {riskPill(fcnRiskLabel(risk.risk_state), warning)}
            {deliveryBuffer ? (
              <small title="Spot / delivery strike - 1. This monitors current price distance and does not confirm a delivery event.">
                <span>Delivery buffer</span>{' · '}
                {deliveryBuffer.instrument_name}: {' '}
                {signedDistance(deliveryBuffer.distance_to_strike_pct, 'delivery strike')}
              </small>
            ) : null}
          </div>
        )
      },
    },
    {
      key: 'coupon',
      label: 'Annual Coupon',
      align: 'right',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn' &&
        row.derivative_contract.terms.annual_coupon_rate_pct != null
          ? `${formatNumber(row.derivative_contract.terms.annual_coupon_rate_pct, 2)}%`
          : '—',
    },
    {
      key: 'issue_date',
      label: 'Issue Date',
      align: 'center',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn'
          ? row.derivative_contract.terms.issue_date
          : '—',
    },
    {
      key: 'final_observation_date',
      label: 'Final Observation',
      align: 'center',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn'
          ? row.derivative_contract.terms.final_observation_date ?? '—'
          : '—',
    },
    {
      key: 'maturity_date',
      label: 'Maturity',
      align: 'center',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn'
          ? row.derivative_contract.terms.maturity_date
          : '—',
    },
    {
      key: 'days_to_maturity',
      label: 'Days',
      align: 'right',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn'
          ? formatNumber(daysBetween(workspace.as_of_date, row.derivative_contract.terms.maturity_date), 0)
          : '—',
    },
    {
      key: 'issuer',
      label: 'Issuer',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn'
          ? row.derivative_contract.terms.issuer || '—'
          : '—',
    },
    {
      key: 'counterparty',
      label: 'Counterparty',
      render: (row) =>
        row.derivative_contract?.contract_type === 'fcn'
          ? row.derivative_contract.terms.counterparty || '—'
          : '—',
    },
    {
      key: 'remaining_basis',
      label: 'Remaining Basis',
      align: 'right',
      render: (row) => formatCurrency(row.cost_basis, holdingCurrency(row)),
    },
    {
      key: 'signed_nav_amount_base',
      label: `Signed NAV Amount (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(derivativeBaseValue(row), workspace.base_currency),
    },
    {
      key: 'carrying_historical_base',
      label: `Historical Carrying Basis (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(row.carrying_value_historical_base, workspace.base_currency),
    },
    {
      key: 'carrying_fx_translation_base',
      label: `Carrying FX Translation (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(row.carrying_fx_translation_base, workspace.base_currency),
    },
    {
      key: 'weight',
      label: 'Portfolio Weight',
      align: 'right',
      render: (row) => formatPercent(row.allocation),
    },
    {
      key: 'valuation_basis',
      label: 'Valuation Basis',
      align: 'center',
      render: (row) => (row.valuation_basis ? formatLabel(row.valuation_basis) : '—'),
    },
    {
      key: 'fair_value_status',
      label: 'Fair Value Status',
      align: 'center',
      render: (row) =>
        row.fair_value_coverage_status
          ? formatLabel(row.fair_value_coverage_status)
          : '—',
    },
    { key: 'coverage', label: 'Coverage', align: 'center', render: coveragePill },
  ]

  const optionColumns: FixedColumn[] = [
    {
      key: 'contract',
      label: 'Contract',
      opensDetail: true,
      render: (row) => row.derivative_contract?.contract_name ?? row.line_id,
    },
    {
      key: 'external_reference',
      label: 'External Ref',
      render: (row) => row.derivative_contract?.external_reference ?? '—',
    },
    { key: 'account', label: 'Account', render: accountLabel },
    { key: 'side', label: 'Side', align: 'center', render: optionSide },
    {
      key: 'option_type',
      label: 'Type',
      align: 'center',
      render: (row) => formatLabel(row.option_type ?? (row.derivative_contract?.contract_type === 'option' ? row.derivative_contract.terms.option_type : '')),
    },
    {
      key: 'underlying',
      label: 'Underlying',
      render: (row) => {
        const underlyingId =
          row.related_underlying_id ??
          (row.derivative_contract?.contract_type === 'option'
            ? row.derivative_contract.terms.underlying_instrument_id
            : null)
        return underlyingId
          ? linkedInstrumentName(
              workspace,
              underlyingId,
              row.option_risk?.underlying_name ?? namesById.get(underlyingId) ?? underlyingId,
            )
          : '—'
      },
    },
    {
      key: 'underlying_spot',
      label: 'Underlying Spot',
      align: 'right',
      render: (row) =>
        formatUnitPrice(
          row.option_risk?.underlying_spot,
          row.option_risk?.underlying_quote_currency,
        ),
    },
    { key: 'currency', label: 'Currency', align: 'center', render: holdingCurrency },
    {
      key: 'expiry_date',
      label: 'Expiry',
      align: 'center',
      render: (row) =>
        row.expiry_date ??
        (row.derivative_contract?.contract_type === 'option'
          ? row.derivative_contract.terms.expiry_date
          : '—'),
    },
    {
      key: 'days_to_expiry',
      label: 'Days',
      align: 'right',
      render: (row) => {
        const expiry =
          row.expiry_date ??
          (row.derivative_contract?.contract_type === 'option'
            ? row.derivative_contract.terms.expiry_date
            : null)
        return formatNumber(row.days_to_expiry ?? daysBetween(workspace.as_of_date, expiry), 0)
      },
    },
    {
      key: 'strike',
      label: 'Strike',
      align: 'right',
      render: (row) =>
        formatUnitPrice(
          row.strike ??
            (row.derivative_contract?.contract_type === 'option'
              ? row.derivative_contract.terms.strike
              : null),
          row.option_risk?.underlying_quote_currency,
        ),
    },
    {
      key: 'moneyness',
      label: 'Moneyness',
      align: 'right',
      render: (row) => formatPercent(row.option_risk?.moneyness_pct),
    },
    {
      key: 'open_contracts',
      label: 'Open Contracts',
      align: 'right',
      render: (row) => formatQuantity(row.open_contract_quantity ?? Math.abs(row.quantity)),
    },
    {
      key: 'multiplier',
      label: 'Multiplier',
      align: 'right',
      render: (row) =>
        formatQuantity(
          row.contract_multiplier ??
            (row.derivative_contract?.contract_type === 'option'
              ? row.derivative_contract.terms.contract_multiplier
              : null),
        ),
    },
    {
      key: 'underlying_equivalent',
      label: 'Underlying Equivalent',
      align: 'right',
      render: (row) => formatQuantity(row.required_underlying_quantity),
    },
    {
      key: 'basis_type',
      label: 'Basis Type',
      align: 'center',
      render: (row) => (isOptionObligationHolding(row) ? 'Remaining Premium' : 'Open Cost'),
    },
    {
      key: 'remaining_basis',
      label: 'Remaining Basis',
      align: 'right',
      render: (row) => formatCurrency(optionRemainingBasis(row), holdingCurrency(row)),
    },
    {
      key: 'signed_nav_amount_base',
      label: `Signed NAV Amount (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(derivativeBaseValue(row), workspace.base_currency),
    },
    {
      key: 'carrying_historical_base',
      label: `Historical Carrying Basis (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(row.carrying_value_historical_base, workspace.base_currency),
    },
    {
      key: 'carrying_fx_translation_base',
      label: `Carrying FX Translation (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(row.carrying_fx_translation_base, workspace.base_currency),
    },
    {
      key: 'strike_notional_base',
      label: `Strike Notional (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(row.strike_notional_base, workspace.base_currency),
    },
    {
      key: 'backing',
      label: 'Portfolio Backing',
      render: (row) => {
        const backing = row.option_risk?.backing
        if (!backing) {
          return '—'
        }
        const isCash = backing.kind === 'portfolio_settled_cash'
        const amount = (value: number) =>
          isCash
            ? formatCurrency(value, backing.currency ?? holdingCurrency(row))
            : `${formatQuantity(value)} shares`
        return (
          <div className="holding-risk-cell">
            {riskPill(
              backing.shortfall > 0 ? `${formatPercent(backing.ratio)} backed` : 'Fully backed',
              backing.shortfall > 0,
            )}
            <small>
              Portfolio {isCash ? 'cash' : 'shares'} {amount(backing.available)} / {amount(backing.required)}
              {backing.shortfall > 0 ? ` · short ${amount(backing.shortfall)}` : ''}
            </small>
          </div>
        )
      },
    },
    {
      key: 'risk',
      label: 'Current Risk',
      render: (row) => {
        const risk = row.option_risk
        if (!risk) {
          return '—'
        }
        const isWritten = isOptionObligationHolding(row) || row.quantity < 0
        const warning =
          risk.risk_state === 'expired_unresolved' ||
          risk.risk_state === 'uncovered' ||
          risk.risk_state === 'quote_unavailable' ||
          (isWritten && risk.risk_state === 'in_the_money')
        return (
          <div className="holding-risk-cell">
            {riskPill(optionRiskLabel(risk.risk_state), warning)}
            {risk.intrinsic_value_per_share != null ? (
              <small>
                Intrinsic/share{' '}
                {formatUnitPrice(
                  risk.intrinsic_value_per_share,
                  risk.underlying_quote_currency,
                )}
              </small>
            ) : null}
          </div>
        )
      },
    },
    {
      key: 'weight',
      label: 'Portfolio Weight',
      align: 'right',
      render: (row) => formatPercent(row.allocation),
    },
    {
      key: 'status',
      label: 'Status',
      align: 'center',
      render: (row) => formatLabel(row.obligation_status ?? 'open'),
    },
    {
      key: 'valuation_basis',
      label: 'Valuation Basis',
      align: 'center',
      render: (row) => (row.valuation_basis ? formatLabel(row.valuation_basis) : '—'),
    },
    {
      key: 'fair_value_status',
      label: 'Fair Value Status',
      align: 'center',
      render: (row) =>
        row.fair_value_coverage_status
          ? formatLabel(row.fair_value_coverage_status)
          : '—',
    },
    { key: 'coverage', label: 'Coverage', align: 'center', render: coveragePill },
  ]

  const cashColumns: FixedColumn[] = [
    {
      key: 'description',
      label: 'Description',
      opensDetail: true,
      render: (row) => row.instrument_core?.instrument_name ?? row.line_id,
    },
    { key: 'type', label: 'Type', render: holdingKindLabel },
    { key: 'currency', label: 'Currency', align: 'center', render: holdingCurrency },
    { key: 'account', label: 'Account', render: accountLabel },
    {
      key: 'availability',
      label: 'Available',
      align: 'center',
      render: (row) => (row.available_for_trading ? 'Yes' : 'No'),
    },
    {
      key: 'local_amount',
      label: 'Local Amount',
      align: 'right',
      render: (row) => formatCurrency(row.market_value, holdingCurrency(row)),
    },
    {
      key: 'base_value',
      label: `Base Value (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(signedNavAmountBase(row, workspace), workspace.base_currency),
    },
    {
      key: 'fx_cost_basis_base',
      label: `FX Cost Basis (${workspace.base_currency})`,
      align: 'right',
      render: (row) =>
        formatCurrency(row.cost_basis_historical_base, workspace.base_currency),
    },
    {
      key: 'monetary_fx_pnl_base',
      label: `Unrealized FX P&L (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(row.unrealized_fx_pnl_base, workspace.base_currency),
    },
    {
      key: 'weight',
      label: 'Portfolio Weight',
      align: 'right',
      render: (row) => formatPercent(row.allocation),
    },
    {
      key: 'settlement_date',
      label: 'Settlement Date',
      align: 'center',
      render: (row) => row.settlement_date ?? '—',
    },
    {
      key: 'pending_until_date',
      label: 'Pending Until',
      align: 'center',
      render: (row) => row.pending_until_date ?? '—',
    },
    {
      key: 'related_instrument',
      label: 'Related Instrument',
      render: (row) =>
        row.economic_instrument_ref?.instrument_name ?? row.economic_instrument_id ?? '—',
    },
    {
      key: 'status',
      label: 'Settlement Status',
      align: 'center',
      render: (row) => (row.pending_status ? formatLabel(row.pending_status) : '—'),
    },
    { key: 'coverage', label: 'Coverage', align: 'center', render: coveragePill },
  ]

  return (
    <>
      {unclassifiedDerivativeRows.length ? (
        <div className="inline-notice inline-notice-error" role="alert">
          {unclassifiedDerivativeRows.length} derivative holding
          {unclassifiedDerivativeRows.length === 1 ? '' : 's'} cannot be classified as FCN or{' '}
          Options and are not shown below.
        </div>
      ) : null}

      {fcnRows.length ? (
        <ConfigurableHoldingsSection
          portfolioId={workspace.portfolio_id}
          viewScope="holdings_fcn"
          sectionKey="fcn"
          id="holdings-fcn-heading"
          title="FCN"
          count={fcnContractCount}
          columns={fcnColumns}
          requiredColumnKey="contract"
          systemViews={FCN_SYSTEM_VIEWS}
          onVisibleColumnsChange={onVisibleColumnsChange}
        >
          {(visibleColumnKeys) => {
            const visibleColumns = fcnColumns.filter((column) =>
              visibleColumnKeys.includes(column.key),
            )
            return (
              <FixedHoldingsTable
                ariaLabel="FCN holdings"
                columns={visibleColumns}
                rows={fcnRows}
                subtotalLabel={`FCN Subtotal (${workspace.base_currency})`}
                minimumWidth={FCN_TABLE_MIN_WIDTH}
                subtotalValues={{
                  signed_nav_amount_base: formatCurrency(
                    sumComplete(fcnRows, derivativeBaseValue),
                    workspace.base_currency,
                  ),
                  carrying_historical_base: formatCurrency(
                    sumComplete(fcnRows, (row) => row.carrying_value_historical_base),
                    workspace.base_currency,
                  ),
                  carrying_fx_translation_base: formatCurrency(
                    sumComplete(fcnRows, (row) => row.carrying_fx_translation_base),
                    workspace.base_currency,
                  ),
                  weight: formatPercent(sumComplete(fcnRows, (row) => row.allocation)),
                }}
                onSelectHolding={onSelectHolding}
              />
            )
          }}
        </ConfigurableHoldingsSection>
      ) : null}

      {optionRows.length ? (
        <ConfigurableHoldingsSection
          portfolioId={workspace.portfolio_id}
          viewScope="holdings_options"
          sectionKey="options"
          id="holdings-options-heading"
          title="Options"
          count={optionContractCount}
          columns={optionColumns}
          requiredColumnKey="contract"
          systemViews={OPTION_SYSTEM_VIEWS}
          onVisibleColumnsChange={onVisibleColumnsChange}
        >
          {(visibleColumnKeys) => {
            const visibleColumns = optionColumns.filter((column) =>
              visibleColumnKeys.includes(column.key),
            )
            return (
              <FixedHoldingsTable
                ariaLabel="Option holdings"
                columns={visibleColumns}
                rows={optionRows}
                subtotalLabel={`Options Subtotal (${workspace.base_currency})`}
                minimumWidth={OPTION_TABLE_MIN_WIDTH}
                subtotalValues={{
                  signed_nav_amount_base: formatCurrency(
                    sumComplete(optionRows, derivativeBaseValue),
                    workspace.base_currency,
                  ),
                  carrying_historical_base: formatCurrency(
                    sumComplete(optionRows, (row) => row.carrying_value_historical_base),
                    workspace.base_currency,
                  ),
                  carrying_fx_translation_base: formatCurrency(
                    sumComplete(optionRows, (row) => row.carrying_fx_translation_base),
                    workspace.base_currency,
                  ),
                  strike_notional_base: formatCurrency(
                    sumComplete(optionRows, (row) => row.strike_notional_base),
                    workspace.base_currency,
                  ),
                  weight: formatPercent(sumComplete(optionRows, (row) => row.allocation)),
                }}
                onSelectHolding={onSelectHolding}
              />
            )
          }}
        </ConfigurableHoldingsSection>
      ) : null}

      {cashRows.length ? (
        <section className="holdings-section" aria-labelledby="holdings-cash-heading">
          <div className="holdings-section-heading">
            <div className="holdings-section-title">
              <h2 id="holdings-cash-heading">Cash & Settlement</h2>
              <span className="holdings-section-count">{cashRows.length.toLocaleString()}</span>
            </div>
          </div>
          <FixedHoldingsTable
            ariaLabel="Cash and settlement holdings"
            columns={cashColumns}
            rows={cashRows}
            subtotalLabel={`Cash & Settlement Subtotal (${workspace.base_currency})`}
            minimumWidth={CASH_TABLE_MIN_WIDTH}
            subtotalValues={{
              base_value: formatCurrency(
                sumComplete(cashRows, (row) => signedNavAmountBase(row, workspace)),
                workspace.base_currency,
              ),
              fx_cost_basis_base: formatCurrency(
                sumComplete(cashRows, (row) => row.cost_basis_historical_base),
                workspace.base_currency,
              ),
              monetary_fx_pnl_base: formatCurrency(
                sumComplete(cashRows, (row) => row.unrealized_fx_pnl_base),
                workspace.base_currency,
              ),
              weight: formatPercent(sumComplete(cashRows, (row) => row.allocation)),
            }}
            onSelectHolding={onSelectHolding}
          />
        </section>
      ) : null}

    </>
  )
}
