import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getSharedInstruments,
  searchPlatformEquityCatalog,
  type SharedInstrumentRecord,
} from './api'
import { searchWatchlistInstrumentCandidates } from './watchlistInstrumentSearch'

vi.mock('./api', () => ({
  getSharedInstruments: vi.fn(),
  searchPlatformEquityCatalog: vi.fn(),
}))

function instrument(
  instrumentId: string,
  instrumentType: SharedInstrumentRecord['instrument_type'],
): SharedInstrumentRecord {
  return {
    instrument_id: instrumentId,
    instrument_name: instrumentId,
    instrument_type: instrumentType,
    currency: 'USD',
    identifiers: [],
  }
}

describe('Watchlist instrument search', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(getSharedInstruments).mockImplementation(async (options) => {
      const instrumentType = options?.instrument_type as SharedInstrumentRecord['instrument_type']
      return [instrument(`registry-${instrumentType}`, instrumentType)]
    })
  })

  it('keeps Registry results available when the stock catalog fails', async () => {
    vi.mocked(searchPlatformEquityCatalog).mockRejectedValue(
      new Error('Stock catalog unavailable'),
    )

    const response = await searchWatchlistInstrumentCandidates('alpha')

    expect(response.results).toHaveLength(5)
    expect(response.results.map((item) => item.instrument_type)).toEqual([
      'public_fund',
      'private_fund',
      'etf',
      'equity',
      'index',
    ])
    expect(response.stockCatalogError).toBe('Stock catalog unavailable')
  })

  it('deduplicates a catalog stock that already has a Registry identity', async () => {
    const registryEquity = instrument('equity-aapl', 'equity')
    vi.mocked(getSharedInstruments).mockImplementation(async (options) =>
      options?.instrument_type === 'equity' ? [registryEquity] : [],
    )
    vi.mocked(searchPlatformEquityCatalog).mockResolvedValue([
      {
        ...registryEquity,
        fmp_symbol: 'AAPL',
        source: 'fmp_catalog',
        existing_instrument_id: 'equity-aapl',
      },
    ])

    const response = await searchWatchlistInstrumentCandidates('AAPL')

    expect(response.results).toEqual([registryEquity])
    expect(response.stockCatalogError).toBeNull()
  })
})
