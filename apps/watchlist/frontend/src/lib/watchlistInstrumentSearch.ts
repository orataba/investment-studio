import {
  getSharedInstruments,
  type SharedInstrumentRecord,
} from './api'

const REGISTRY_SEARCH_TYPES = [
  'public_fund',
  'private_fund',
  'etf',
  'equity',
  'index',
] as const

export type WatchlistInstrumentSearchResult = {
  results: SharedInstrumentRecord[]
}

export async function searchWatchlistInstrumentCandidates(
  query: string,
  limit = 12,
): Promise<WatchlistInstrumentSearchResult> {
  const registryGroups = await Promise.all(
    REGISTRY_SEARCH_TYPES.map((instrumentType) =>
      getSharedInstruments({
        search: query,
        instrument_type: instrumentType,
        limit,
      }),
    ),
  )
  const seenInstrumentIds = new Set<string>()
  const results = registryGroups.flat().filter((item) => {
    if (seenInstrumentIds.has(item.instrument_id)) {
      return false
    }
    seenInstrumentIds.add(item.instrument_id)
    return true
  })

  return {
    results,
  }
}
