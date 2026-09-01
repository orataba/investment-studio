import type { ReactNode } from 'react'

import ConfigurableHoldingsSection, {
  type HoldingsSectionSystemView,
} from './ConfigurableHoldingsSection'
import HoldingsTotalRow, { type HoldingsTotalCell } from './HoldingsTotalRow'
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

type FixedColumn = {
  key: string
  label: string
  align?: 'right' | 'center'
  render: (row: PortfolioHoldingRow) => ReactNode
}

export type HoldingsPortfolioTotalColumn = HoldingsTotalCell & {
  label: string
}

type HoldingsSectionTablesProps = {
  workspace: HoldingsWorkspaceResponse
  derivativeRows: PortfolioHoldingRow[]
  cashRows: PortfolioHoldingRow[]
  portfolioTotalColumns: HoldingsPortfolioTotalColumn[]
  onSelectHolding: (row: PortfolioHoldingRow) => void
  onVisibleColumnsChange?: (sectionKey: string, columns: string[]) => void
}

export type HoldingsSectionKey = 'fcn' | 'options' | 'cash' | 'total'

export type HoldingsSectionVisibleColumns = Record<HoldingsSectionKey, string[]>

export const HOLDINGS_SECTION_COLUMN_KEYS: HoldingsSectionVisibleColumns = {
  fcn: [
    'contract',
    'external_reference',
    'account',
    'currency',
    'notional',
    'underlying_terms',
    'coupon',
    'issue_date',
    'final_observation_date',
    'maturity_date',
    'days_to_maturity',
    'issuer',
    'counterparty',
    'remaining_basis',
    'signed_nav_amount_base',
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
    'currency',
    'expiry_date',
    'days_to_expiry',
    'strike',
    'open_contracts',
    'multiplier',
    'underlying_equivalent',
    'basis_type',
    'remaining_basis',
    'signed_nav_amount_base',
    'strike_notional_base',
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
    'weight',
    'quote_date',
    'day_change_base',
    'day_fx_return',
    'settlement_date',
    'pending_until_date',
    'related_instrument',
    'status',
    'coverage',
  ],
  total: [
    'portfolio',
    'market_value_base',
    'weight',
    'cost_basis_base',
    'unrealized_return_basis',
    'unrealized_value',
    'unrealized_pct',
    'day_change_value',
    'day_change_pct',
    'instrument_return_1w',
    'instrument_return_1m',
    'instrument_return_mtd',
    'instrument_return_ytd',
    'instrument_volatility_1m',
    'instrument_volatility_3m',
    'instrument_volatility_1y',
    'instrument_current_drawdown',
    'instrument_max_drawdown',
    'forward_risk_share',
    'forward_volatility',
    'risk_coverage',
  ],
}

export const DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS: HoldingsSectionVisibleColumns = {
  fcn: [
    'contract',
    'account',
    'currency',
    'notional',
    'underlying_terms',
    'coupon',
    'maturity_date',
    'days_to_maturity',
    'remaining_basis',
    'signed_nav_amount_base',
    'weight',
    'valuation_basis',
    'coverage',
  ],
  options: [
    'contract',
    'account',
    'side',
    'option_type',
    'underlying',
    'currency',
    'expiry_date',
    'days_to_expiry',
    'strike',
    'open_contracts',
    'multiplier',
    'remaining_basis',
    'signed_nav_amount_base',
    'strike_notional_base',
    'weight',
    'status',
    'valuation_basis',
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
    'weight',
    'settlement_date',
    'pending_until_date',
    'related_instrument',
    'status',
    'coverage',
  ],
  total: [
    'portfolio',
    'market_value_base',
    'weight',
    'cost_basis_base',
    'unrealized_return_basis',
    'unrealized_value',
    'unrealized_pct',
    'day_change_value',
    'day_change_pct',
    'instrument_return_1w',
    'instrument_return_1m',
    'instrument_return_mtd',
    'instrument_return_ytd',
    'forward_risk_share',
    'forward_volatility',
    'risk_coverage',
  ],
}

