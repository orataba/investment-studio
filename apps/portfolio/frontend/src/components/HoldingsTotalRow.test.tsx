import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import HoldingsTotalRow, {
  PORTFOLIO_RETURN_WITHHELD_REASON,
} from './HoldingsTotalRow'

describe('HoldingsTotalRow extraction contract', () => {
  it('renders ordinary totals but withholds instrument returns from Portfolio Total', () => {
    render(
      <table>
        <tbody>
          <HoldingsTotalRow
            className="holdings-total-row"
            label="Portfolio Total (USD)"
            cells={[
              { key: 'instrument' },
              { key: 'market_value_base', content: '$1,000.00' },
              { key: 'instrument_return_1w', content: '+99.00%' },
              { key: 'instrument_return_1y', content: '+88.00%' },
            ]}
          />
        </tbody>
      </table>,
    )

    const row = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row!).getByText('$1,000.00')).toBeInTheDocument()
    expect(row).not.toHaveTextContent('+99.00%')
    expect(row).not.toHaveTextContent('+88.00%')
    for (const key of ['instrument_return_1w', 'instrument_return_1y']) {
      const cell = row!.querySelector(`[data-column-key="${key}"]`)
      expect(cell).toHaveTextContent('—')
      expect(cell).toHaveAttribute('title', PORTFOLIO_RETURN_WITHHELD_REASON)
    }
  })
})
