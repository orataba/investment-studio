import type { ReactNode } from 'react'

const INSTRUMENT_RETURN_COLUMN_KEYS = new Set([
  'instrument_return_1w',
  'instrument_return_1m',
  'instrument_return_3m',
  'instrument_return_6m',
  'instrument_return_mtd',
  'instrument_return_ytd',
  'instrument_return_1y',
])

export const CURRENT_HOLDINGS_RETURN_BASIS =
  'Current-holdings basket market return using as-of signed base-value weights for the displayed subtotal. It is neither book unrealized P&L nor historical portfolio TWR.'

function isBasketReturnColumn(columnKey: string) {
  return INSTRUMENT_RETURN_COLUMN_KEYS.has(columnKey)
}

export type HoldingsSubtotalCell = {
  key: string
  className?: string
  content?: ReactNode
  aggregationKind?: string
  title?: string
}

export default function HoldingsSubtotalRow({
  className,
  label,
  cells,
}: {
  className: string
  label: string
  cells: HoldingsSubtotalCell[]
}) {
  return (
    <tr className={`portfolio-tree-row ${className}`} data-tree-level="primary">
      {cells.map((cell, index) => {
        const isCurrentHoldingsReturn = isBasketReturnColumn(cell.key)
        return (
          <td
            key={cell.key}
            data-column-key={cell.key}
            data-aggregation-kind={cell.aggregationKind}
            className={cell.className}
            title={cell.title ?? (isCurrentHoldingsReturn ? CURRENT_HOLDINGS_RETURN_BASIS : undefined)}
          >
            {index === 0 ? <strong>{label}</strong> : cell.content ?? ''}
          </td>
        )
      })}
    </tr>
  )
}
