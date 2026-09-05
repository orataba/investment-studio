import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getSharedInstruments } from './api'
import { searchWatchlistInstrumentCandidates } from './watchlistInstrumentSearch'

vi.mock('./api', () => ({ getSharedInstruments: vi.fn() }))

describe('registered asset search', () => {
  beforeEach(() => vi.resetAllMocks())

  it('searches the five supported registered asset types', async () => {
    vi.mocked(getSharedInstruments).mockImplementation(async (options) => [{
      instrument_id: options!.instrument_type!, instrument_name: 'Registered asset',
      instrument_type: options!.instrument_type!, currency: 'USD', identifiers: [],
    }])
    const { results } = await searchWatchlistInstrumentCandidates('alpha')
    expect(results.map((item) => item.instrument_type)).toEqual([
      'public_fund', 'private_fund', 'etf', 'equity', 'index',
    ])
  })

  it('does not create an unregistered asset on a search miss', async () => {
    vi.mocked(getSharedInstruments).mockResolvedValue([])
    expect(await searchWatchlistInstrumentCandidates('MAGS')).toEqual({ results: [] })
  })
})
