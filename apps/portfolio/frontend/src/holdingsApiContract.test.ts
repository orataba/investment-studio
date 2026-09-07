import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  getHoldingsWorkspace,
  getPortfolioPerformance,
  getPortfolioPositionHoldingProjection,
  getPortfolioTableViewStore,
  savePortfolioTableViewStore,
  updatePortfolioSettings,
} from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('holdings workspace request contract', () => {
  it('keeps computed reports cached after table preferences change but invalidates them after portfolio settings change', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getHoldingsWorkspace('portfolio-cache')
    await getPortfolioPerformance('portfolio-cache', { start_date: '2026-07-01', end_date: '2026-07-15' })
    await getPortfolioTableViewStore('portfolio-cache', 'holdings')
    await savePortfolioTableViewStore('portfolio-cache', 'holdings', { activeViewId: 'default' })
    expect(fetchMock).toHaveBeenCalledTimes(4)

    await getHoldingsWorkspace('portfolio-cache')
    await getPortfolioPerformance('portfolio-cache', { start_date: '2026-07-01', end_date: '2026-07-15' })
    expect(fetchMock).toHaveBeenCalledTimes(4)
    await getPortfolioTableViewStore('portfolio-cache', 'holdings')
    expect(fetchMock).toHaveBeenCalledTimes(5)

    await updatePortfolioSettings('portfolio-cache', { base_currency: 'CNY' })
    await getHoldingsWorkspace('portfolio-cache')
    await getPortfolioPerformance('portfolio-cache', { start_date: '2026-07-01', end_date: '2026-07-15' })
    expect(fetchMock).toHaveBeenCalledTimes(8)
  })

  it('keeps the default holdings payload lightweight', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getHoldingsWorkspace('portfolio-lightweight')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/holdings?portfolio_id=portfolio-lightweight',
      expect.any(Object),
    )
  })

  it('requests long return series only when a risk surface opts in', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getHoldingsWorkspace('portfolio-risk', { include_details: true })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/holdings?portfolio_id=portfolio-risk&include_details=true',
      expect.any(Object),
    )
  })

  it('expands holdings detail arrays only through an explicit opt-in', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getHoldingsWorkspace('portfolio-detailed', { include_details: true })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/holdings?portfolio_id=portfolio-detailed&include_details=true',
      expect.any(Object),
    )
  })

  it('loads instrument detail through the holding-kind projection endpoint', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getPortfolioPositionHoldingProjection('portfolio-fast', '159516-sz', {
      as_of_date: '2026-07-10',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/holdings/position?portfolio_id=portfolio-fast&position_reference_id=159516-sz&as_of_date=2026-07-10',
      expect.any(Object),
    )
  })
})
