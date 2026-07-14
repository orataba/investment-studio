import { describe, expect, it, vi } from 'vitest'

import type {
  HoldingsWorkspaceResponse,
  PortfolioDailyPublishedPerformanceReportResponse,
} from './lib/api'
import {
  loadOverviewPublishedBundle,
  OVERVIEW_PUBLICATION_CHANGED_CODE,
  OVERVIEW_PUBLICATION_MAX_ATTEMPTS,
} from './lib/overviewPublication'

function holdings(publicationId: string) {
  return {
    publication: { publication_id: publicationId },
  } as unknown as HoldingsWorkspaceResponse
}

function performance(publicationId: string) {
  return {
    publication: { publication_id: publicationId },
  } as unknown as PortfolioDailyPublishedPerformanceReportResponse
}

describe('Overview immutable publication loading', () => {
  it('commits holdings and performance from the same publication', async () => {
    const clearCache = vi.fn()
    const getHoldings = vi.fn(async () => holdings('publication-7'))
    const getPerformance = vi.fn(async () => performance('publication-7'))

    const result = await loadOverviewPublishedBundle('portfolio-1', {
      clearCache,
      getHoldings,
      getPerformance,
    })

    expect(result.publication_id).toBe('publication-7')
    expect(result.attempt_count).toBe(1)
    expect(result.holdings.publication.publication_id).toBe('publication-7')
    expect(result.performance.publication.publication_id).toBe('publication-7')
    expect(clearCache).toHaveBeenCalledTimes(1)
  })

  it('retries both projections when the first publication ids differ', async () => {
    const clearCache = vi.fn()
    const getHoldings = vi
      .fn()
      .mockResolvedValueOnce(holdings('publication-old'))
      .mockResolvedValueOnce(holdings('publication-new'))
    const getPerformance = vi
      .fn()
      .mockResolvedValueOnce(performance('publication-new'))
      .mockResolvedValueOnce(performance('publication-new'))

    const result = await loadOverviewPublishedBundle('portfolio-1', {
      clearCache,
      getHoldings,
      getPerformance,
    })

    expect(result.publication_id).toBe('publication-new')
    expect(result.attempt_count).toBe(2)
    expect(getHoldings).toHaveBeenCalledTimes(2)
    expect(getPerformance).toHaveBeenCalledTimes(2)
    expect(clearCache).toHaveBeenCalledTimes(2)
  })

  it('fails closed after the bounded retry budget instead of returning mixed data', async () => {
    const clearCache = vi.fn()
    const getHoldings = vi.fn(async () => holdings('publication-holdings'))
    const getPerformance = vi.fn(async () => performance('publication-performance'))

    await expect(
      loadOverviewPublishedBundle('portfolio-1', {
        clearCache,
        getHoldings,
        getPerformance,
      }),
    ).rejects.toThrow(OVERVIEW_PUBLICATION_CHANGED_CODE)
    expect(getHoldings).toHaveBeenCalledTimes(OVERVIEW_PUBLICATION_MAX_ATTEMPTS)
    expect(getPerformance).toHaveBeenCalledTimes(OVERVIEW_PUBLICATION_MAX_ATTEMPTS)
    expect(clearCache).toHaveBeenCalledTimes(OVERVIEW_PUBLICATION_MAX_ATTEMPTS)
  })
})
