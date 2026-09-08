// @vitest-environment jsdom

import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
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
  fetchJson: vi.fn(),
}))

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  ...apiMocks,
}))

describe('WatchlistsPage loading', () => {
  it('does not refetch unchanged screener criteria after the result renders', async () => {
    apiMocks.fetchJson.mockImplementation(async (path: string) => path.startsWith('/api/risk/review?')
      ? { available: true, scope: { kind: 'watchlist', id: 'all-private-funds', name: 'All Private Funds' },
        input_as_of: null, counts: { research: 0, quantitative: 0, coverage: 0 }, instruments: [],
        limitations: [], latest_completed: null, latest_run: null }
      : { instruments: [], cases: [], available: true, sectors: [], events: [] })
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
      total_fields: 2,
      column_field_keys: ['instrument_name', 'attr.risk_attention'],
      filter_field_keys: ['attr.risk_attention'],
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
          field_key: 'attr.risk_attention', label: '风险关注', category_code: 'research',
          data_type: 'string', formatter_code: 'text', sort_mode: 'text',
          filter_mode: 'multi_select', group_mode: 'none', instrument_scope_json: [],
          product_scope_json: [], availability_rule_json: {}, source_domain: 'research',
          source_metric_code: 'risk_attention', default_width: 120, default_visible: false,
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
      rows: [{ instrument_id: 'fund-1', instrument_name: 'Fund 1', 'attr.risk_attention': 'attention' }],
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
      <LanguageProvider enableDomTranslation={false}>
        <MemoryRouter initialEntries={['/watchlists/all-private-funds']}>
          <Routes>
            <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>,
    )

    await waitFor(() => expect(apiMocks.runScreenerQuery).toHaveBeenCalled())
    await new Promise((resolve) => window.setTimeout(resolve, 50))

    const criteriaKeys = apiMocks.runScreenerQuery.mock.calls.map(([payload]) =>
      JSON.stringify(payload),
    )
    expect(new Set(criteriaKeys).size).toBe(criteriaKeys.length)
    expect(apiMocks.runScreenerQuery.mock.calls[0][0].selected_fields).toContain('attr.risk_attention')
    expect(screen.getByRole('button', { name: 'Fund 1 有关注事项，查看风险提示' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '风险关注：升序' })).toBeNull()
    const title = screen.getByRole('heading', { name: 'All Private Funds', level: 1 })
    const tools = screen.getByRole('group', { name: 'Current workspace tools' })
    expect(title.parentElement?.contains(tools)).toBe(true)
    expect(tools.closest('.watchlists-topbar')).toBeNull()
    expect(document.querySelector('.watchlists-topbar > .language-switcher')).toBeTruthy()
    expect(within(tools).getAllByRole('button').map((button) => button.textContent)).toEqual(['Settings', 'Risk alerts', 'Research assistant'])
    fireEvent.click(within(tools).getByRole('button', { name: 'Watchlist settings' }))
    const settings = screen.getByRole('group', { name: 'Watchlist settings menu' })
    expect(within(settings).getByRole('button', { name: 'Column settings' })).toBeTruthy()
    expect(within(settings).getByRole('button', { name: 'Copy Watchlist' })).toBeTruthy()
    expect(within(settings).queryByRole('button', { name: 'Delete Watchlist' })).toBeNull()
    const language = screen.getByRole('combobox', { name: 'Language' })
    fireEvent.change(language, { target: { value: 'zh-Hans' } })
    expect(within(tools).getByRole('button', { name: '列表设置' }).getAttribute('title')).toBe('列表设置')
    expect(screen.getByRole('group', { name: '列表设置菜单' })).toBeTruthy()
    expect(within(settings).getByRole('button', { name: '列设置' })).toBeTruthy()
    fireEvent.change(language, { target: { value: 'en' } })
    expect(within(tools).getByRole('button', { name: 'Watchlist settings' }).getAttribute('title')).toBe('Watchlist settings')
    expect(screen.queryByRole('button', { name: 'Watchlist actions' })).toBeNull()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Fund 1' }))
    expect(within(tools).getByRole('button', { name: 'Research assistant' }).textContent).toBe('Research assistant(1)')
    fireEvent.click(within(tools).getByRole('button', { name: 'Risk alerts' }))
    expect(screen.queryByRole('group', { name: 'Watchlist settings menu' })).toBeNull()
    expect(await screen.findByRole('dialog', { name: '风险提示' })).toBeTruthy()
    await waitFor(() => expect(apiMocks.fetchJson).toHaveBeenCalledWith('/api/risk?watchlist_id=all-private-funds', undefined))
  })

  it('uses a flat table until grouping is explicitly selected for mixed watchlists', async () => {
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
      column_field_keys: ['instrument_name'],
      filter_field_keys: [],
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
      <LanguageProvider enableDomTranslation={false}>
        <MemoryRouter initialEntries={['/watchlists/mixed-research']}>
          <Routes>
            <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>,
    )

    await waitFor(() => expect(screen.getByText('Fund 1')).toBeTruthy())
    expect(screen.getByText('Equity 1')).toBeTruthy()
    expect(screen.queryByText('Public Fund')).toBeNull()
    expect(screen.queryByText('Equity')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Group By/ }))
    expect(screen.getByRole('button', { name: 'None' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Taxonomy' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Currency' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Investment Status' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Research Rating' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Instrument Type' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Freshness' })).toBeNull()
  })

  it.each([
    ['fund', ['public_fund']],
    ['equity', ['equity']],
    ['mixed', ['public_fund', 'equity']],
    ['empty', []],
  ] as const)('keeps the shared column and filter catalog in a %s watchlist', async (_name, instrumentTypes) => {
    const fields = [
      { field_key: 'instrument_name', label: 'Instrument', instrument_scope_json: [], filter_mode: 'text' },
      { field_key: 'instrument_type', label: 'Instrument Type', instrument_scope_json: [], filter_mode: 'multi_select' },
      { field_key: 'attr.coverage_status', label: 'Investment Status', instrument_scope_json: ['public_fund', 'private_fund', 'equity'], filter_mode: 'multi_select' },
      { field_key: 'attr.research_stage', label: 'Research Stage', instrument_scope_json: [], filter_mode: 'multi_select' },
      { field_key: 'latest_quote', label: 'Latest Quote', description: 'Fund NAV, market price, or index level as applicable.', instrument_scope_json: [], filter_mode: 'none' },
      { field_key: 'return_chart_1m', label: 'Return 1M', instrument_scope_json: [], filter_mode: 'none' },
    ].map((field) => ({
      category_code: 'general', data_type: 'string', formatter_code: 'text',
      sort_mode: 'alpha', group_mode: 'none', product_scope_json: [],
      availability_rule_json: {}, source_domain: 'read_model', source_metric_code: field.field_key,
      default_width: 160, default_visible: false, ...field,
    }))
    const record = {
      watchlist_id: 'shared-catalog', name: 'Shared Catalog', description: null,
      item_count: instrumentTypes.length, owner_type: 'team', owner_id: 'investment-team',
      is_default: false, is_shared: false, default_view_id: 'overview',
    }
    const detail: WatchlistDetail = {
      ...record, instrument_types: [...instrumentTypes],
      views: [{
        view_id: 'overview', view_key: 'overview', name: 'Overview', description: null, kind: 'custom',
        default_group_by: 'none', default_sort: [{ field: 'attr.research_stage', direction: 'asc' }],
        default_filters: { 'attr.research_stage': ['watching'] }, default_advanced_filters: null,
        columns: ['instrument_name', 'attr.coverage_status', 'attr.research_stage'],
      }],
      available_group_bys: [{ code: 'none', label: 'None' }, { code: 'currency', label: 'Currency' }],
      default_filters_summary: {},
    }
    apiMocks.getWatchlists.mockResolvedValue([record])
    apiMocks.getWatchlistDetail.mockResolvedValue(detail)
    apiMocks.getFieldRegistry.mockResolvedValue({
      categories: [{ category_code: 'general', label: 'Investment research', display_order: 0, parent_category_code: null }],
      total_fields: fields.length, fields,
      column_field_keys: ['instrument_name', 'instrument_type', 'attr.coverage_status', 'latest_quote', 'return_chart_1m'],
      filter_field_keys: ['instrument_type', 'attr.coverage_status'],
    })
    apiMocks.getInstrumentTaxonomyTree.mockResolvedValue({
      instrument_types: ['public_fund'], max_depth: 2,
      nodes: [
        { node_id: 'fund-allocation', label: 'Fund allocation', instrument_type: 'public_fund', parent_node_id: null, level_index: 0, display_order: 0, is_leaf: false, path_labels: ['Fund allocation'], path_node_ids: ['fund-allocation'] },
        { node_id: 'balanced', label: 'Balanced', instrument_type: 'public_fund', parent_node_id: 'fund-allocation', level_index: 1, display_order: 0, is_leaf: true, path_labels: ['Fund allocation', 'Balanced'], path_node_ids: ['fund-allocation', 'balanced'] },
        { node_id: 'unused', label: 'Unused classification', instrument_type: 'public_fund', parent_node_id: 'fund-allocation', level_index: 1, display_order: 1, is_leaf: true, path_labels: ['Fund allocation', 'Unused classification'], path_node_ids: ['fund-allocation', 'unused'] },
      ],
    })
    apiMocks.updateWatchlistView.mockImplementation(async (_watchlistId, _viewId, payload) => ({
      ...detail.views[0], columns: payload.columns.map((column: { field_key: string }) => column.field_key),
      default_sort: payload.default_sort, default_filters: payload.default_filters,
    }))
    apiMocks.runScreenerQuery.mockResolvedValue({
      rows: instrumentTypes.map((type) => ({
        instrument_id: type, instrument_name: type, instrument_type: type,
        ...(type === 'public_fund' ? { 'attr.instrument_taxonomy_level_1': 'Fund allocation', 'attr.instrument_taxonomy_level_2': 'Balanced' } : {}),
      })),
      groups: [], total_rows: instrumentTypes.length, stale_row_count: 0, sparklines: {},
    })

    render(
      <LanguageProvider enableDomTranslation={false}>
        <MemoryRouter initialEntries={['/watchlists/shared-catalog']}>
          <Routes><Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} /></Routes>
        </MemoryRouter>
      </LanguageProvider>,
    )
    await waitFor(() => expect(apiMocks.runScreenerQuery).toHaveBeenCalledWith(expect.objectContaining({
      selected_fields: expect.arrayContaining(['attr.coverage_status']),
      sort: [], filters: {},
    })))
    await waitFor(() => expect(apiMocks.updateWatchlistView).toHaveBeenCalledWith(
      'shared-catalog', 'overview', expect.objectContaining({
        columns: [expect.objectContaining({ field_key: 'instrument_name' }), expect.objectContaining({ field_key: 'attr.coverage_status' })],
        default_filters: {}, default_sort: [],
      }),
    ))
    fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
    let dialog = screen.getByRole('dialog', { name: 'Choose columns' })
    expect(within(dialog).getAllByRole('checkbox').map((item) => item.closest('label')?.textContent)).toEqual([
      'Instrument Type', 'Latest price / NAV', '1-month return chart', 'Investment Status',
    ])
    expect(within(dialog).getAllByRole('heading').map((item) => item.textContent)).toEqual([
      'Basic information', 'Returns', 'Risk and status',
    ])
    expect(within(dialog).queryByText('Investment research')).toBeNull()
    expect(within(dialog).queryByText('Fund NAV, market price, or index level as applicable.')).toBeNull()
    const quoteHelp = within(dialog).getByRole('button', { name: 'Latest price / NAV: Fund NAV, market price, or index level as applicable.' })
    expect(quoteHelp.getAttribute('title')).toBeNull()
    expect(quoteHelp.textContent).toBe('!')
    fireEvent.pointerEnter(quoteHelp, { pointerType: 'mouse' })
    const explanation = screen.getByRole('tooltip')
    expect(explanation.textContent).toContain('Fund NAV, market price, or index level as applicable.')
    expect(dialog.contains(explanation)).toBe(false)
    fireEvent.click(quoteHelp)
    expect(screen.getByRole('tooltip')).toBe(explanation)
    expect((within(dialog).getByLabelText('Latest price / NAV') as HTMLInputElement).checked).toBe(false)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Choose columns' })).toBe(dialog)
    fireEvent.change(within(dialog).getByPlaceholderText('Search columns'), { target: { value: '1-month' } })
    expect(within(dialog).getAllByRole('checkbox')).toHaveLength(1)
    expect(within(dialog).getByLabelText('1-month return chart')).toBeTruthy()
    fireEvent.change(within(dialog).getByPlaceholderText('Search columns'), { target: { value: '' } })
    expect((within(dialog).getByLabelText('Investment Status') as HTMLInputElement).checked).toBe(true)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
    expect(screen.getByRole('button', { name: 'Taxonomy' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Investment Status' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Instrument Type' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Research Stage' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Unused classification/ })).toBeNull()
    if (instrumentTypes.some((type) => type === 'public_fund')) {
      expect(screen.getByRole('button', { name: /^Fund allocation\s*1$/ })).toBeTruthy()
      expect(screen.getByRole('button', { name: /^Balanced\s*1$/ })).toBeTruthy()
    } else {
      expect(screen.getByText('No classified instruments in this watchlist.')).toBeTruthy()
    }

    if (_name === 'mixed') {
      fireEvent.click(screen.getByRole('button', { name: 'Instrument Type' }))
      fireEvent.click(screen.getByRole('checkbox', { name: 'equity' }))
      await waitFor(() => expect(apiMocks.runScreenerQuery).toHaveBeenCalledWith(expect.objectContaining({
        selected_fields: expect.arrayContaining(['attr.coverage_status']),
        filters: { instrument_type: ['equity'] },
        sort: [],
      })))
      fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
      dialog = screen.getByRole('dialog', { name: 'Choose columns' })
      expect((within(dialog).getByLabelText('Investment Status') as HTMLInputElement).checked).toBe(true)
    }
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
      column_field_keys: ['instrument_name', 'currency'],
      filter_field_keys: ['currency'],
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
      <LanguageProvider enableDomTranslation={false}>
        <MemoryRouter initialEntries={['/watchlists/coverage']}>
          <Routes>
            <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>,
    )

    await waitFor(() => expect(screen.getByText('Equity 1')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist settings' }))
    const settings = screen.getByRole('group', { name: 'Watchlist settings menu' })
    expect(within(settings).getByRole('button', { name: 'Copy Watchlist' })).toBeTruthy()
    expect(within(settings).getByRole('button', { name: 'Delete Watchlist' })).toBeTruthy()
    fireEvent.click(within(settings).getByRole('button', { name: 'Column settings' }))
    expect(screen.queryByRole('group', { name: 'Watchlist settings menu' })).toBeNull()
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
