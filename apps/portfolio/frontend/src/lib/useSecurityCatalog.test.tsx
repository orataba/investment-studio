import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { searchPortfolioSecurities, type SecurityCatalogResponse } from './api'
import { useSecurityCatalog } from './useSecurityCatalog'

vi.mock('./api', () => ({ searchPortfolioSecurities: vi.fn() }))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

function catalog(symbol: string): SecurityCatalogResponse {
  return {
    results: [{
      instrument_type: 'etf', symbol, catalog_provider: 'fmp', catalog_symbol: symbol,
      name: `${symbol} Fund`, exchange_code: 'NASDAQ', exchange_label: 'NASDAQ',
      market: 'US', currency: 'USD', currency_verified: true, existing_instrument_id: null,
    }],
    catalog_errors: {},
  }
}

describe('useSecurityCatalog', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(searchPortfolioSecurities).mockReset()
  })

  afterEach(() => { vi.useRealTimers() })

  it('debounces typing and searches only the latest trimmed query', async () => {
    vi.mocked(searchPortfolioSecurities).mockResolvedValue(catalog('SHV'))
    const { result, rerender } = renderHook(
      ({ query }) => useSecurityCatalog('portfolio-1', query, true),
      { initialProps: { query: 'S' } },
    )

    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    rerender({ query: ' SHV ' })
    await act(async () => { await vi.advanceTimersByTimeAsync(249) })
    expect(searchPortfolioSecurities).not.toHaveBeenCalled()
    expect(result.current.loading).toBe(true)

    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(searchPortfolioSecurities).toHaveBeenCalledExactlyOnceWith('portfolio-1', 'SHV')
    expect(result.current.results).toEqual(catalog('SHV').results)
    expect(result.current.loading).toBe(false)
  })

  it('does not let a late response replace results for a newer query', async () => {
    const oldRequest = deferred<SecurityCatalogResponse>()
    const newRequest = deferred<SecurityCatalogResponse>()
    vi.mocked(searchPortfolioSecurities)
      .mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(newRequest.promise)
    const { result, rerender } = renderHook(
      ({ query }) => useSecurityCatalog('portfolio-1', query, true),
      { initialProps: { query: 'SHV' } },
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    rerender({ query: 'STIP' })
    expect(result.current.results).toEqual([])
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    await act(async () => { newRequest.resolve(catalog('STIP')) })
    await act(async () => { oldRequest.resolve(catalog('SHV')) })

    expect(result.current).toEqual({ results: catalog('STIP').results, loading: false, error: null })
  })

  it('ignores an in-flight response after the search is disabled', async () => {
    const request = deferred<SecurityCatalogResponse>()
    vi.mocked(searchPortfolioSecurities).mockReturnValue(request.promise)
    const { result, rerender } = renderHook(
      ({ enabled }) => useSecurityCatalog('portfolio-1', 'DBA', enabled),
      { initialProps: { enabled: true } },
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    rerender({ enabled: false })
    await act(async () => { request.resolve(catalog('DBA')) })

    expect(result.current).toEqual({ results: [], loading: false, error: null })
  })

  it('retains successful matches and exposes the actual partial catalog error', async () => {
    vi.mocked(searchPortfolioSecurities).mockResolvedValue({
      ...catalog('EMXC'), catalog_errors: { equity: 'Equity catalog is unavailable: source timeout.' },
    })
    const { result } = renderHook(() => useSecurityCatalog('portfolio-1', 'EMXC', true))
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })

    expect(result.current.results).toEqual(catalog('EMXC').results)
    expect(result.current.error).toBe('Equity catalog is unavailable: source timeout.')
    expect(result.current.loading).toBe(false)
  })

  it('exposes request failures so a service error is not shown as an empty successful search', async () => {
    vi.mocked(searchPortfolioSecurities).mockRejectedValue(new Error('Catalog service unavailable.'))
    const { result } = renderHook(() => useSecurityCatalog('portfolio-1', 'GCC', true))
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })

    expect(result.current).toEqual({ results: [], loading: false, error: 'Catalog service unavailable.' })
  })
})
