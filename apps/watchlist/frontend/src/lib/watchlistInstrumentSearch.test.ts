import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getSharedInstruments,
  searchPlatformSecurityCatalog,
  type SharedInstrumentRecord,
} from './api'
import { searchWatchlistInstrumentCandidates } from './watchlistInstrumentSearch'

vi.mock('./api', () => ({
  getSharedInstruments: vi.fn(),
  searchPlatformSecurityCatalog: vi.fn(),
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

  it('keeps Registry results available when the security catalogs fail', async () => {
    vi.mocked(searchPlatformSecurityCatalog).mockRejectedValue(
      new Error('Security catalogs unavailable'),
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
    expect(response.securityCatalogError).toBe('Security catalogs unavailable')
  })

  it('deduplicates a catalog stock that already has a Registry identity', async () => {
    const registryEquity = instrument('equity-aapl', 'equity')
    vi.mocked(getSharedInstruments).mockImplementation(async (options) =>
      options?.instrument_type === 'equity' ? [registryEquity] : [],
    )
    vi.mocked(searchPlatformSecurityCatalog).mockResolvedValue({
      results: [
        {
          ...registryEquity,
          catalog_provider: 'fmp',
          catalog_symbol: 'AAPL',
          source: 'security_catalog',
          existing_instrument_id: 'equity-aapl',
        },
      ],
      catalogErrors: {},
    })

    const response = await searchWatchlistInstrumentCandidates('AAPL')

    expect(response.results).toEqual([registryEquity])
    expect(response.securityCatalogError).toBeNull()
  })

  it('returns a new ETF from the local FMP catalog', async () => {
    vi.mocked(getSharedInstruments).mockResolvedValue([])
    vi.mocked(searchPlatformSecurityCatalog).mockResolvedValue({
      results: [
        {
          ...instrument('fmp:etf:MAGS', 'etf'),
          catalog_provider: 'fmp',
          catalog_symbol: 'MAGS',
          source: 'security_catalog',
          existing_instrument_id: null,
        },
      ],
      catalogErrors: {},
    })

    const response = await searchWatchlistInstrumentCandidates('MAGS')

    expect(response.results.map((item) => item.instrument_type)).toEqual(['etf'])
    expect(response.securityCatalogError).toBeNull()
  })
})
