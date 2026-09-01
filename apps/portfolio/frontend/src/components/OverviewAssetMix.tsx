import type { CSSProperties } from 'react'

import type {
  HoldingsWorkspaceResponse,
  PortfolioHoldingRow,
} from '../lib/api'
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  formatSignedCurrency,
  signedValueClass,
} from '../lib/format'
import { baseAmountForRow, completeAmountSum } from '../lib/holdingAmounts'
import {
  holdingDayChangeUnavailable,
  holdingUsesEventValuation,
} from '../lib/holdingPresentation'

type AssetMixCoverage = {
  label: string
  tone: 'complete' | 'warning' | 'neutral'
}

export type AssetMixSummaryRow = {
  key: AssetMixCategoryKey | 'portfolio_total'
  label: string
  lineCount: number
  signedNavAmount: number | null
  weight: number | null
  dayChange: number | null
  forwardRiskShare: number | null
  coverage: AssetMixCoverage
}

export type AssetMixSummary = {
  categories: AssetMixSummaryRow[]
  total: AssetMixSummaryRow
  classificationComplete: boolean
}

type AssetMixCategoryKey = 'securities' | 'fcn' | 'options' | 'cash_and_settlement'

const CATEGORY_DEFINITIONS: Array<{
  key: AssetMixCategoryKey
  label: string
  matches: (row: PortfolioHoldingRow) => boolean
}> = [
  {
    key: 'securities',
    label: 'Securities',
    matches: (row) => row.holding_category === 'securities',
  },
  {
    key: 'fcn',
    label: 'FCN',
    matches: (row) =>
      row.holding_category === 'derivatives' &&
      row.derivative_contract?.contract_type === 'fcn',
  },
  {
    key: 'options',
    label: 'Options',
    matches: (row) =>
      row.holding_category === 'derivatives' &&
      row.derivative_contract?.contract_type === 'option',
  },
  {
    key: 'cash_and_settlement',
    label: 'Cash & Settlement',
    matches: (row) => row.holding_category === 'cash_and_settlement',
  },
]

const RECONCILIATION_RELATIVE_TOLERANCE = 1e-9

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function rowBaseValue(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  return baseAmountForRow(
    row,
    workspace.base_currency,
    row.market_value_base,
    row.market_value,
  )
}

function categoryDayChange(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
) {
  if (!rows.length || rows.some(holdingDayChangeUnavailable)) {
    return null
  }
  return completeAmountSum(
    rows.map((row) =>
      baseAmountForRow(
        row,
        workspace.base_currency,
        row.day_change_value_base,
        row.day_change_value,
      ),
    ),
  )
}

function categoryForwardRiskShare(
  category: AssetMixCategoryKey,
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
) {
  if (!rows.length || category === 'fcn' || category === 'options') {
    return null
  }
  if (rows.every((row) => row.forward_risk_status === 'modeled_zero')) {
    return 0
  }
  if (workspace.forward_risk?.status !== 'ok') {
    return null
  }

  let total = 0
  let hasModeledRisk = false
  for (const row of rows) {
    if (row.forward_risk_status === 'modeled_zero') {
      continue
    }
    const baseValue = rowBaseValue(row, workspace)
    if (
      baseValue != null &&
      Math.abs(baseValue) <= 1e-12 &&
      row.forward_risk_status === 'no_exposure'
    ) {
      continue
    }
    const share = finiteNumber(row.forward_risk_share)
    if (row.forward_risk_status !== 'ok' || share == null) {
      return null
    }
    total += share
    hasModeledRisk = true
  }
  return hasModeledRisk ? total : null
}

function categoryCoverage(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
): AssetMixCoverage {
  if (!rows.length) {
    return { label: 'No holdings', tone: 'neutral' }
  }
  const valuedCount = rows.filter((row) => rowBaseValue(row, workspace) != null).length
  if (valuedCount !== rows.length) {
    return {
      label: `${formatNumber(valuedCount, 0)} / ${formatNumber(rows.length, 0)} valued`,
      tone: 'warning',
    }
  }
  if (rows.some(holdingUsesEventValuation)) {
    return { label: 'Event carrying', tone: 'warning' }
  }
  return { label: 'Complete', tone: 'complete' }
}

