import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  getPortfolioResearchRun,
  getPortfolioResearchWorkbench,
} from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('research compact/detail request contract', () => {
  it('keeps the default workbench compact', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getPortfolioResearchWorkbench('portfolio-compact')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio-compact/research/workbench',
      expect.any(Object),
    )
  })

  it('expands an explicitly selected run', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getPortfolioResearchWorkbench('portfolio-compact', 'run-1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio-compact/research/workbench?selected_run_id=run-1',
      expect.any(Object),
    )
  })

  it('loads one run detail from the existing detail endpoint', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getPortfolioResearchRun('portfolio-compact', 'run/with spaces')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio-compact/research/runs/run%2Fwith%20spaces',
      expect.any(Object),
    )
  })
})
