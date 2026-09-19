import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getSharedInstruments, searchSecurities, type SecuritySearchResult } from './api'
import { searchWatchlistInstrumentCandidates } from './watchlistInstrumentSearch'

vi.mock('./api', () => ({ getSharedInstruments: vi.fn(), searchSecurities: vi.fn() }))

const security: SecuritySearchResult = {
  instrument_type: 'etf', symbol: 'MAGS', catalog_provider: 'fmp', catalog_symbol: 'MAGS',
  name: 'Roundhill Magnificent Seven ETF', exchange_code: 'XNYS', exchange_label: 'NYSE',
  market: 'US', currency: 'USD', currency_verified: true, existing_instrument_id: null,
}

describe('registered asset search', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(searchSecurities).mockResolvedValue({ results: [], catalog_errors: {} })
  })

  it('searches every supported registered asset type including native crypto', async () => {
    vi.mocked(getSharedInstruments).mockImplementation(async (options) => [{
      instrument_id: options!.instrument_type!, instrument_name: 'Registered asset',
      instrument_type: options!.instrument_type!, currency: 'USD', identifiers: [],
    }])
    const { results } = await searchWatchlistInstrumentCandidates('alpha')
    expect(results.map((item) => item.instrument_type)).toEqual([
      'public_fund', 'private_fund', 'etf', 'equity', 'index', 'crypto',
    ])
  })

  it('finds an unregistered directory asset without registering it', async () => {
    vi.mocked(getSharedInstruments).mockResolvedValue([])
    vi.mocked(searchSecurities).mockResolvedValue({ results: [security], catalog_errors: {} })
    expect(await searchWatchlistInstrumentCandidates('MAGS')).toEqual({ results: [], catalogResults: [security], catalogErrors: [] })
    expect(searchSecurities).toHaveBeenCalledWith('MAGS', 12)
  })

  it('deduplicates directory matches that are already registered', async () => {
    const registered = { instrument_id: 'mags', instrument_name: security.name, instrument_type: 'etf', currency: 'USD', identifiers: [] }
    vi.mocked(getSharedInstruments).mockResolvedValue([registered])
    vi.mocked(searchSecurities).mockResolvedValue({ results: [{ ...security, existing_instrument_id: 'mags' }], catalog_errors: {} })
    expect(await searchWatchlistInstrumentCandidates('MAGS')).toEqual({ results: [registered], catalogResults: [], catalogErrors: [] })
  })

  it('preserves registered results and reports catalog failures', async () => {
    vi.mocked(getSharedInstruments).mockResolvedValue([])
    vi.mocked(searchSecurities).mockRejectedValue(new Error('Directory unavailable'))
    expect(await searchWatchlistInstrumentCandidates('MAGS')).toEqual({ results: [], catalogResults: [], catalogErrors: ['Directory unavailable'] })
  })

  it('does not query the market directory before the user enters a search', async () => {
    vi.mocked(getSharedInstruments).mockResolvedValue([])
    await searchWatchlistInstrumentCandidates(' ')
    expect(searchSecurities).not.toHaveBeenCalled()
  })
})
