import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearPortfolioApiCache, getPortfolioFxRates } from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

function payload(rate: unknown) {
  return {
    portfolio_id: 'portfolio-ops',
    supported_currencies: ['USD', 'CNY'],
    maintained_pairs: ['USD/CNY'],
    rates: [
      {
        base_currency: 'USD',
        quote_currency: 'CNY',
        rate,
        as_of_date: '2026-07-14',
        source_kind: 'direct',
        instrument_id: 'fx-usd-cny',
        source_instrument_ids: ['fx-usd-cny'],
        source_ref: 'test:high-precision-fx',
        status: 'complete',
      },
    ],
  }
}

describe('shared FX reference exact-decimal boundary', () => {
  it('preserves an 18-decimal market reference without six-place truncation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify(payload('7.123456789012345678')), { status: 200 }),
        ),
      ),
    )

    const response = await getPortfolioFxRates('portfolio-ops')

    expect(response.rates[0]?.rate).toBe('7.123456789012345678')
  })

  it('rejects a binary JSON number instead of accepting a rounded reference', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(payload(7.123456789012345678)), { status: 200 })),
      ),
    )

    await expect(getPortfolioFxRates('portfolio-ops')).rejects.toThrow(
      /canonical exact decimal string/,
    )
  })
})
