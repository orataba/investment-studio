import type { ReactNode } from 'react'

const INSTRUMENT_RETURN_COLUMN_KEYS = new Set([
  'instrument_return_1w',
  'instrument_return_1m',
  'instrument_return_mtd',
  'instrument_return_ytd',
  'instrument_return_1y',
])

export const PORTFOLIO_RETURN_WITHHELD_REASON =
  'Portfolio return is reported as TWR on Overview and Performance.'

export function isPortfolioReturnColumn(columnKey: string) {
  return INSTRUMENT_RETURN_COLUMN_KEYS.has(columnKey)
}

export type HoldingsTotalCell = {
  key: string
  className?: string
  content?: ReactNode
}

export default function HoldingsTotalRow({
  className,
  label,
  cells,
}: {
  className: string
  label: string
  cells: HoldingsTotalCell[]
}) {
  return (
    <tr className={className}>
      {cells.map((cell, index) => {
        const withholdInstrumentReturn = isPortfolioReturnColumn(cell.key)
        return (
          <td
            key={cell.key}
            data-column-key={cell.key}
            className={cell.className}
            title={withholdInstrumentReturn ? PORTFOLIO_RETURN_WITHHELD_REASON : undefined}
          >
            {index === 0 ? (
              <strong>{label}</strong>
            ) : withholdInstrumentReturn ? (
              '—'
            ) : (
              cell.content ?? ''
            )}
          </td>
        )
      })}
    </tr>
  )
}
