import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  getHoldingsWorkspace,
  getPortfolioPositionHoldingProjection,
} from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('holdings workspace request contract', () => {
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
