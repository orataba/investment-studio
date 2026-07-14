import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  archivePortfolio,
  clearPortfolioApiCache,
  getHoldingsWorkspace,
  getPortfolios,
  getWorkspaceSummaryForPortfolio,
  restorePortfolio,
} from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

function publication() {
  return {
    publication_id: 'publication-1',
    run_id: 'run-1',
    manifest_id: 'manifest-1',
    methodology_version: 'portfolio-daily/v1',
    published_at: '2026-07-14T00:00:00Z',
    captured_generation: 3,
    current_generation: 4,
    stale: true,
    pending: true,
    pending_generation: 4,
  }
}

describe('published workspace exact-decimal wire contract', () => {
  it('parses the taxonomy display config sealed with the holdings publication', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              portfolio_id: 'portfolio-1',
              portfolio_name: 'Sealed Portfolio',
              base_currency: 'CNY',
              as_of_date: '2026-07-14',
              view_label: 'View: Holdings',
              coverage_note: 'Sealed publication',
              quality_warnings: [],
              publication: publication(),
              sealed_display_config: {
                taxonomy: {
                  default_planning_taxonomy_id: 'taxonomy-1',
                  taxonomies: [
                    {
                      taxonomy_id: 'taxonomy-1',
                      portfolio_id: 'portfolio-1',
                      name: 'Risk Sleeves',
                      taxonomy_type: 'risk_sleeve',
                      purpose: null,
                      primary_assignment_scope: 'instrument',
                      planning_enabled: true,
                      budgeting_level: 'leaf',
                      root_default_target_dimension: 'weight',
                      status: 'active',
                      source_template_ref: null,
                    },
                  ],
                  taxonomy_nodes: [
                    {
                      taxonomy_node_id: 'node-growth',
                      taxonomy_id: 'taxonomy-1',
                      parent_taxonomy_node_id: null,
                      node_name: 'Growth',
                      node_code: null,
                      sort_order: 1,
                      is_terminal: true,
                      default_target_dimension: 'weight',
                      status: 'active',
                    },
                  ],
                  taxonomy_assignments: [
                    {
                      assignment_id: 'assignment-1',
                      taxonomy_id: 'taxonomy-1',
                      target_scope: 'instrument',
                      target_entity_id: 'instrument-1',
                      taxonomy_node_id: 'node-growth',
                      status: 'active',
                    },
                  ],
                },
              },
              summary_cards: [],
              rows: [],
              totals: {
                market_value: '100',
                day_change_pct: '0.01',
                day_change_value: '1',
                cost_basis: '90',
                unrealized_pnl_base: '10',
                unrealized_return: null,
                allocation: '1',
              },
            }),
            { status: 200 },
          ),
        ),
      ),
    )

    const result = await getHoldingsWorkspace('portfolio-1')

    expect(result.publication.publication_id).toBe('publication-1')
    expect(result.sealed_display_config.taxonomy.default_planning_taxonomy_id).toBe('taxonomy-1')
    expect(result.sealed_display_config.taxonomy.taxonomy_nodes[0]?.node_name).toBe('Growth')
    expect(result.sealed_display_config.taxonomy.taxonomy_assignments[0]?.target_entity_id).toBe(
      'instrument-1',
    )
  })

  it('preserves exact strings and converts only the display projection', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              portfolio_id: 'portfolio-1',
              portfolio_name: 'Exact Portfolio',
              base_currency: 'CNY',
              operating_profile: 'standard_taxonomy',
              as_of_date: '2026-07-14',
              nav: '100000000000000000000.00000001',
              economic_pnl: '0.1',
              subperiod_twr_method50: '0.0000000000000000010000000000000000001',
              subperiod_twr_published: '0.000000000000000001',
              nav_coverage_state: 'complete',
              return_coverage_state: 'complete',
              valuation_endpoint_status: 'fresh',
              toolbar_label: 'View: Portfolio Summary',
              badges: [],
              sections: [],
              publication: publication(),
            }),
            { status: 200 },
          ),
        ),
      ),
    )

    const result = await getWorkspaceSummaryForPortfolio('portfolio-1')

    expect(result.nav_exact).toBe('100000000000000000000.00000001')
    expect(result.day_change_value_exact).toBe('0.1')
    expect(result.day_change_pct_method50).toBe(
      '0.0000000000000000010000000000000000001',
    )
    expect(result.day_change_pct_published).toBe('0.000000000000000001')
    expect(result.nav).toBe(100000000000000000000)
    expect(result.badges).toEqual(['Published calculation is stale', 'Recalculation pending'])
  })

  it('rejects JSON numbers at the exact financial contract boundary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              portfolio_id: 'portfolio-1',
              portfolio_name: 'Invalid Portfolio',
              base_currency: 'CNY',
              as_of_date: '2026-07-14',
              nav: 0.1,
              economic_pnl: null,
              subperiod_twr_method50: null,
              subperiod_twr_published: null,
              toolbar_label: 'View: Portfolio Summary',
              sections: [],
            }),
            { status: 200 },
          ),
        ),
      ),
    )

    await expect(getWorkspaceSummaryForPortfolio('portfolio-1')).rejects.toThrow(
      /canonical exact decimal string/,
    )
  })

  it('evicts an invalid decoded GET and recovers from the corrected wire response', async () => {
    const portfolio = (nav: number | string) => ({
      portfolio_id: 'portfolio-1',
      portfolio_name: 'Recoverable Portfolio',
      base_currency: 'CNY',
      operating_profile: 'standard_taxonomy',
      sort_order: 0,
      lifecycle_status: 'active',
      default_planning_taxonomy_id: null,
      calculation_status: 'published',
      as_of_date: '2026-07-14',
      nav,
      economic_pnl: '0.1',
      subperiod_twr_method50: '0.01',
      subperiod_twr_published: '0.01',
      holding_count: 1,
      publication: publication(),
    })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([portfolio(100.1)]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([portfolio('100.1')]), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const [result] = await getPortfolios()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result.nav_exact).toBe('100.1')
    expect(result.nav).toBe(100.1)
  })

  it('represents a portfolio without a publication as unavailable, never zero', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify([
              {
                portfolio_id: 'portfolio-new',
                portfolio_name: 'New Portfolio',
                base_currency: 'USD',
                operating_profile: 'external_etf_rotation',
                sort_order: 0,
                lifecycle_status: 'active',
                default_planning_taxonomy_id: null,
                calculation_status: 'not_ready',
                as_of_date: null,
                nav: null,
                economic_pnl: null,
                subperiod_twr_method50: null,
                subperiod_twr_published: null,
                holding_count: 0,
                publication: null,
              },
            ]),
            { status: 200 },
          ),
        ),
      ),
    )

    const [result] = await getPortfolios()
    expect(result.nav).toBeNull()
    expect(result.nav_exact).toBeNull()
    expect(result.day_change_value).toBeNull()
    expect(result.calculation_status).toBe('not_ready')
    expect(result.operating_profile).toBe('external_etf_rotation')
  })

  it.each([
    ['archive', archivePortfolio, 'archived'],
    ['restore', restorePortfolio, 'active'],
  ] as const)('uses the explicit %s lifecycle command', async (command, request, status) => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            portfolio_id: 'portfolio-1',
            portfolio_name: 'Lifecycle Portfolio',
            base_currency: 'CNY',
            operating_profile: 'standard_taxonomy',
            sort_order: 0,
            lifecycle_status: status,
            calculation_status: 'not_ready',
            as_of_date: null,
            nav: null,
            economic_pnl: null,
            subperiod_twr_method50: null,
            subperiod_twr_published: null,
            holding_count: 0,
            publication: null,
          }),
          { status: 200 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await request('portfolio-1')

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/api/portfolios/portfolio-1/${command}`),
      expect.objectContaining({ method: 'POST' }),
    )
    expect(result.lifecycle_status).toBe(status)
  })
})
