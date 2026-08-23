import {
  getSharedInstruments,
  searchPlatformSecurityCatalog,
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
  securityCatalogError: string | null
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
  const securityCatalogRequest = searchPlatformSecurityCatalog(query, limit)
    .then(({ results, catalogErrors }) => ({
      results,
      error: Object.keys(catalogErrors).length
        ? `${Object.keys(catalogErrors).map((item) => item.toUpperCase()).join(' and ')} catalog unavailable.`
        : null,
    }))
    .catch((error: unknown) => ({
      results: [] as SharedInstrumentRecord[],
      error:
        error instanceof Error
          ? error.message
          : 'The local security catalogs are unavailable.',
    }))

  const [registryGroups, securityCatalog] = await Promise.all([
    registryRequest,
    securityCatalogRequest,
  ])
  const seenInstrumentIds = new Set<string>()
  const results = [...registryGroups.flat(), ...securityCatalog.results].filter((item) => {
    if (seenInstrumentIds.has(item.instrument_id)) {
      return false
    }
    seenInstrumentIds.add(item.instrument_id)
    return true
  })

  return {
    results,
    securityCatalogError: securityCatalog.error,
  }
}
