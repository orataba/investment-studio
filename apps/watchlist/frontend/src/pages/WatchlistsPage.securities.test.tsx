// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import WatchlistsPage from './WatchlistsPage'
import type { SecuritySearchResult, WatchlistDetail } from '../lib/api'

const mocks = vi.hoisted(() => ({
  getWatchlists: vi.fn(), getWatchlistDetail: vi.fn(), getFieldRegistry: vi.fn(),
  getInstrumentTaxonomyTree: vi.fn(), runScreenerQuery: vi.fn(), searchSharedInstruments: vi.fn(),
  searchSecurities: vi.fn(), materializeSecurity: vi.fn(), addWatchlistItems: vi.fn(), updateWatchlistCoverageStatus: vi.fn(),
  resolveSharedInstrumentsFile: vi.fn(), createWatchlist: vi.fn(),
  canWrite: vi.fn(),
}))
vi.mock('../lib/api', async (original) => ({ ...(await original<typeof import('../lib/api')>()), ...mocks }))
vi.mock('../components/AccountBoundary', async (original) => ({ ...(await original<typeof import('../components/AccountBoundary')>()), useCanWriteTeam: mocks.canWrite }))

const detail: WatchlistDetail = {
  watchlist_id: 'focus', name: 'Focus', description: null, item_count: 0,
  owner_type: 'team', owner_id: 'default', created_by_user_id: 'alice', created_by_display_name: 'Alice',
  is_default: false, is_shared: true, default_view_id: 'overview', instrument_types: [],
  views: [{ view_id: 'overview', view_key: 'overview', name: 'Overview', description: null,
    kind: 'system', default_group_by: 'none', default_sort: [], default_filters: {},
    default_advanced_filters: null, columns: ['instrument_name'] }],
  available_group_bys: [{ code: 'none', label: 'None' }], default_filters_summary: {},
}
const catalog: SecuritySearchResult = {
  instrument_type: 'etf', symbol: 'SHV', catalog_provider: 'fmp', catalog_symbol: 'SHV',
  name: 'Short Treasury ETF', exchange_code: 'XNAS', exchange_label: 'NASDAQ', market: 'US',
  currency: 'USD', currency_verified: false, existing_instrument_id: null,
}
const registered = { instrument_id: 'shv', instrument_name: catalog.name, instrument_type: 'etf', currency: 'USD',
  identifiers: [{ identifier_type: 'ticker', identifier_value: 'SHV', is_primary: true }] }

beforeEach(() => {
  vi.resetAllMocks()
  window.history.replaceState({}, '', '?lang=en')
  document.cookie = 'investment_studio_language=en; path=/'
  mocks.canWrite.mockReturnValue(true)
  mocks.getWatchlists.mockResolvedValue([detail])
  mocks.getWatchlistDetail.mockResolvedValue(detail)
  mocks.getFieldRegistry.mockResolvedValue({ fields: [], column_field_keys: ['instrument_name'], filter_field_keys: [] })
  mocks.getInstrumentTaxonomyTree.mockResolvedValue({ nodes: [], instrument_types: [] })
  mocks.runScreenerQuery.mockResolvedValue({ rows: [], groups: [], total_rows: 0, sparklines: {}, snapshot_metadata: {} })
  mocks.searchSharedInstruments.mockResolvedValue([])
  mocks.searchSecurities.mockResolvedValue({ results: [catalog], catalog_errors: {} })
  mocks.materializeSecurity.mockResolvedValue(registered)
  mocks.addWatchlistItems.mockResolvedValue({ accepted_count: 1 })
})
afterEach(cleanup)

function showPage(path = '/watchlists/focus') {
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={[path]}>
    <Routes><Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} /></Routes>
  </MemoryRouter></LanguageProvider>)
}

async function chooseDirectorySecurity() {
  showPage()
  const add = await screen.findByRole('button', { name: 'Add' })
  await waitFor(() => expect(add.hasAttribute('disabled')).toBe(false))
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText('Search Instruments'), { target: { value: 'SHV' } })
  const candidate = await screen.findByRole('button', { name: /SHV · NASDAQ/ })
  expect(within(candidate).getByText(/Currency to be verified/)).toBeTruthy()
  expect(mocks.materializeSecurity).not.toHaveBeenCalled()
  fireEvent.click(candidate)
}