const FCN_SYSTEM_VIEWS: HoldingsSectionSystemView[] = [
  { id: 'position', name: 'Position', columns: DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.fcn },
  {
    id: 'terms',
    name: 'Terms & Events',
    columns: [
      'contract',
      'external_reference',
      'currency',
      'notional',
      'underlying_terms',
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
      'remaining_basis',
      'signed_nav_amount_base',
      'weight',
      'valuation_basis',
      'fair_value_status',
      'coverage',
    ],
  },
]

const OPTION_SYSTEM_VIEWS: HoldingsSectionSystemView[] = [
  { id: 'position', name: 'Position', columns: DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.options },
  {
    id: 'contract',
    name: 'Contract Terms',
    columns: [
      'contract',
      'external_reference',
      'side',
      'option_type',
      'underlying',
      'currency',
      'expiry_date',
      'days_to_expiry',
      'strike',
      'open_contracts',
      'multiplier',
      'underlying_equivalent',
      'strike_notional_base',
      'status',
    ],
  },
  {
    id: 'valuation',
    name: 'Valuation',
    columns: [
      'contract',
      'account',
      'side',
      'basis_type',
      'remaining_basis',
      'signed_nav_amount_base',
      'weight',
      'valuation_basis',
      'fair_value_status',
      'coverage',
    ],
  },
]

const CASH_SYSTEM_VIEWS: HoldingsSectionSystemView[] = [
  { id: 'balances', name: 'Balances', columns: DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.cash },
  {
    id: 'settlement',
    name: 'Settlement',
    columns: [
      'description',
      'type',
      'currency',
      'account',
      'availability',
      'base_value',
      'weight',
      'settlement_date',
      'pending_until_date',
      'related_instrument',
      'status',
      'coverage',
    ],
  },
  {
    id: 'daily-fx',
    name: 'Daily FX',
    columns: [
      'description',
      'currency',
      'local_amount',
      'base_value',
      'weight',
      'quote_date',
      'day_change_base',
      'day_fx_return',
      'coverage',
    ],
  },
]

const TOTAL_SYSTEM_VIEWS: HoldingsSectionSystemView[] = [
  { id: 'summary', name: 'Summary', columns: DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.total },
  {
    id: 'return-risk',
    name: 'Return & Risk',
    columns: [
      'portfolio',
      'market_value_base',
      'weight',
      'instrument_return_1w',
      'instrument_return_1m',
      'instrument_return_mtd',
      'instrument_return_ytd',
      'instrument_volatility_1m',
      'instrument_volatility_3m',
      'instrument_volatility_1y',
      'instrument_current_drawdown',
      'instrument_max_drawdown',
      'forward_risk_share',
      'forward_volatility',
      'risk_coverage',
    ],
  },
]

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

