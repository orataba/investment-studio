// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import WatchlistsPage from './WatchlistsPage'

const mocks = vi.hoisted(() => ({
  getWatchlists: vi.fn(), getFieldRegistry: vi.fn(), getInstrumentTaxonomyTree: vi.fn(),
  getWatchlistDetail: vi.fn(), runScreenerQuery: vi.fn(), fetchJson: vi.fn(),
}))
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()), ...mocks,
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function record(id: string) {
  return { watchlist_id: id, name: id, description: null, item_count: 1,
    owner_type: 'team', owner_id: 'team', is_default: false, is_shared: true,
    default_view_id: `${id}-overview` }
}

function detail(id: string) {
  return { ...record(id), instrument_types: ['equity'], default_filters_summary: {},
    available_group_bys: [{ code: 'none', label: 'None' }],
    views: [{ view_id: `${id}-overview`, view_key: 'overview', name: 'Overview',
      description: null, kind: 'system', default_group_by: 'none', default_sort: [],
      default_filters: {}, default_advanced_filters: null, columns: ['instrument_name', 'currency'] }] }
}

function rows(id: string) {
  return { rows: [{ instrument_id: `${id}-security`, instrument_name: `${id} security`, currency: 'USD' }],
    groups: [], total_rows: 1, stale_row_count: 0, sparklines: {},
    snapshot_metadata: { as_of_date: '2026-09-18', as_of_date_min: '2026-09-18', as_of_date_max: '2026-09-18',
      has_mixed_as_of_dates: false, as_of_date_missing_count: 0, methodology_version: 'test',
      source_cutoff_at: null, is_current: true, advanced_filter_applied: false } }
}

function Navigation() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/watchlists/Beta')}>Open Beta</button>
}

function openPage() {
  return render(<LanguageProvider enableDomTranslation={false}>
    <MemoryRouter initialEntries={['/watchlists/Alpha']}>
      <Navigation />
      <Routes><Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} /></Routes>
    </MemoryRouter>
  </LanguageProvider>)
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.getWatchlists.mockResolvedValue([record('Alpha'), record('Beta')])
  mocks.getFieldRegistry.mockResolvedValue({ categories: [], total_fields: 2,
    column_field_keys: ['instrument_name', 'currency'], filter_field_keys: [],
    fields: ['instrument_name', 'currency'].map((field_key) => ({
      field_key, label: field_key, category_code: 'identity', data_type: 'string',
      formatter_code: 'text', sort_mode: 'text', filter_mode: 'none', group_mode: 'none',
      instrument_scope_json: [], product_scope_json: [], availability_rule_json: {},
      source_domain: 'registry', source_metric_code: field_key, default_width: 180, default_visible: true,
    })) })
  mocks.getInstrumentTaxonomyTree.mockResolvedValue({ taxonomy_code: 'instrument_taxonomy', instrument_types: ['equity'], max_depth: 0, nodes: [] })
  mocks.getWatchlistDetail.mockImplementation(async (id: string) => detail(id))
  mocks.runScreenerQuery.mockImplementation(async (payload) => rows(payload.watchlist_id))
  mocks.fetchJson.mockResolvedValue({ instruments: [], cases: [], available: true, sectors: [], events: [] })
})
afterEach(cleanup)

it('loads rows after directory synchronization without waiting for optional classification choices', async () => {
  const directory = deferred<ReturnType<typeof record>[]>()
  const taxonomy = deferred<unknown>()
  const result = deferred<ReturnType<typeof rows>>()
  mocks.getWatchlists.mockReturnValue(directory.promise)
  mocks.getInstrumentTaxonomyTree.mockReturnValue(taxonomy.promise)
  mocks.runScreenerQuery.mockReturnValue(result.promise)
  openPage()
  await waitFor(() => expect(mocks.getWatchlistDetail).toHaveBeenCalledTimes(1))
  expect(mocks.getInstrumentTaxonomyTree).toHaveBeenCalledTimes(1)
  expect(mocks.runScreenerQuery).not.toHaveBeenCalled()
  await act(async () => directory.resolve([record('Alpha'), record('Beta')]))
  await waitFor(() => expect(mocks.runScreenerQuery).toHaveBeenCalledTimes(1))
  expect(mocks.runScreenerQuery.mock.calls[0][0]).toMatchObject({
    watchlist_id: 'Alpha', view_id: 'Alpha-overview', selected_fields: ['instrument_name', 'currency'],
  })
  expect(screen.getByRole('status').classList.contains('watchlist-loading-overlay')).toBe(true)
  await act(async () => result.resolve(rows('Alpha')))
  expect(screen.getByText('Alpha security')).toBeTruthy()
  expect(screen.queryByRole('status')).toBeNull()
  await act(async () => taxonomy.reject(new Error('Classification choices unavailable')))
  expect(screen.getByText('Alpha security')).toBeTruthy()
  expect(mocks.runScreenerQuery).toHaveBeenCalledTimes(1)
})

