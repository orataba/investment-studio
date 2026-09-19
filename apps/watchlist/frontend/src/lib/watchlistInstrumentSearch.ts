import {
  getSharedInstruments,
  searchSecurities,
  type SecuritySearchResult,
  type SharedInstrumentRecord,
} from './api'

const REGISTRY_SEARCH_TYPES = [
  'public_fund',
  'private_fund',
  'etf',
  'equity',
  'index',
  'crypto',
] as const

export type WatchlistInstrumentSearchResult = {
  results: SharedInstrumentRecord[]
  catalogResults: SecuritySearchResult[]
  catalogErrors: string[]
}

export async function searchWatchlistInstrumentCandidates(
  query: string,
  limit = 12,
): Promise<WatchlistInstrumentSearchResult> {
  const catalogRequest = query.trim()
    ? searchSecurities(query.trim(), limit).then(({ results, catalog_errors }) => ({
        results,
        errors: Object.values(catalog_errors),
      })).catch((error) => ({
        results: [],
        errors: [error instanceof Error ? error.message : 'Security directory is unavailable.'],
      }))
    : Promise.resolve({ results: [], errors: [] })
  const [registryGroups, catalog] = await Promise.all([
    Promise.all(
      REGISTRY_SEARCH_TYPES.map((instrumentType) =>
        getSharedInstruments({
          search: query,
          instrument_type: instrumentType,
          limit,
        }),
      ),
    ),
    catalogRequest,
  ])
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
    catalogResults: catalog.results.filter((item) =>
      !item.existing_instrument_id || !seenInstrumentIds.has(item.existing_instrument_id),
    ),
    catalogErrors: [...new Set(catalog.errors)],
  }
}
