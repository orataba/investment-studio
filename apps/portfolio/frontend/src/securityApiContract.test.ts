import { afterEach, expect, it, vi } from 'vitest'
import { clearPortfolioApiCache, getPortfolioInstruments, materializePortfolioSecurity } from './lib/api'
import { instrumentFixture } from './test/portfolioFixtures'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

it('preserves the fixed Futu source across registration and subsequent registry reads', async () => {
  const record = {
    instrument_core: instrumentFixture({ instrument_id: '2822-hk', instrument_type: 'etf', currency: 'HKD' }),
    source_settings: { source_api_profile: 'futu' },
    latest_market_data: [], coverage_state: 'unavailable',
    quote_selection_policy: { trading: ['close'], valuation: ['close'], total_return: ['close'], chart: ['close'], reference: ['close'] },
  }
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(record), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ portfolio_id: 'portfolio-1', instruments: [record] }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const request = { instrument_type: 'etf' as const, catalog_provider: 'futu' as const, catalog_symbol: 'HK.02822' }

  expect((await materializePortfolioSecurity('portfolio-1', request)).source_settings).toEqual(record.source_settings)
  expect((await getPortfolioInstruments('portfolio-1')).instruments[0].source_settings).toEqual(record.source_settings)
  expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/portfolios/portfolio-1/securities/materialize',
    expect.objectContaining({ method: 'POST', body: JSON.stringify(request) }))
})
