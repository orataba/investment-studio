import { describe, expect, it } from 'vitest'

import { executionQuoteUnavailableMessage } from './executionQuotePresentation'

describe('execution quote unavailable reason presentation', () => {
  it.each([
    [
      'ambiguous_quote_series_identity',
      'Execution quote is unavailable because multiple market-data series match this instrument.',
    ],
    [
      'quote_currency_mismatch',
      'Execution quote currency does not match the instrument currency, so no quote was applied.',
    ],
    [
      'price_contract_unsupported',
      'Execution quote unavailable: price contract unsupported.',
    ],
  ])('maps %s to readable copy', (reason, expected) => {
    expect(executionQuoteUnavailableMessage(reason)).toBe(expected)
  })

  it('keeps an already-readable backend reason unchanged', () => {
    expect(executionQuoteUnavailableMessage('No eligible quote is available.')).toBe(
      'No eligible quote is available.',
    )
  })
})
