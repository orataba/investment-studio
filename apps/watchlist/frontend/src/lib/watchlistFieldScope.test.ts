import { describe, expect, it } from 'vitest'

import { fieldSupportsAllInstrumentTypes } from './watchlistFieldScope'

describe('fieldSupportsAllInstrumentTypes', () => {
  it('keeps unscoped fields for every watchlist', () => {
    expect(fieldSupportsAllInstrumentTypes({ instrument_scope_json: [] }, [])).toBe(true)
  })

  it('requires a scoped field to support every instrument type in a mixed watchlist', () => {
    const fundField = { instrument_scope_json: ['public_fund', 'private_fund'] }

    expect(fieldSupportsAllInstrumentTypes(fundField, ['public_fund', 'private_fund'])).toBe(true)
    expect(fieldSupportsAllInstrumentTypes(fundField, ['public_fund', 'etf'])).toBe(false)
  })

  it('does not expose asset-specific fields before an empty watchlist has an asset type', () => {
    expect(
      fieldSupportsAllInstrumentTypes({ instrument_scope_json: ['equity'] }, []),
    ).toBe(false)
  })
})
