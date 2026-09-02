// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import WatchlistsPage from './WatchlistsPage'
import type { WatchlistDetail } from '../lib/api'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, 'ResizeObserver', {
  configurable: true,
  value: ResizeObserverStub,
})

const apiMocks = vi.hoisted(() => ({
  getWatchlists: vi.fn(),
  getFieldRegistry: vi.fn(),
  getInstrumentTaxonomyTree: vi.fn(),
  getWatchlistDetail: vi.fn(),
  runScreenerQuery: vi.fn(),
  updateWatchlistView: vi.fn(),
}))

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  ...apiMocks,
}))

describe('WatchlistsPage loading', () => {
  it('does not refetch unchanged screener criteria after the result renders', async () => {
    apiMocks.getWatchlists.mockResolvedValue([
      {
        watchlist_id: 'all-private-funds',
        name: 'All Private Funds',
        description: null,
        item_count: 1,
        owner_type: 'system',
        owner_id: 'system',
        is_default: true,
        is_shared: true,
        default_view_id: 'overview',
      },
    ])
    apiMocks.getFieldRegistry.mockResolvedValue({
      categories: [],
      total_fields: 1,
      fields: [
        {
          field_key: 'instrument_name',
          label: 'Instrument',
          description: null,
          category_code: 'identity',
          data_type: 'string',
          formatter_code: 'text',
          sort_mode: 'text',
          filter_mode: 'none',
          group_mode: 'none',
          instrument_scope_json: [],
          product_scope_json: [],
          availability_rule_json: {},
          source_domain: 'registry',
          source_metric_code: 'instrument_name',
          default_width: 220,
          default_visible: true,
        },
      ],
    })
    apiMocks.getInstrumentTaxonomyTree.mockResolvedValue({
      taxonomy_code: 'instrument_taxonomy',
      instrument_types: ['private_fund'],
      max_depth: 0,
      nodes: [],
    })
    apiMocks.getWatchlistDetail.mockResolvedValue({
      watchlist_id: 'all-private-funds',
      name: 'All Private Funds',
      description: null,
      item_count: 1,
      owner_type: 'system',
      owner_id: 'system',
      is_default: true,
      is_shared: true,
      default_view_id: 'overview',
      instrument_types: ['private_fund'],
      views: [
        {
          view_id: 'overview',
          view_key: 'overview',
          name: 'Overview',
          description: null,
          kind: 'system',
          default_group_by: 'none',
          default_sort: [],
          default_filters: {},
          default_advanced_filters: null,
          columns: ['instrument_name'],
        },
      ],
      available_group_bys: [
        { code: 'none', label: 'None' },
        { code: 'currency', label: 'Currency' },
      ],
      default_filters_summary: {},
    })
    apiMocks.runScreenerQuery.mockResolvedValue({
      rows: [{ instrument_id: 'fund-1', instrument_name: 'Fund 1' }],
      groups: [],
      total_rows: 1,
      stale_row_count: 0,
      sparklines: {},
      snapshot_metadata: {
        as_of_date: '2026-08-26',
        as_of_date_min: '2026-08-26',
        as_of_date_max: '2026-08-26',
        has_mixed_as_of_dates: false,
        as_of_date_missing_count: 0,
        methodology_version: 'test',
        source_cutoff_at: null,
        is_current: true,
        advanced_filter_applied: false,
      },
    })

    render(
      <MemoryRouter initialEntries={['/watchlists/all-private-funds']}>
        <Routes>
          <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await waitFor(() => expect(apiMocks.runScreenerQuery).toHaveBeenCalled())
    await new Promise((resolve) => window.setTimeout(resolve, 50))

    const criteriaKeys = apiMocks.runScreenerQuery.mock.calls.map(([payload]) =>
      JSON.stringify(payload),
    )
    expect(new Set(criteriaKeys).size).toBe(criteriaKeys.length)
  })

  it('keeps instrument type as the fixed outer section for mixed custom watchlists', async () => {
    apiMocks.getWatchlists.mockResolvedValue([
      {
        watchlist_id: 'mixed-research',
        name: 'Mixed Research',
        description: null,
        item_count: 2,
        owner_type: 'team',
        owner_id: 'investment-team',
        is_default: false,
        is_shared: false,
        default_view_id: 'overview',
      },
    ])
    apiMocks.getFieldRegistry.mockResolvedValue({
      categories: [],
      total_fields: 1,
      fields: [
        {
          field_key: 'instrument_name',
          label: 'Instrument',
          description: null,
          category_code: 'general',
          data_type: 'string',
          formatter_code: 'text',
          sort_mode: 'alpha',
          filter_mode: 'text',
          group_mode: 'none',
          instrument_scope_json: [],
          product_scope_json: [],
          availability_rule_json: {},
          source_domain: 'read_model',
          source_metric_code: 'watchlist_row_read_model.instrument_name',
          default_width: 220,
          default_visible: true,
        },
      ],
    })
    apiMocks.getInstrumentTaxonomyTree.mockResolvedValue({
      taxonomy_code: 'instrument_taxonomy',
      instrument_types: ['public_fund', 'equity'],
      max_depth: 0,
      nodes: [],
    })
    apiMocks.getWatchlistDetail.mockResolvedValue({
      watchlist_id: 'mixed-research',
      name: 'Mixed Research',
      description: null,
      item_count: 2,
      owner_type: 'team',
      owner_id: 'investment-team',
      is_default: false,
      is_shared: false,
      default_view_id: 'overview',
      instrument_types: ['equity', 'public_fund'],
      views: [
        {
          view_id: 'overview',
          view_key: 'overview',
          name: 'Overview',
          description: null,
          kind: 'system',
          default_group_by: 'none',
          default_sort: [],
          default_filters: {},
          default_advanced_filters: null,
          columns: ['instrument_name'],
        },
      ],
      available_group_bys: [
        { code: 'none', label: 'None' },
        { code: 'taxonomy', label: 'Taxonomy' },
        { code: 'currency', label: 'Currency' },
        { code: 'attr.coverage_status', label: 'Investment Status' },
        { code: 'attr.manual_rating', label: 'Research Rating' },
      ],
      default_filters_summary: {},
    })
    apiMocks.runScreenerQuery.mockResolvedValue({
      rows: [
        { instrument_id: 'fund-1', instrument_type: 'public_fund', instrument_name: 'Fund 1' },
        { instrument_id: 'equity-1', instrument_type: 'equity', instrument_name: 'Equity 1' },
      ],
      groups: [],
      total_rows: 2,
      stale_row_count: 0,
      sparklines: {},
      snapshot_metadata: {
        as_of_date: null,
        as_of_date_min: null,
        as_of_date_max: null,
        has_mixed_as_of_dates: false,
        as_of_date_missing_count: 2,
        methodology_version: 'test',
        source_cutoff_at: null,
        is_current: true,
        advanced_filter_applied: false,
      },
    })

    render(
      <MemoryRouter initialEntries={['/watchlists/mixed-research']}>
        <Routes>
          <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await waitFor(() => expect(screen.getByText('Public Fund')).toBeTruthy())
    expect(screen.getByText('Equity')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Group By/ }))
    expect(screen.getByRole('button', { name: 'None' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Taxonomy' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Currency' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Investment Status' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Research Rating' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Instrument Type' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Freshness' })).toBeNull()
  })

  it('updates the active Overview view directly from the Columns dialog', async () => {
    apiMocks.getWatchlists.mockResolvedValue([
      {
        watchlist_id: 'coverage',
        name: 'Coverage',
        description: null,
        item_count: 1,
        owner_type: 'team',
        owner_id: 'investment-team',
        is_default: true,
        is_shared: false,
        default_view_id: 'overview',
      },
    ])
    apiMocks.getFieldRegistry.mockResolvedValue({
      categories: [
        {
          category_code: 'identity',
          label: 'Identity',
          description: null,
          display_order: 0,
          parent_category_code: null,
        },
      ],
      total_fields: 2,
      fields: [
        {
          field_key: 'instrument_name',
          label: 'Instrument',
          description: null,
          category_code: 'identity',
          data_type: 'string',
          formatter_code: 'text',
          sort_mode: 'text',
          filter_mode: 'none',
          group_mode: 'none',
          instrument_scope_json: [],
          product_scope_json: [],
          availability_rule_json: {},
          source_domain: 'registry',
          source_metric_code: 'instrument_name',
          default_width: 220,
          default_visible: true,
        },
        {
          field_key: 'currency',
          label: 'Currency',
          description: null,
          category_code: 'identity',
          data_type: 'string',
          formatter_code: 'text',
          sort_mode: 'text',
          filter_mode: 'none',
          group_mode: 'exact',
          instrument_scope_json: [],
          product_scope_json: [],
          availability_rule_json: {},
          source_domain: 'registry',
          source_metric_code: 'currency',
          default_width: 100,
          default_visible: true,
        },
      ],
    })
    apiMocks.getInstrumentTaxonomyTree.mockResolvedValue({
      taxonomy_code: 'instrument_taxonomy',
      instrument_types: ['equity'],
      max_depth: 0,
      nodes: [],
    })
    let detail: WatchlistDetail = {
      watchlist_id: 'coverage',
      name: 'Coverage',
      description: null,
      item_count: 1,
      owner_type: 'team',
      owner_id: 'investment-team',
      is_default: true,
      is_shared: false,
      default_view_id: 'overview',
      instrument_types: ['equity'],
      views: [
        {
          view_id: 'overview',
          view_key: 'overview',
          name: 'Overview',
          description: null,
          kind: 'system',
          default_group_by: 'none',
          default_sort: [],
          default_filters: {},
          default_advanced_filters: null,
          columns: ['instrument_name', 'currency'],
        },
      ],
      available_group_bys: [
        { code: 'none', label: 'None' },
        { code: 'currency', label: 'Currency' },
      ],
      default_filters_summary: {},
    }
    apiMocks.getWatchlistDetail.mockImplementation(async () => detail)
    apiMocks.updateWatchlistView.mockImplementation(
      async (
        _watchlistId: string,
        _viewId: string,
        payload: {
          columns: Array<{ field_key: string; display_order: number }>
          default_group_by: string
          default_sort: Array<{ field: string; direction: string }>
          default_filters: Record<string, unknown>
        },
      ) => {
        const updated = {
          ...detail.views[0],
          columns: payload.columns.map((column) => column.field_key),
          column_meta: payload.columns,
          default_group_by: payload.default_group_by,
          default_sort: payload.default_sort,
          default_filters: payload.default_filters,
        }
        detail = { ...detail, views: [updated] }
        return updated
      },
    )
    apiMocks.runScreenerQuery.mockResolvedValue({
      rows: [{ instrument_id: 'equity-1', instrument_type: 'equity', instrument_name: 'Equity 1', currency: 'USD' }],
      groups: [],
      total_rows: 1,
      stale_row_count: 0,
      sparklines: {},
      snapshot_metadata: {
        as_of_date: '2026-09-02',
        as_of_date_min: '2026-09-02',
        as_of_date_max: '2026-09-02',
        has_mixed_as_of_dates: false,
        as_of_date_missing_count: 0,
        methodology_version: 'test',
        source_cutoff_at: null,
        is_current: true,
        advanced_filter_applied: false,
      },
    })

    render(
      <MemoryRouter initialEntries={['/watchlists/coverage']}>
        <Routes>
          <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Columns' }))
    const columnsDialog = screen.getByRole('dialog', { name: 'Choose columns' })
    const currencyField = within(columnsDialog).getByText('Currency').closest('label')!
    fireEvent.click(currencyField.querySelector('input')!)
    fireEvent.click(within(columnsDialog).getByRole('button', { name: 'Update' }))

    await waitFor(() => {
      expect(apiMocks.updateWatchlistView).toHaveBeenCalledWith(
        'coverage',
        'overview',
        expect.objectContaining({
          columns: [expect.objectContaining({ field_key: 'instrument_name' })],
        }),
      )
    })
    await waitFor(() => {
      const viewSelect = document.querySelector('.watchlists-inline-select-group') as HTMLSelectElement
      expect(viewSelect.selectedOptions[0]?.textContent).toBe('View\u00A0: Overview')
      expect(screen.getByRole('button', { name: 'Create view' })).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Update View' })).toBeNull()
    })

    apiMocks.updateWatchlistView.mockClear()
    fireEvent.click(screen.getByRole('button', { name: /Group By\s*: None/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Currency' }))
    expect(screen.queryByRole('button', { name: 'Update View' })).toBeNull()
    await waitFor(() => {
      expect(apiMocks.updateWatchlistView).toHaveBeenCalledWith(
        'coverage',
        'overview',
        expect.objectContaining({ default_group_by: 'currency' }),
      )
    })
  })
})
