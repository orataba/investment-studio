import { afterEach, describe, expect, it, vi } from 'vitest'

import { getInstrumentNavSeries, updateInstrumentSettings } from './api'

describe('instrument NAV series API contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fails closed when the canonical selected value is null', async () => {
    const payload = {
      instrument_id: 'fund-a',
      count: 1,
      nav_basis_preference: 'auto',
      nav_basis_type: 'nav_with_dividend',
      nav_basis_source: 'quote_policy',
      nav_basis_status: 'selected',
      selected_role: 'total_return',
      selected_metric_family: 'nav',
      selected_quote_basis: 'total_return',
      selected_series_type: 'nav_with_dividend',
      selected_series_label: 'NAV with dividend',
      selected_date_label: 'NAV date',
      calculation_frequency_profile: {
        requested_frequency: 'daily',
        resolved_frequency: 'daily',
        inferred_frequency: 'daily',
        source_frequency_counts: { daily: 1, unknown: 0 },
        raw_observation_count: 1,
        observation_count: 1,
        start_date: '2026-07-15',
        end_date: '2026-07-15',
        annualization_periods_per_year: null,
        largest_gap_days: null,
        gap_count: 0,
        gap_status: 'aligned',
        status_label: 'Daily',
      },
      compare_settings: {
        default_benchmark_instrument_id: null,
        peer_instrument_ids: [],
      },
      rows: [
        {
          date: '2026-07-15',
          nav: 1.01,
          nav_with_dividend: 1.23,
          selected_basis_type: 'nav_with_dividend',
          selected_value: null,
          calculation_included: true,
          currency: 'CNY',
          frequency: 'daily',
          adopted_at: null,
        },
      ],
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    })
    vi.stubGlobal('fetch', fetchMock)

    const response = await getInstrumentNavSeries('fund-a')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/instruments/fund-a/nav-series',
      expect.objectContaining({ headers: { 'Content-Type': 'application/json' } }),
    )
    expect(response.rows[0].selected_value).toBeNull()
    expect(response.series).toEqual([])
    expect(response.calculation_series).toEqual([])
  })
})

describe('instrument settings API contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('saves taxonomy and investment status in one request', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        instrument_id: 'equity/a',
        definitions: [],
        values: { coverage_status: 'Invested' },
        taxonomy: {
          taxonomy_code: 'instrument_taxonomy',
          assigned_node_id: 'equity-sector-information-technology',
          assigned_label: '信息技术',
          path_labels: ['股票', '信息技术'],
          path_node_ids: ['equity', 'equity-sector-information-technology'],
          depth: 2,
          derived_values: {},
        },
        updated: true,
        taxonomy_updated: true,
        status_updated: true,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await updateInstrumentSettings('equity/a', {
      taxonomy_node_id: 'equity-sector-information-technology',
      coverage_status: 'Invested',
      updated_by: 'terminal_ui',
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/instrument-attributes/instruments/equity%2Fa/settings',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          taxonomy_node_id: 'equity-sector-information-technology',
          coverage_status: 'Invested',
          updated_by: 'terminal_ui',
        }),
      }),
    )
  })
})
