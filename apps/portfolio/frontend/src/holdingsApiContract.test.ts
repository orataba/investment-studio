import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  getHoldingsWorkspace,
  getPortfolioInstrumentHoldingProjection,
} from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

function holdingsPayload() {
  return {
    portfolio_id: 'portfolio-lightweight',
    portfolio_name: 'Portfolio',
    base_currency: 'USD',
    as_of_date: '2026-07-14',
    view_label: 'View: Holdings',
    coverage_note: 'Published calculation.',
    quality_warnings: [],
    publication: { publication_id: 'publication-1' },
    sealed_display_config: {
      taxonomy: {
        default_planning_taxonomy_id: null,
        taxonomies: [],
        taxonomy_nodes: [],
        taxonomy_assignments: [],
      },
    },
    summary_cards: [],
    rows: [],
    totals: {
      market_value: '0',
      cash_balance: '12.5',
      pending_settlement: '0',
      nav: '12.5',
      day_change_pct: null,
      day_change_value: null,
      cost_basis: '0',
      unrealized_pnl_base: '0',
      unrealized_return: null,
      allocation: '0',
    },
  }
}

describe('holdings workspace request contract', () => {
  it('keeps the default holdings payload lightweight', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(holdingsPayload()), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    const response = await getHoldingsWorkspace('portfolio-lightweight')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/holdings?portfolio_id=portfolio-lightweight',
      expect.any(Object),
    )
    expect(response.totals.nav).toBe(12.5)
    expect(response.totals.exact_values?.nav).toBe('12.5')
  })

  it('rejects a JSON number for current published NAV', async () => {
    const payload = holdingsPayload()
    payload.totals.nav = 12.5 as unknown as string
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })),
      ),
    )

    await expect(getHoldingsWorkspace('portfolio-invalid-nav')).rejects.toThrow(
      /holdings\.totals\.nav is not a canonical exact decimal string/,
    )
  })

  it('does not expose the removed live return-series expansion flag', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(holdingsPayload()), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getHoldingsWorkspace('portfolio-risk')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/holdings?portfolio_id=portfolio-risk',
      expect.any(Object),
    )
  })

  it('loads instrument detail through the single-row projection endpoint', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            portfolio_id: 'portfolio-fast',
            portfolio_name: 'Portfolio',
            base_currency: 'USD',
            as_of_date: '2026-07-10',
            view_label: 'View: Holdings',
            quality_warnings: [],
            row: null,
          }),
          { status: 200 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getPortfolioInstrumentHoldingProjection('portfolio-fast', '159516-sz', {
      as_of_date: '2026-07-10',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/holdings/instrument?portfolio_id=portfolio-fast&instrument_id=159516-sz&as_of_date=2026-07-10',
      expect.any(Object),
    )
  })
})
