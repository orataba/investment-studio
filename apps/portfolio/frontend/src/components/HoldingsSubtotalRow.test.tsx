import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import HoldingsSubtotalRow, {
  CURRENT_HOLDINGS_RETURN_BASIS,
} from './HoldingsSubtotalRow'

describe('HoldingsSubtotalRow contract', () => {
  it('renders current-holdings basket returns with an explicit basis', () => {
    render(
      <table>
        <tbody>
          <HoldingsSubtotalRow
            className="holdings-subtotal-row"
            label="Securities Subtotal (USD)"
            cells={[
              { key: 'instrument' },
              { key: 'market_value_base', content: '$1,000.00' },
              { key: 'instrument_return_1w', content: '+99.00%' },
              { key: 'instrument_return_1m', content: '+98.00%' },
              { key: 'instrument_return_3m', content: '+97.00%' },
              { key: 'instrument_return_6m', content: '+96.00%' },
              { key: 'instrument_return_1y', content: '+88.00%' },
            ]}
          />
        </tbody>
      </table>,
    )

    const row = screen.getByText('Securities Subtotal (USD)').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row!).getByText('$1,000.00')).toBeInTheDocument()
    const expectedReturns = {
      instrument_return_1w: '+99.00%',
      instrument_return_1m: '+98.00%',
      instrument_return_3m: '+97.00%',
      instrument_return_6m: '+96.00%',
      instrument_return_1y: '+88.00%',
    }
    for (const [key, expectedReturn] of Object.entries(expectedReturns)) {
      const cell = row!.querySelector(`[data-column-key="${key}"]`)
      expect(cell).toHaveTextContent(expectedReturn)
      expect(cell).toHaveAttribute('title', CURRENT_HOLDINGS_RETURN_BASIS)
    }
  })
})
