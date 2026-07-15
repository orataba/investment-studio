import { describe, expect, it } from 'vitest'

import { executionQuoteUnavailableMessage } from './executionQuotePresentation'

describe('execution quote unavailable reason presentation', () => {
  it.each([
    [
      'clean_price_requires_matching_accrued_interest',
      'Clean bond price requires matching same-date accrued interest, so no execution quote was applied.',
    ],
    [
      'bond_price_contract_unavailable',
      'Bond quote is missing a valid percent-of-par unit or scale, so no execution quote was applied.',
    ],
    [
      'ambiguous_quote_series_identity',
      'Execution quote is unavailable because multiple market-data series match this instrument.',
    ],
    [
      'quote_currency_mismatch',
      'Execution quote currency does not match the instrument currency, so no quote was applied.',
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
