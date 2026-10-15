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
function show(enableDomTranslation = false) { return render(<LanguageProvider enableDomTranslation={enableDomTranslation}><MemoryRouter initialEntries={['/watchlists/focus']}><Routes><Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} /></Routes></MemoryRouter></LanguageProvider>) }
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
it('discloses an incomplete return window instead of drawing an apparently complete sparkline', async () => {
  const chartKey = 'return_chart_1m'
  api.getWatchlistDetail.mockResolvedValue({ ...detail, views: [{ ...view, columns: ['instrument_name', chartKey] }] })
  const chartField = { ...fields[0], field_key: chartKey, label: 'Return 1M' }
  api.getFieldRegistry.mockResolvedValue({ fields: [...fields, chartField], column_field_keys: [...fields.map(x => x.field_key), chartKey], filter_field_keys: [] })
  const reason = 'Missing observation inside the requested return window'
  api.runScreenerQuery.mockResolvedValue({ ...result(), sparklines: { goog: { [chartKey]: { status: 'unavailable', reason, points: [] } } } })
  show()
  const unavailable = await screen.findByLabelText(reason)
  expect(unavailable.textContent).toBe('—')
  expect(unavailable.getAttribute('title')).toBe(reason)
  expect(unavailable.closest('td')?.querySelector('svg')).toBeNull()
})
it('mirrors selected return values under the mobile identity without changing the named view', async () => {
  const named = { ...view, name: 'Research returns', columns: ['instrument_name', 'currency', 'return_ytd'] }
  api.getWatchlistDetail.mockResolvedValue({ ...detail, views: [named] })
  const { container } = show()
  await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  const summaries = container.querySelectorAll('.watchlists-mobile-returns')
  expect(summaries[0].textContent).toContain('YTD')
  expect(summaries[0].textContent).toContain('2.00%')
  expect(summaries[1].textContent).toContain('—')
  expect(summaries[0].querySelector('[title]')?.getAttribute('title')).toContain('2026-09-30')
  expect(api.updateWatchlistView).not.toHaveBeenCalled()
  expect(screen.getByRole('columnheader', { name: /YTD/ })).toBeTruthy()
  expect(screen.getByRole('option', { name: /Research returns/ })).toBeTruthy()
})
it('preserves custom list and view names through language switches while localizing system names', async () => {
  const named = { ...detail, name: 'Risk', views: [{ ...view, name: 'High' }, { ...view, view_id: 'system-overview', view_key: 'system-overview', kind: 'system' }] }
  api.getWatchlists.mockResolvedValue([named, { ...detail, watchlist_id: 'target', name: 'Return' }, { ...detail, watchlist_id: 'all', name: 'All Instruments', owner_type: 'system' }])
  api.getWatchlistDetail.mockResolvedValue(named)
  const { container } = show(true)
  await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  const listSelect = screen.getByRole('combobox', { name: 'Switch watchlist' }) as HTMLSelectElement
  const viewSelect = screen.getByRole('option', { name: /^View\s*: High$/ }).parentElement as HTMLSelectElement
  for (let round = 0; round < 2; round += 1) {
    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
    await waitFor(() => expect(within(listSelect).getByRole('option', { name: '全部标的' })).toBeTruthy())
    expect(within(listSelect).getByRole('option', { name: 'Risk' }).getAttribute('value')).toBe('focus')
    expect(within(listSelect).getByRole('option', { name: 'Return' }).getAttribute('value')).toBe('target')
    expect(within(viewSelect).getByRole('option', { name: /^视图\s*: High$/ }).getAttribute('value')).toBe('overview')
    expect(within(viewSelect).getByRole('option', { name: /^视图\s*: 总览$/ }).getAttribute('value')).toBe('system-overview')
    expect(listSelect.value).toBe('focus'); expect(viewSelect.value).toBe('overview')
    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => expect(within(listSelect).getByRole('option', { name: 'All Instruments' })).toBeTruthy())
    expect(within(viewSelect).getByRole('option', { name: /^View\s*: High$/ })).toBeTruthy()
    expect(within(viewSelect).getByRole('option', { name: /^View\s*: Overview$/ })).toBeTruthy()
  }
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' }))
  for (const action of ['Copy', 'Move']) {
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${action}$`) }))
    const dialog = screen.getByRole('dialog', { name: `${action} selected instruments` })
    const target = within(dialog).getByRole('combobox') as HTMLSelectElement
    expect(target.value).toBe('target')
    expect(within(dialog).getByText('1 selected from Risk.')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
    await waitFor(() => expect(within(dialog).getByText('目标关注列表')).toBeTruthy())
    expect(within(dialog).getByText('已从“Risk”选择 1 项。')).toBeTruthy()
    expect(within(target).getByRole('option', { name: 'Return' }).getAttribute('value')).toBe('target')
    expect(target.value).toBe('target')
    expect(container.querySelector('.watchlists-compact-switch [translate="no"]')?.textContent).toBe('Risk')
    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Close' })).toBeTruthy())
    expect(within(dialog).getByText('1 selected from Risk.')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
  }
  expect(api.updateWatchlistView).not.toHaveBeenCalled()
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
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search this watchlist' }), { target: { value: 'missing' } })
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
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search this watchlist' }), { target: { value: 'CODE-268' } })
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

it('keeps a searchable list selector and current identity for long names and no matches', async () => {
  const lists = Array.from({ length: 20 }, (_, i) => ({ ...detail, watchlist_id: `list-${i}`, name: `全球多资产长期研究关注列表 ${i}` }))
  api.getWatchlists.mockResolvedValue([detail, ...lists])
  show()
  const selector = await screen.findByRole('combobox', { name: 'Switch watchlist' }) as HTMLSelectElement
  const search = screen.getByRole('searchbox', { name: 'Find watchlist' })
  fireEvent.change(search, { target: { value: '19' } })
  expect(within(selector).getAllByRole('option')).toHaveLength(2)
  expect(within(selector).getByRole('option', { name: '全球多资产长期研究关注列表 19' })).toBeTruthy()
  expect(selector.value).toBe('focus')
  fireEvent.change(search, { target: { value: 'no such list' } })
  expect(within(selector).getAllByRole('option')).toHaveLength(1)
  expect(selector.title).toBe('Focus')
  expect(screen.getByText('No matching list; current selection retained.')).toBeTruthy()
  expect(api.updateWatchlistView).not.toHaveBeenCalled()
})

it('recovers a failed row request without hiding the current list identity', async () => {
  api.runScreenerQuery.mockRejectedValue(new Error('The read request timed out.'))
  show()
  await screen.findByText('The read request timed out.')
  expect((screen.getByRole('combobox', { name: 'Switch watchlist' }) as HTMLSelectElement).value).toBe('focus')
  api.runScreenerQuery.mockResolvedValue(result())
  fireEvent.click(screen.getByRole('button', { name: /retry/i }))
  await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  expect(screen.queryByText('The read request timed out.')).toBeNull()
})

function readExport(blob: Blob, binary = false) {
  return new Promise<string | ArrayBuffer>(resolve => {
    const reader = new FileReader(); reader.onload = () => resolve(reader.result as string | ArrayBuffer)
    if (binary) reader.readAsArrayBuffer(blob); else reader.readAsText(blob)
  })
}
function csvRows(text: string) {
  const rows: string[][] = []; let row: string[] = []; let cell = ''; let quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    if (char === '"') { if (quoted && text[i + 1] === '"') { cell += '"'; i += 1 } else quoted = !quoted }
    else if (char === ',' && !quoted) { row.push(cell); cell = '' }
    else if (char === '\n' && !quoted) { row.push(cell.replace(/\r$/, '')); rows.push(row); row = []; cell = '' }
    else cell += char
  }
  if (cell || row.length) { row.push(cell); rows.push(row) }
  if (rows.length) rows[0][0] = rows[0][0].replace(/^\uFEFF/, '')
  return rows
}
function xlsxRows(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer); const view = new DataView(buffer); const decoder = new TextDecoder()
  for (let at = 0; at + 30 <= bytes.length && view.getUint32(at, true) === 0x04034b50;) {
    const compression = view.getUint16(at + 8, true); const size = view.getUint32(at + 18, true)
    const nameLength = view.getUint16(at + 26, true); const extra = view.getUint16(at + 28, true)
    const name = decoder.decode(bytes.slice(at + 30, at + 30 + nameLength)); const start = at + 30 + nameLength + extra
    if (name === 'xl/worksheets/sheet1.xml') {
      expect(compression).toBe(0)
      const xml = new DOMParser().parseFromString(decoder.decode(bytes.slice(start, start + size)), 'application/xml')
      expect(xml.querySelector('parsererror')).toBeNull()
      return [...xml.querySelectorAll('sheetData row')].map(row => {
        const values = Array<string>(9).fill('')
        for (const cell of row.querySelectorAll('c')) {
          const letters = cell.getAttribute('r')!.replace(/\d/g, '')
          const index = [...letters].reduce((value, char) => value * 26 + char.charCodeAt(0) - 64, 0) - 1
          values[index] = cell.querySelector('t, v')?.textContent || ''
        }
        return values
      })
    }
    at = start + size
  }
  throw new Error('Export has no worksheet')
}

it.each(['CSV', 'Excel'])('downloads all 269 rows with explicit units, clocks and nulls in the actual %s file', async format => {
  const chart = { ...fields[0], field_key: 'return_chart_1m', label: 'Return 1M' }
  const status = { ...fields[0], field_key: 'attr.coverage_status', label: 'Investment Status', data_type: 'enum' }
  api.getFieldRegistry.mockResolvedValue({ fields: [...fields, chart, status], column_field_keys: [...fields.map(f => f.field_key), chart.field_key, status.field_key], filter_field_keys: [] })
  api.getWatchlistDetail.mockResolvedValue({ ...detail, item_count: 269, views: [{ ...view, columns: ['instrument_name', 'return_ytd', 'attr.coverage_status', chart.field_key] }] })
  const allRows = Array.from({ length: 269 }, (_, i) => ({ ...rows[0], instrument_id: `asset-${i}`, instrument_name: i === 0 ? 'A long, quoted "基金名称" · 多币种组合' : `Asset ${i}`, return_ytd: i === 0 ? 0 : i === 1 ? null : 12345.6789, 'attr.coverage_status': 'Invested', metric_quote_basis: 'daily_close', metric_return_kind: 'price_return', metric_series_type: 'canonical' }))
  api.runScreenerQuery.mockImplementation(async (payload: { fetch_all?: boolean }) => ({ ...result(payload.fetch_all ? allRows : allRows.slice(0, 2)), total_rows: 269 }))
  const created = vi.fn((_blob: Blob) => 'blob:watchlist-export')
  const oldCreate = URL.createObjectURL; const oldRevoke = URL.revokeObjectURL
  URL.createObjectURL = created; URL.revokeObjectURL = vi.fn()
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  try {
    show()
    await screen.findByRole('checkbox', { name: /Select A long/ })
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    fireEvent.click(screen.getByRole('menuitem', { name: format }))
    await waitFor(() => expect(created).toHaveBeenCalledOnce())
    const file = await readExport(created.mock.calls[0][0], format === 'Excel')
    const exported = format === 'Excel' ? xlsxRows(file as ArrayBuffer) : csvRows(file as string)
    expect(exported).toHaveLength(270)
    expect(exported[0]).toEqual(['instrument_id', 'currency', 'instrument_name', 'return_ytd (%)', 'attr.coverage_status', 'metric_as_of_date', 'metric_return_kind', 'metric_quote_basis', 'metric_series_type'])
    expect(exported[1]).toEqual(['asset-0', 'USD', allRows[0].instrument_name, '0', 'Invested', '2026-09-30', 'price_return', 'daily_close', 'canonical'])
    expect(exported[2][3]).toBe('')
    expect(exported[exported.length - 1].slice(0, 4)).toEqual(['asset-268', 'USD', 'Asset 268', '12345.6789'])
    expect(api.runScreenerQuery).toHaveBeenCalledWith(expect.objectContaining({ fetch_all: true, selected_fields: expect.arrayContaining(['currency']) }), undefined)
    expect(click).toHaveBeenCalledOnce()
  } finally { click.mockRestore(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke }
})

it('explains business measurement fields without adding implementation hints to identity or status', async () => {
  const status = { ...fields[0], field_key: 'attr.coverage_status', label: 'Investment Status', data_type: 'enum', description: 'Coverage workflow status' }
  api.getFieldRegistry.mockResolvedValue({ fields: [...fields.map(f => ({ ...f, description: 'Internal read model detail' })), status], column_field_keys: [...fields.map(f => f.field_key), status.field_key], filter_field_keys: [] })
  api.getWatchlistDetail.mockResolvedValue({ ...detail, views: [{ ...view, columns: ['instrument_name', 'currency', 'attr.coverage_status'] }] })
  show(); await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  const name = screen.getByRole('columnheader', { name: /Name/ })
  const investment = screen.getByRole('columnheader', { name: /Investment Status/ })
  expect(name.querySelector('.investment-studio-info-hint')).toBeNull()
  expect(investment.querySelector('.investment-studio-info-hint')).toBeNull()
  expect(investment.querySelector('.watchlists-th-content')?.textContent).toBe('Investment Status')
  const hint = screen.getByRole('columnheader', { name: /Currency/ }).querySelector('.investment-studio-info-hint')!
  fireEvent.click(hint)
  expect(await screen.findByText('Currency of prices and NAVs; returns are not converted to another currency.')).toBeTruthy()
  expect(screen.queryByText('Internal read model detail')).toBeNull()
})

it.each(['CSV', 'Excel'])('exports the full searched set in saved filter/sort order for %s, independent of rendered rows', async format => {
  const allRows = Array.from({ length: 269 }, (_, i) => ({ ...rows[0], instrument_id: `asset-${268 - i}`, instrument_name: `Asset ${268 - i}`, ticker_or_isin: `CODE-${268 - i}` }))
  api.getWatchlistDetail.mockResolvedValue({ ...detail, item_count: 269, views: [{ ...view, default_filters: { currency: ['USD'] }, default_sort: [{ field: 'instrument_name', direction: 'desc' }] }] })
  api.runScreenerQuery.mockImplementation(async () => result(allRows))
  const created = vi.fn((_blob: Blob) => 'blob:filtered-export')
  const oldCreate = URL.createObjectURL; const oldRevoke = URL.revokeObjectURL
  URL.createObjectURL = created; URL.revokeObjectURL = vi.fn()
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  try {
    show()
    await screen.findByRole('checkbox', { name: /Select Asset 268/ })
    for (const [query, count, first, last] of [['Asset', 269, 'asset-268', 'asset-0'], ['CODE-268', 1, 'asset-268', 'asset-268']] as const) {
      created.mockClear()
      fireEvent.change(screen.getByRole('searchbox', { name: 'Search this watchlist' }), { target: { value: query } })
      fireEvent.click(screen.getByRole('button', { name: 'Download' }))
      fireEvent.click(screen.getByRole('menuitem', { name: format }))
      await waitFor(() => expect(created).toHaveBeenCalledOnce())
      const file = await readExport(created.mock.calls[0][0], format === 'Excel')
      const exported = format === 'Excel' ? xlsxRows(file as ArrayBuffer) : csvRows(file as string)
      expect(exported).toHaveLength(count + 1)
      expect(exported[1][0]).toBe(first)
      expect(exported[exported.length - 1][0]).toBe(last)
    }
    expect(api.runScreenerQuery).toHaveBeenLastCalledWith(expect.objectContaining({ fetch_all: true, filters: { currency: ['USD'] }, sort: [{ field: 'instrument_name', direction: 'desc' }] }), undefined)
    created.mockClear()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search this watchlist' }), { target: { value: 'no-matching-instrument' } })
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    const option = screen.queryByRole('menuitem', { name: format })
    if (option) fireEvent.click(option)
    expect(created).not.toHaveBeenCalled()
  } finally { click.mockRestore(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke }
})


it('keeps metric date ranges on exactly the searched result set, including no matches', async () => {
  api.runScreenerQuery.mockResolvedValue({ ...result([
    { ...rows[0], instrument_name: 'Older company', metric_as_of_date: '2026-07-28' },
    { ...rows[1], instrument_name: 'Latest company', metric_as_of_date: '2026-10-13' },
  ]), snapshot_metadata: { as_of_date_min: '2026-07-28', as_of_date_max: '2026-10-13', has_mixed_as_of_dates: true } })
  show()
  await screen.findByText('Per-instrument metric as-of 2026-07-28 to 2026-10-13')
  const search = screen.getByRole('searchbox', { name: 'Search this watchlist' })
  fireEvent.change(search, { target: { value: 'Latest company' } })
  expect(screen.getByText('Metrics as of 2026-10-13')).toBeTruthy()
  expect(screen.queryByText('Per-instrument metric as-of 2026-07-28 to 2026-10-13')).toBeNull()
  fireEvent.change(search, { target: { value: 'does-not-exist' } })
  expect(screen.getByText('No matching instruments · no metric date range')).toBeTruthy()
  fireEvent.change(search, { target: { value: '' } })
  expect(screen.getByText('Per-instrument metric as-of 2026-07-28 to 2026-10-13')).toBeTruthy()
})

it('does not offer more rows when only collapsed groups hide records', async () => {
  api.getWatchlistDetail.mockResolvedValue({ ...detail, views: [{ ...view, default_group_by: 'currency' }] })
  show()
  await screen.findByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })
  const group = screen.getByRole('button', { name: /USD/ })
  fireEvent.click(group)
  expect(screen.getByText('2 rows in collapsed groups')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Show .*more/ })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull()
  fireEvent.click(group)
  expect(screen.getByRole('checkbox', { name: 'Select Alphabet Inc. · GOOG' })).toBeTruthy()
})
it('paginates expanded rows separately from collapsed rows and updates counts after searching', async () => {
  const many = Array.from({ length: 93 }, (_, i) => ({ ...rows[0], instrument_id: `asset-${i}`, instrument_name: `Asset ${i}`, ticker_or_isin: `CODE-${i}`, currency: i < 3 ? 'CNY' : 'USD' }))
  api.getWatchlistDetail.mockResolvedValue({ ...detail, views: [{ ...view, default_group_by: 'currency' }] })
  api.runScreenerQuery.mockResolvedValue(result(many))
  show()
  const group = await screen.findByRole('button', { name: /CNY/ })
  fireEvent.click(group)
  expect(screen.getByText('3 rows in collapsed groups')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Show 10 more' }))
  expect(screen.getByText('Showing 90 of 93 rows')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull()
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search this watchlist' }), { target: { value: 'CODE-92' } })
  await screen.findByText('Showing 1 of 1 matched rows')
  expect(screen.queryByText(/rows in collapsed groups/)).toBeNull()
})