function sumAvailable(
  rows: PortfolioHoldingRow[],
  accessor: (row: PortfolioHoldingRow) => number | null | undefined,
) {
  let hasValue = false
  const total = rows.reduce((sum, row) => {
    const value = finiteNumber(accessor(row))
    if (value == null) {
      return sum
    }
    hasValue = true
    return sum + value
  }, 0)
  return hasValue ? total : null
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

function FixedHoldingsTable({
  ariaLabel,
  columns,
  rows,
  emptyLabel,
  subtotalLabel,
  subtotalValues,
  onSelectHolding,
}: {
  ariaLabel: string
  columns: FixedColumn[]
  rows: PortfolioHoldingRow[]
  emptyLabel: string
  subtotalLabel?: string
  subtotalValues?: Record<string, ReactNode>
  onSelectHolding?: (row: PortfolioHoldingRow) => void
}) {
  return (
    <div className="table-shell holdings-section-table-shell">
      <table className="holdings-table holdings-section-table" aria-label={ariaLabel}>
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
            <tr
              key={row.line_id}
              tabIndex={onSelectHolding ? 0 : undefined}
              className={onSelectHolding ? 'holdings-section-data-row' : undefined}
              onClick={onSelectHolding ? () => onSelectHolding(row) : undefined}
              onKeyDown={
                onSelectHolding
                  ? (event) => {
                      if (event.key === 'Enter') {
                        onSelectHolding(row)
                      }
                    }
                  : undefined
              }
            >
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
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
          {!rows.length ? (
            <tr className="table-status-row">
              <td colSpan={columns.length} className="empty-state-cell">
                {emptyLabel}
              </td>
            </tr>
          ) : null}
          {rows.length && subtotalLabel ? (
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
          ) : null}
        </tbody>
      </table>
    </div>
  )
}

export default function HoldingsSectionTables({
  workspace,
  derivativeRows,
  cashRows,
  portfolioTotalColumns,
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
            {row.derivative_contract.terms.underlyings.map((underlying) => (
              <div key={underlying.instrument_id} className="derivative-underlying-item">
                <span>{namesById.get(underlying.instrument_id) ?? underlying.instrument_id}</span>
                <small>
                  Initial {formatNumber(underlying.initial_reference_price, 4)} · Strike{' '}
                  {underlying.strike_level_pct == null
                    ? '—'
                    : `${formatNumber(underlying.strike_level_pct, 2)}%`}{' '}
                  · KI{' '}
                  {underlying.knock_in_level_pct == null
                    ? '—'
                    : `${formatNumber(underlying.knock_in_level_pct, 2)}%`}{' '}
                  · KO{' '}
                  {underlying.knock_out_level_pct == null
                    ? '—'
                    : `${formatNumber(underlying.knock_out_level_pct, 2)}%`}{' '}
                  · {underlying.deliverable ? 'Deliverable' : 'Cash settled'}
                </small>
              </div>
            ))}
          </div>
        ) : (
          '—'
        ),
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
        return underlyingId ? namesById.get(underlyingId) ?? underlyingId : '—'
      },
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
          holdingCurrency(row),
        ),
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
      key: 'strike_notional_base',
      label: `Strike Notional (${workspace.base_currency})`,
      align: 'right',
      render: (row) => formatCurrency(row.strike_notional_base, workspace.base_currency),
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
    { key: 'description', label: 'Description', render: (row) => row.instrument_core?.instrument_name ?? row.line_id },
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
      key: 'weight',
      label: 'Portfolio Weight',
      align: 'right',
      render: (row) => formatPercent(row.allocation),
    },
    {
      key: 'quote_date',
      label: 'FX / Quote Date',
      align: 'center',
      render: (row) => row.quote_as_of_date ?? '—',
    },
    {
      key: 'day_change_base',
      label: `Day Change (${workspace.base_currency})`,
      align: 'right',
      render: (row) =>
        formatCurrency(
          baseAmountForRow(
            row,
            workspace.base_currency,
            row.day_change_value_base,
            row.day_change_value,
          ),
          workspace.base_currency,
        ),
    },
    {
      key: 'day_fx_return',
      label: 'Day FX Return',
      align: 'right',
      render: (row) => formatPercent(row.day_change_pct),
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

      <ConfigurableHoldingsSection
        portfolioId={workspace.portfolio_id}
        viewScope="holdings_fcn"
        sectionKey="fcn"
        id="holdings-fcn-heading"
        title="FCN"
        count={fcnRows.length}
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
              emptyLabel="No FCN holdings."
              subtotalLabel={`FCN Subtotal (${workspace.base_currency})`}
              subtotalValues={{
                signed_nav_amount_base: formatCurrency(
                  sumComplete(fcnRows, derivativeBaseValue),
                  workspace.base_currency,
                ),
                weight: formatPercent(sumComplete(fcnRows, (row) => row.allocation)),
              }}
              onSelectHolding={onSelectHolding}
            />
          )
        }}
      </ConfigurableHoldingsSection>

      <ConfigurableHoldingsSection
        portfolioId={workspace.portfolio_id}
        viewScope="holdings_options"
        sectionKey="options"
        id="holdings-options-heading"
        title="Options"
        count={optionRows.length}
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
              emptyLabel="No option holdings."
              subtotalLabel={`Options Subtotal (${workspace.base_currency})`}
              subtotalValues={{
                signed_nav_amount_base: formatCurrency(
                  sumComplete(optionRows, derivativeBaseValue),
                  workspace.base_currency,
                ),
                strike_notional_base: formatCurrency(
                  sumAvailable(optionRows, (row) => row.strike_notional_base),
                  workspace.base_currency,
                ),
                weight: formatPercent(sumComplete(optionRows, (row) => row.allocation)),
              }}
              onSelectHolding={onSelectHolding}
            />
          )
        }}
      </ConfigurableHoldingsSection>

      <ConfigurableHoldingsSection
        portfolioId={workspace.portfolio_id}
        viewScope="holdings_cash"
        sectionKey="cash"
        id="holdings-cash-heading"
        title="Cash & Settlement"
        count={cashRows.length}
        columns={cashColumns}
        requiredColumnKey="description"
        systemViews={CASH_SYSTEM_VIEWS}
        onVisibleColumnsChange={onVisibleColumnsChange}
      >
        {(visibleColumnKeys) => {
          const visibleColumns = cashColumns.filter((column) =>
            visibleColumnKeys.includes(column.key),
          )
          return (
            <FixedHoldingsTable
              ariaLabel="Cash and settlement holdings"
              columns={visibleColumns}
              rows={cashRows}
              emptyLabel="No cash or settlement balances."
              subtotalLabel={`Cash & Settlement Subtotal (${workspace.base_currency})`}
              subtotalValues={{
                base_value: formatCurrency(
                  sumComplete(cashRows, (row) => signedNavAmountBase(row, workspace)),
                  workspace.base_currency,
                ),
                weight: formatPercent(sumComplete(cashRows, (row) => row.allocation)),
                day_change_base: formatCurrency(
                  sumComplete(cashRows, (row) =>
                    baseAmountForRow(
                      row,
                      workspace.base_currency,
                      row.day_change_value_base,
                      row.day_change_value,
                    ),
                  ),
                  workspace.base_currency,
                ),
              }}
            />
          )
        }}
      </ConfigurableHoldingsSection>

      <ConfigurableHoldingsSection
        portfolioId={workspace.portfolio_id}
        viewScope="holdings_total"
        sectionKey="total"
        id="holdings-total-heading"
        title="Portfolio Total"
        className="holdings-portfolio-total-section"
        columns={portfolioTotalColumns}
        requiredColumnKey="portfolio"
        systemViews={TOTAL_SYSTEM_VIEWS}
        onVisibleColumnsChange={onVisibleColumnsChange}
      >
        {(visibleColumnKeys) => {
          const visibleColumns = portfolioTotalColumns.filter((column) =>
            visibleColumnKeys.includes(column.key),
          )
          return (
            <div className="table-shell holdings-section-table-shell">
              <table className="holdings-table holdings-section-table holdings-portfolio-total-table" aria-label="Portfolio total holdings">
                <thead>
                  <tr>
                    {visibleColumns.map((column) => (
                      <th
                        key={column.key}
                        scope="col"
                        data-column-key={column.key}
                        className={column.className}
                        title={column.title}
                      >
                        {column.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <HoldingsTotalRow
                    className="total-row holdings-total-row"
                    label={`Portfolio Total (${workspace.base_currency})`}
                    cells={visibleColumns}
                  />
                </tbody>
              </table>
            </div>
          )
        }}
      </ConfigurableHoldingsSection>
    </>
  )
}
