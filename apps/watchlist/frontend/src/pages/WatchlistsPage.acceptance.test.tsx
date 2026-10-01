// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import WatchlistsPage from './WatchlistsPage'

const api = vi.hoisted(() => ({ getWatchlists: vi.fn(), getWatchlistDetail: vi.fn(), getFieldRegistry: vi.fn(), getInstrumentTaxonomyTree: vi.fn(), runScreenerQuery: vi.fn(), updateWatchlistView: vi.fn() }))
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), ...api }))
vi.mock('../components/AccountBoundary', () => ({ useCanWriteTeam: () => true }))
const view = { view_id: 'overview', view_key: 'overview', name: 'Overview', kind: 'custom', columns: ['instrument_name'], default_group_by: 'none', default_sort: [], default_filters: {} }
const detail = { watchlist_id: 'focus', name: 'Focus', item_count: 2, owner_type: 'team', owner_id: 'default', default_view_id: 'overview', instrument_types: ['equity'], views: [view], available_group_bys: [{ code: 'none', label: 'None' }, { code: 'currency', label: 'Currency' }] }
const fields = ['instrument_name', 'currency', 'return_ytd'].map(key => ({ field_key: key, label: { instrument_name: 'Name', currency: 'Currency', return_ytd: 'YTD' }[key], category_code: 'identity', data_type: key === 'return_ytd' ? 'number' : 'string', formatter_code: key === 'return_ytd' ? 'percent' : 'text', sort_mode: 'text', filter_mode: key === 'currency' ? 'multi_select' : 'none', group_mode: key === 'currency' ? 'exact' : 'none', default_width: 160, instrument_scope_json: [] }))
const rows = [{ instrument_id: 'goog', instrument_name: 'Alphabet Inc.', ticker_or_isin: 'GOOG', currency: 'USD', return_ytd: 2, metric_as_of_date: '2026-09-30' }, { instrument_id: 'googl', instrument_name: 'Alphabet Inc.', ticker_or_isin: 'GOOGL', currency: 'USD', return_ytd: null, metric_as_of_date: '2026-09-30' }]
const result = (r = rows) => ({ rows: r, groups: [], total_rows: r.length, sparklines: {}, snapshot_metadata: {} })
beforeEach(() => {
  vi.resetAllMocks()
  window.history.replaceState({}, '', '?lang=en')
  document.cookie = 'investment_studio_language=en; path=/'
  api.getWatchlists.mockResolvedValue([detail]); api.getWatchlistDetail.mockResolvedValue(detail)
  api.getFieldRegistry.mockResolvedValue({ fields, column_field_keys: fields.map(x => x.field_key), filter_field_keys: ['currency'] })
  api.getInstrumentTaxonomyTree.mockResolvedValue({ nodes: [], instrument_types: ['equity'] })
  api.runScreenerQuery.mockResolvedValue(result())
  api.updateWatchlistView.mockImplementation(async (_id, _view, p) => ({ ...view, columns: p.columns.map((c: {field_key: string}) => c.field_key), column_meta: p.columns, default_group_by: p.default_group_by, default_filters: p.default_filters, default_sort: p.default_sort }))
})
afterEach(cleanup)
function show() { return render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/watchlists/focus']}><Routes><Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} /></Routes></MemoryRouter></LanguageProvider>) }
function toggleColumn(label: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
  const dialog = screen.getByRole('dialog', { name: 'Choose columns' })
  fireEvent.click(within(dialog).getByText(label).closest('label')!.querySelector('input')!)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Update' }))
}
it('keeps share identifiers in Name and selection labels when optional identifiers are hidden', async () => {
  show()
  expect(await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })).toBeTruthy()
  expect(screen.getByRole('checkbox', { name: 'Select Alphabet Inc. · GOOGL' })).toBeTruthy()
  expect(screen.getByText('GOOG').closest('td')?.textContent).toContain('Alphabet Inc.')
})
it('keeps a newer column draft through an older save response and saves it in order', async () => {
  let finish!: (v: unknown) => void
  api.updateWatchlistView.mockImplementationOnce((_id, _view, p) => new Promise(resolve => { finish = () => resolve({ ...view, columns: p.columns.map((c: {field_key: string}) => c.field_key), column_meta: p.columns }) }))
  show(); await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  toggleColumn('Currency')
  expect(screen.getByText('Saving view…')).toBeTruthy()
  await waitFor(() => expect(api.updateWatchlistView).toHaveBeenCalledTimes(1))
  toggleColumn('Year-to-date return')
  await act(async () => finish(null))
  await waitFor(() => expect(api.updateWatchlistView).toHaveBeenCalledTimes(2))
  expect(api.updateWatchlistView.mock.calls[1][2].columns.map((c: {field_key: string}) => c.field_key)).toEqual(['instrument_name', 'currency', 'return_ytd'])
  await waitFor(() => expect(screen.queryByText('Saving view…')).toBeNull())
  expect(screen.getByRole('columnheader', { name: /YTD/ })).toBeTruthy()
})
it('does not call pending group facts unspecified and discloses valid-member means', async () => {
  api.getWatchlistDetail.mockResolvedValue({ ...detail, views: [{ ...view, columns: ['instrument_name', 'return_ytd'] }] })
  let finish!: (v: unknown) => void
  api.runScreenerQuery.mockResolvedValueOnce(result(rows.map(({ currency: _currency, ...r }) => r) as typeof rows)).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  show(); await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  fireEvent.click(screen.getByRole('button', { name: /Group By/ })); fireEvent.click(screen.getByRole('button', { name: 'Currency' }))
  await waitFor(() => expect(api.runScreenerQuery).toHaveBeenCalledTimes(2))
  expect(screen.queryByText('Unspecified')).toBeNull()
  await act(async () => finish(result()))
  expect(await screen.findByText('Mean · 1/2')).toBeTruthy()
  expect(screen.getByText(/mean drawdown is not portfolio drawdown/)).toBeTruthy()
})
it('constrains the filter position to the viewport and returns focus on Escape without clearing filters', async () => {
  show(); await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  const trigger = screen.getByRole('button', { name: 'Filter' })
  vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({ left: 1100, right: 1160, top: 120, bottom: 160, width: 60, height: 40, x: 1100, y: 120, toJSON() {} })
  fireEvent.click(trigger)
  const panel = screen.getByRole('dialog', { name: 'Filter instruments' })
  expect(parseFloat(panel.style.left) + 680).toBeLessThanOrEqual(window.innerWidth - 16)
  fireEvent.click(within(panel).getByRole('button', { name: 'Currency' }))
  await waitFor(() => expect(within(panel).getByLabelText('USD')).toBeTruthy())
  fireEvent.click(within(panel).getByLabelText('USD'))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog', { name: 'Filter instruments' })).toBeNull()
  expect(document.activeElement).toBe(trigger)
  expect(trigger.textContent).toContain('1')
})
it('distinguishes true empty lists from search misses with usable actions', async () => {
  api.runScreenerQuery.mockResolvedValue(result([])); show()
  const add = await screen.findByRole('button', { name: 'Add instruments' }); fireEvent.click(add)
  expect(screen.getByRole('dialog', { name: 'Add instruments' })).toBeTruthy()
  cleanup(); api.runScreenerQuery.mockResolvedValue(result()); show()
  await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'missing' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Clear search and filters' }))
  expect(await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })).toBeTruthy()
})
it('selects all 269 filtered results before expanding rows, and search removes hidden selections', async () => {
  api.runScreenerQuery.mockResolvedValue(result(Array.from({ length: 269 }, (_, i) => ({ ...rows[0], instrument_id: `asset-${i}`, instrument_name: `Asset ${i}`, ticker_or_isin: `CODE-${i}` })))); show()
  await screen.findByRole('checkbox', { name: 'Select Asset 0 · CODE-0' })
  expect(screen.getAllByRole('checkbox')).toHaveLength(81)
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select all filtered instruments' }))
  expect(screen.getByText('269 selected of 269 filtered results')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
  expect(screen.getAllByRole('checkbox')).toHaveLength(270)
  expect(screen.getByText('269 selected of 269 filtered results')).toBeTruthy()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'CODE-268' } })
  await screen.findByText('1 selected of 1 filtered results')
})
it('keeps a failed view change visible and retries its exact settings', async () => {
  api.updateWatchlistView.mockRejectedValueOnce(new Error('Save unavailable'))
  show(); await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  toggleColumn('Currency')
  const retry = await screen.findByRole('button', { name: 'Retry save' })
  expect(screen.getByRole('columnheader', { name: /Currency/ })).toBeTruthy()
  fireEvent.click(retry)
  await waitFor(() => expect(api.updateWatchlistView).toHaveBeenCalledTimes(2))
  expect(api.updateWatchlistView.mock.calls[1][2]).toEqual(api.updateWatchlistView.mock.calls[0][2])
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry save' })).toBeNull())
})
it('retries a failed initial list request and returns to that list', async () => {
  api.getWatchlistDetail.mockRejectedValueOnce(new Error('Identity dependency unavailable'))
  show()
  const retry = await screen.findByRole('button', { name: 'Retry' })
  expect(screen.getByRole('link', { name: 'Home' })).toBeTruthy()
  fireEvent.click(retry)
  expect(await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })).toBeTruthy()
  expect(api.getWatchlistDetail.mock.calls.every(([id]) => id === 'focus')).toBe(true)
})
