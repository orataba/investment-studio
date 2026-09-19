import { beforeEach, describe, expect, it, vi } from 'vitest'
import { searchSharedInstruments, searchSecurities, type SecuritySearchResult } from './api'
import { searchWatchlistInstrumentCandidates } from './watchlistInstrumentSearch'

vi.mock('./api', () => ({ searchSharedInstruments: vi.fn(), searchSecurities: vi.fn() }))

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

  it('uses a single lightweight registry search for all supported asset types', async () => {
    const records = ['public_fund', 'private_fund', 'etf', 'equity', 'index', 'crypto'].map(type => ({
      instrument_id: type, instrument_name: 'Registered asset', instrument_type: type, currency: 'USD', identifiers: [],
    }))
    vi.mocked(searchSharedInstruments).mockResolvedValue(records)
    const { results } = await searchWatchlistInstrumentCandidates('alpha')
    expect(results).toEqual(records)
    expect(searchSharedInstruments).toHaveBeenCalledExactlyOnceWith('alpha', 12)
  })

  it('finds an unregistered directory asset without registering it', async () => {
    vi.mocked(searchSharedInstruments).mockResolvedValue([])
    vi.mocked(searchSecurities).mockResolvedValue({ results: [security], catalog_errors: {} })
    expect(await searchWatchlistInstrumentCandidates('MAGS')).toEqual({ results: [], catalogResults: [security], catalogErrors: [] })
    expect(searchSecurities).toHaveBeenCalledWith('MAGS', 12)
  })

  it('deduplicates directory matches that are already registered', async () => {
    const registered = { instrument_id: 'mags', instrument_name: security.name, instrument_type: 'etf', currency: 'USD', identifiers: [] }
    vi.mocked(searchSharedInstruments).mockResolvedValue([registered])
    vi.mocked(searchSecurities).mockResolvedValue({ results: [{ ...security, existing_instrument_id: 'mags' }], catalog_errors: {} })
    expect(await searchWatchlistInstrumentCandidates('MAGS')).toEqual({ results: [registered], catalogResults: [], catalogErrors: [] })
  })

  it('preserves registered results and reports catalog failures', async () => {
    vi.mocked(searchSharedInstruments).mockResolvedValue([])
    vi.mocked(searchSecurities).mockRejectedValue(new Error('Directory unavailable'))
    expect(await searchWatchlistInstrumentCandidates('MAGS')).toEqual({ results: [], catalogResults: [], catalogErrors: ['Directory unavailable'] })
  })

  it('does not query the market directory before the user enters a search', async () => {
    vi.mocked(searchSharedInstruments).mockResolvedValue([])
    await searchWatchlistInstrumentCandidates(' ')
    expect(searchSecurities).not.toHaveBeenCalled()
  })
})