function totalForwardRiskShare(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
) {
  if (workspace.forward_risk?.status !== 'ok') {
    return null
  }
  let total = 0
  let hasModeledRisk = false
  for (const row of rows) {
    if (
      row.holding_category === 'derivatives' ||
      row.forward_risk_status === 'modeled_zero'
    ) {
      continue
    }
    const baseValue = rowBaseValue(row, workspace)
    if (
      baseValue != null &&
      Math.abs(baseValue) <= 1e-12 &&
      row.forward_risk_status === 'no_exposure'
    ) {
      continue
    }
    const share = finiteNumber(row.forward_risk_share)
    if (row.forward_risk_status !== 'ok' || share == null) {
      return null
    }
    total += share
    hasModeledRisk = true
  }
  return hasModeledRisk ? total : null
}

export function buildAssetMixSummary(
  workspace: HoldingsWorkspaceResponse,
): AssetMixSummary {
  const nav = finiteNumber(workspace.totals.nav)
  const categories = CATEGORY_DEFINITIONS.map(({ key, label, matches }) => {
    const rows = workspace.rows.filter(matches)
    const signedNavAmount = completeAmountSum(
      rows.map((row) => rowBaseValue(row, workspace)),
    )
    return {
      key,
      label,
      lineCount: rows.length,
      signedNavAmount,
      weight:
        signedNavAmount != null && nav != null && Math.abs(nav) > 1e-12
          ? signedNavAmount / nav
          : null,
      dayChange: categoryDayChange(rows, workspace),
      forwardRiskShare: categoryForwardRiskShare(key, rows, workspace),
      coverage: categoryCoverage(rows, workspace),
    }
  })

  const rowNetValue = completeAmountSum(
    workspace.rows.map((row) => rowBaseValue(row, workspace)),
  )
  const reconciliationScale = Math.max(
    1,
    Math.abs(nav ?? 0),
    Math.abs(rowNetValue ?? 0),
  )
  const reconciled =
    nav != null &&
    rowNetValue != null &&
    Math.abs(rowNetValue - nav) <=
      reconciliationScale * RECONCILIATION_RELATIVE_TOLERANCE
  const classificationComplete = !workspace.rows.some(
    (row) =>
      row.holding_category === 'derivatives' &&
      row.derivative_contract?.contract_type !== 'fcn' &&
      row.derivative_contract?.contract_type !== 'option',
  )

  return {
    categories,
    classificationComplete,
    total: {
      key: 'portfolio_total',
      label: 'Portfolio Total',
      lineCount: workspace.rows.length,
      signedNavAmount: nav,
      weight: nav != null && Math.abs(nav) > 1e-12 ? 1 : null,
      dayChange:
        workspace.rows.length &&
        !workspace.rows.some(holdingDayChangeUnavailable)
          ? completeAmountSum(
              workspace.rows.map((row) =>
                baseAmountForRow(
                  row,
                  workspace.base_currency,
                  row.day_change_value_base,
                  row.day_change_value,
                ),
              ),
            )
          : null,
      forwardRiskShare: totalForwardRiskShare(workspace.rows, workspace),
      coverage: !classificationComplete
        ? { label: 'Classification incomplete', tone: 'warning' }
        : reconciled
          ? { label: 'NAV reconciled', tone: 'complete' }
          : { label: 'NAV unreconciled', tone: 'warning' },
    },
  }
}