it('registers only the explicitly chosen directory security, then adds its canonical ID', async () => {
  await chooseDirectorySecurity()
  fireEvent.click(screen.getByRole('button', { name: 'Register and add' }))
  await waitFor(() => expect(mocks.addWatchlistItems).toHaveBeenCalledWith('focus', ['shv'], undefined))
  expect(mocks.materializeSecurity).toHaveBeenCalledExactlyOnceWith(catalog)
  await waitFor(() => expect(screen.getByLabelText('Search Instruments')).toHaveProperty('value', ''))
  expect(screen.getByRole('dialog', { name: 'Add instruments' })).toBeTruthy()
  await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('Search Instruments')))
  expect(mocks.getWatchlists).toHaveBeenCalledTimes(2)
})

it('retains the registered asset when adding list membership fails, so retry does not register twice', async () => {
  mocks.addWatchlistItems.mockRejectedValueOnce(new Error('List update failed')).mockResolvedValueOnce({ accepted_count: 1 })
  await chooseDirectorySecurity()
  fireEvent.click(screen.getByRole('button', { name: 'Register and add' }))
  expect((await screen.findByRole('alert')).textContent).toContain('List update failed')
  fireEvent.click(screen.getByRole('button', { name: 'Add To Watchlist' }))
  await waitFor(() => expect(mocks.addWatchlistItems).toHaveBeenCalledTimes(2))
  expect(mocks.materializeSecurity).toHaveBeenCalledTimes(1)
})

it('shows the creator in settings and prevents a reader from adding or creating lists', async () => {
  mocks.canWrite.mockReturnValue(false)
  showPage()
  const add = await screen.findByRole('button', { name: 'Add' })
  expect(add.hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('button', { name: '+ Create Watchlist' }).hasAttribute('disabled')).toBe(true)
  const settings = screen.getByRole('button', { name: 'Watchlist settings' })
  await waitFor(() => expect(settings.hasAttribute('disabled')).toBe(false))
  fireEvent.click(settings)
  const menu = screen.getByRole('group', { name: 'Watchlist settings menu' })
  expect(within(menu).getByText('Created by Alice')).toBeTruthy()
  expect(within(menu).getByRole('button', { name: 'Copy Watchlist' }).hasAttribute('disabled')).toBe(true)
})

it('saves the chosen investment status with every addition while keeping it for the next search', async () => {
  await chooseDirectorySecurity()
  fireEvent.change(screen.getByLabelText('Investment status'), { target: { value: 'Proposed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Register and add' }))
  await waitFor(() => expect(mocks.addWatchlistItems).toHaveBeenCalledWith('focus', ['shv'], 'Proposed'))
  await waitFor(() => expect(screen.getByLabelText('Search Instruments')).toHaveProperty('value', ''))
  expect(screen.getByLabelText('Investment status')).toHaveProperty('value', 'Proposed')
  fireEvent.change(screen.getByLabelText('Search Instruments'), { target: { value: 'another' } })
  expect(await screen.findByRole('button', { name: /SHV · NASDAQ/ })).toBeTruthy()
})

it('sets investment status for selected rows in a single batch', async () => {
  mocks.runScreenerQuery.mockResolvedValue({ rows: [{ instrument_id: 'shv', instrument_name: 'SHV' }], groups: [], total_rows: 1, sparklines: {}, snapshot_metadata: {} })
  mocks.updateWatchlistCoverageStatus.mockResolvedValue({ updated_count: 1 })
  showPage()
  const selected = await screen.findByRole('checkbox', { name: /Select SHV/ })
  fireEvent.click(selected)
  fireEvent.click(screen.getByRole('button', { name: 'Set Invested' }))
  await waitFor(() => expect(mocks.updateWatchlistCoverageStatus).toHaveBeenCalledWith('focus', ['shv'], 'Invested'))
})

