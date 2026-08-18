import {
  getSharedInstruments,
  searchPlatformEquityCatalog,
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
  stockCatalogError: string | null
}

export async function searchWatchlistInstrumentCandidates(
  query: string,
  limit = 12,
): Promise<WatchlistInstrumentSearchResult> {
  const registryRequest = Promise.all(
    REGISTRY_SEARCH_TYPES.map((instrumentType) =>
      getSharedInstruments({
        search: query,
        instrument_type: instrumentType,
        limit,
      }),
    ),
  )
  const stockCatalogRequest = searchPlatformEquityCatalog(query, limit)
    .then((results) => ({ results, error: null as string | null }))
    .catch((error: unknown) => ({
      results: [] as SharedInstrumentRecord[],
      error:
        error instanceof Error
          ? error.message
          : 'The local FMP stock catalog is unavailable.',
    }))

  const [registryGroups, stockCatalog] = await Promise.all([
    registryRequest,
    stockCatalogRequest,
  ])
  const seenInstrumentIds = new Set<string>()
  const results = [...registryGroups.flat(), ...stockCatalog.results].filter((item) => {
    if (seenInstrumentIds.has(item.instrument_id)) {
      return false
    }
    seenInstrumentIds.add(item.instrument_id)
    return true
  })

  return {
    results,
    stockCatalogError: stockCatalog.error,
  }
}