function AssetMixSignedBars({
  rows,
  classificationComplete,
}: {
  rows: AssetMixSummaryRow[]
  classificationComplete: boolean
}) {
  if (!classificationComplete) {
    return <div className="price-chart-empty">Asset mix unavailable: unclassified derivative holding.</div>
  }
  const heldRows = rows.filter((row) => row.lineCount > 0)
  if (!heldRows.length || heldRows.some((row) => row.weight == null)) {
    return <div className="price-chart-empty">Asset mix unavailable.</div>
  }

  const availableRows = rows.filter(
    (row): row is AssetMixSummaryRow & { weight: number } => row.weight != null,
  )

  const minimum = Math.min(0, ...availableRows.map((row) => row.weight))
  const maximum = Math.max(0, ...availableRows.map((row) => row.weight))
  const span = maximum - minimum
  const zeroPosition = span > 0 ? ((0 - minimum) / span) * 100 : 0
  const ariaLabel = `Asset mix by signed portfolio weight: ${rows
    .map((row) => `${row.label} ${formatPercent(row.weight)}`)
    .join(', ')}`

  return (
    <div className="overview-asset-mix-bars" role="img" aria-label={ariaLabel}>
      {rows.map((row) => {
        const position =
          row.weight != null && span > 0
            ? ((row.weight - minimum) / span) * 100
            : zeroPosition
        const style = {
          '--asset-mix-bar-left': `${Math.min(zeroPosition, position)}%`,
          '--asset-mix-bar-width': `${Math.abs(position - zeroPosition)}%`,
          '--asset-mix-zero-left': `${zeroPosition}%`,
        } as CSSProperties
        return (
          <div className="overview-asset-mix-bar-row" key={row.key} style={style}>
            <div className="overview-asset-mix-bar-label">
              <strong>{row.label}</strong>
              <span>{formatPercent(row.weight)}</span>
            </div>
            <div className="overview-asset-mix-bar-track">
              <span className="overview-asset-mix-zero-axis" />
              {row.weight != null && Math.abs(row.weight) > 1e-12 ? (
                <span
                  className={`overview-asset-mix-bar overview-asset-mix-bar-${row.key}`}
                />
              ) : null}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function CoveragePill({ coverage }: { coverage: AssetMixCoverage }) {
  return (
    <span
      className={`coverage-pill ${
        coverage.tone === 'complete'
          ? 'coverage-pill-live'
          : coverage.tone === 'warning'
            ? 'coverage-pill-warning'
            : ''
      }`.trim()}
    >
      {coverage.label}
    </span>
  )
}

export default function OverviewAssetMix({
  workspace,
}: {
  workspace: HoldingsWorkspaceResponse
}) {
  const summary = buildAssetMixSummary(workspace)
  const rows = [...summary.categories, summary.total]

  return (
    <section
      className="portfolio-section-block overview-asset-mix-block"
      aria-labelledby="overview-asset-mix-title"
    >
      <div className="portfolio-detail-toolbar portfolio-section-toolbar overview-section-toolbar">
        <div className="panel-title" id="overview-asset-mix-title">
          Asset Mix
        </div>
      </div>
      <div className="overview-asset-mix-grid">
        <AssetMixSignedBars
          rows={summary.categories}
          classificationComplete={summary.classificationComplete}
        />
        <div className="table-shell overview-asset-mix-table-shell">
          <table className="overview-asset-mix-table" aria-label="Asset mix summary">
            <thead>
              <tr>
                <th>Category</th>
                <th>Lines</th>
                <th>Signed NAV Amount ({workspace.base_currency})</th>
                <th>Portfolio Weight</th>
                <th>Day Change ({workspace.base_currency})</th>
                <th>Forward RC</th>
                <th>Valuation Coverage</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.key}
                  className={
                    row.key === 'portfolio_total'
                      ? 'total-row overview-asset-mix-total-row'
                      : undefined
                  }
                >
                  <th scope="row">{row.label}</th>
                  <td className="numeric-cell">{formatNumber(row.lineCount, 0)}</td>
                  <td className="numeric-cell">
                    {formatCurrency(row.signedNavAmount, workspace.base_currency)}
                  </td>
                  <td className="numeric-cell">{formatPercent(row.weight)}</td>
                  <td className={`numeric-cell ${signedValueClass(row.dayChange)}`}>
                    {row.dayChange == null
                      ? 'N/A'
                      : formatSignedCurrency(row.dayChange, workspace.base_currency)}
                  </td>
                  <td className="numeric-cell">
                    {row.forwardRiskShare == null
                      ? 'N/A'
                      : formatPercent(row.forwardRiskShare)}
                  </td>
                  <td>
                    <CoveragePill coverage={row.coverage} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