it('keeps failed list creation editable and reports the service error outside the dialog', async () => {
  mocks.createWatchlist.mockRejectedValue(new Error('Creation service unavailable'))
  showPage()
  fireEvent.click(await screen.findByRole('button', { name: '+ Create Watchlist' }))
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My research' } })
  const dialog = screen.getByRole('dialog', { name: 'Create watchlist' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create Watchlist' }))
  const notice = await screen.findByRole('alert')
  expect(notice.textContent).toContain('Creation service unavailable')
  expect(notice.closest('#investment-studio-notices')).toBeTruthy()
  expect(dialog.contains(notice)).toBe(false)
  expect(screen.getByLabelText('Name')).toHaveProperty('value', 'My research')
})

it('separates file validation from upload service failures without closing the add dialog', async () => {
  mocks.resolveSharedInstrumentsFile.mockResolvedValueOnce({ results: [{ identifier: 'MISSING', status: 'not_found', instrument: null }] })
    .mockRejectedValueOnce(new Error('File service unavailable'))
  await chooseDirectorySecurity()
  const dialog = screen.getByRole('dialog', { name: 'Add instruments' })
  const upload = dialog.querySelector('input[type="file"]')!
  const file = new File(['MISSING'], 'identifiers.txt', { type: 'text/plain' })
  fireEvent.change(upload, { target: { files: [file] } })
  const validation = await within(dialog).findByRole('alert')
  expect(validation.textContent).toContain('These identifiers were not found')
  expect(validation.closest('#investment-studio-notices')).toBeNull()
  expect(mocks.addWatchlistItems).not.toHaveBeenCalled()
  fireEvent.change(upload, { target: { files: [file] } })
  const notice = await screen.findByRole('alert')
  expect(notice.textContent).toContain('File service unavailable')
  expect(notice.closest('#investment-studio-notices')).toBeTruthy()
  expect(within(dialog).queryByRole('alert')).toBeNull()
  expect(screen.getByLabelText('Search Instruments')).toHaveProperty('value', 'SHV')
})

it('identifies existing members independently of filtered rows and blocks redundant additions', async () => {
  mocks.getWatchlistDetail.mockResolvedValue({ ...detail, item_count: 1, instrument_ids: ['shv'] })
  mocks.searchSharedInstruments.mockResolvedValue([registered])
  mocks.searchSecurities.mockResolvedValue({ results: [], catalog_errors: {} })
  showPage()
  const add = await screen.findByRole('button', { name: 'Add' })
  await waitFor(() => expect(add.hasAttribute('disabled')).toBe(false))
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText('Search Instruments'), { target: { value: 'SHV' } })
  const member = await screen.findByRole('button', { name: /SHV.*Already in this watchlist/ })
  expect(member.hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('button', { name: 'Add To Watchlist' }).hasAttribute('disabled')).toBe(true)
  fireEvent.click(member)
  expect(mocks.addWatchlistItems).not.toHaveBeenCalled()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(document.activeElement).toBe(add))
})


it('preserves a named view through membership refresh instead of selecting the default view', async () => {
  const named = { ...detail.views[0], view_id: 'numeric', view_key: 'numeric', name: 'Numeric Check', kind: 'custom' }
  mocks.getWatchlistDetail.mockResolvedValue({ ...detail, views: [...detail.views, named] })
  mocks.searchSharedInstruments.mockResolvedValue([registered])
  mocks.searchSecurities.mockResolvedValue({ results: [], catalog_errors: {} })
  showPage('/watchlists/focus?view=numeric')
  const add = await screen.findByRole('button', { name: 'Add' })
  await waitFor(() => expect(add.hasAttribute('disabled')).toBe(false))
  const view = screen.getByRole('option', { name: /Numeric Check/ }).parentElement as HTMLSelectElement
  expect(view.value).toBe('numeric')
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText('Search Instruments'), { target: { value: 'SHV' } })
  fireEvent.click(await screen.findByRole('button', { name: /Short Treasury ETF/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Add To Watchlist' }))
  await waitFor(() => expect(mocks.getWatchlistDetail).toHaveBeenCalledTimes(2))
  expect(view.value).toBe('numeric')
  expect(mocks.runScreenerQuery.mock.calls[mocks.runScreenerQuery.mock.calls.length - 1]?.[0].view_id).toBe('numeric')
})