it('aborts old detail requests and ignores a late detail response after navigation', async () => {
  const old = deferred<ReturnType<typeof detail>>()
  mocks.getWatchlistDetail.mockImplementation((id: string) => id === 'Alpha' ? old.promise : Promise.resolve(detail(id)))
  openPage()
  await waitFor(() => expect(mocks.getWatchlistDetail).toHaveBeenCalledTimes(1))
  const oldSignal = mocks.getWatchlistDetail.mock.calls[0][1] as AbortSignal
  fireEvent.click(screen.getByRole('button', { name: 'Open Beta' }))
  await waitFor(() => expect(mocks.getWatchlistDetail.mock.calls.map(([id]) => id)).toContain('Beta'))
  expect(await screen.findByText('Beta security')).toBeTruthy()
  expect(oldSignal.aborted).toBe(true)
  await act(async () => old.resolve(detail('Alpha')))
  expect(screen.queryByText('Alpha security')).toBeNull()
  expect(screen.getByRole('heading', { level: 1, name: 'Beta' })).toBeTruthy()
  expect(mocks.runScreenerQuery.mock.calls.every(([payload]) => payload.watchlist_id === 'Beta')).toBe(true)
})

it('only loads filter choices when opened and cancels the optional request when closed', async () => {
  const options = deferred<ReturnType<typeof rows>>()
  mocks.runScreenerQuery.mockImplementation((payload) => payload.view_id
    ? Promise.resolve(rows(payload.watchlist_id)) : options.promise)
  openPage()
  expect(await screen.findByText('Alpha security')).toBeTruthy()
  expect(mocks.runScreenerQuery).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
  await waitFor(() => expect(mocks.runScreenerQuery).toHaveBeenCalledTimes(2))
  const optionsSignal = mocks.runScreenerQuery.mock.calls[1][1] as AbortSignal
  expect(screen.getByText('Loading filter options…')).toBeTruthy()
  expect(document.querySelector('.watchlist-loading-overlay')).toBeNull()
  expect(screen.getByText('Alpha security')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
  expect(optionsSignal.aborted).toBe(true)
  await act(async () => options.resolve(rows('Alpha')))
  expect(screen.queryByText('Loading filter options…')).toBeNull()
  expect(screen.getByText('Alpha security')).toBeTruthy()
  expect(mocks.runScreenerQuery).toHaveBeenCalledTimes(2)
})

it('aborts old row requests without allowing their late result to replace the new list', async () => {
  const old = deferred<ReturnType<typeof rows>>()
  mocks.runScreenerQuery.mockImplementation((payload) => payload.watchlist_id === 'Alpha' ? old.promise : Promise.resolve(rows('Beta')))
  openPage()
  await waitFor(() => expect(mocks.runScreenerQuery).toHaveBeenCalledTimes(1))
  const oldSignal = mocks.runScreenerQuery.mock.calls[0][1] as AbortSignal
  fireEvent.click(screen.getByRole('button', { name: 'Open Beta' }))
  await waitFor(() => expect(mocks.getWatchlistDetail.mock.calls.map(([id]) => id)).toContain('Beta'))
  expect(await screen.findByText('Beta security')).toBeTruthy()
  expect(oldSignal.aborted).toBe(true)
  await act(async () => old.resolve(rows('Alpha')))
  expect(screen.queryByText('Alpha security')).toBeNull()
  expect(screen.getByText('Beta security')).toBeTruthy()
  expect(screen.queryByRole('status')).toBeNull()
  expect(mocks.runScreenerQuery.mock.calls.map(([payload]) => payload.watchlist_id)).toEqual(['Alpha', 'Beta'])
})
