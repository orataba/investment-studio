// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import WatchlistEntryPage from './WatchlistEntryPage'
import type { WatchlistRecord } from '../lib/api'

const mocks = vi.hoisted(() => ({ getWatchlists: vi.fn() }))
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  getWatchlists: mocks.getWatchlists,
}))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('shows the shared floating loader while the directory loads, without title status text', async () => {
  let resolve!: (lists: WatchlistRecord[]) => void
  mocks.getWatchlists.mockReturnValue(new Promise<WatchlistRecord[]>((done) => { resolve = done }))
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><WatchlistEntryPage /></MemoryRouter></LanguageProvider>)
  const status = screen.getByRole('status')
  expect(status.classList.contains('watchlist-loading-overlay')).toBe(true)
  expect(within(screen.getByRole('heading', { name: 'All Watchlists' }).parentElement!).queryByText('Loading')).toBeNull()
  await act(async () => resolve([]))
  expect(screen.queryByRole('status')).toBeNull()
  expect(screen.getByText('0 Watchlists · 0 Securities')).toBeTruthy()
})

it('aborts a directory load when leaving the entry page', () => {
  mocks.getWatchlists.mockReturnValue(new Promise(() => {}))
  const { unmount } = render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><WatchlistEntryPage /></MemoryRouter></LanguageProvider>)
  const signal = mocks.getWatchlists.mock.calls[0][0] as AbortSignal
  expect(signal.aborted).toBe(false)
  unmount()
  expect(signal.aborted).toBe(true)
})

it('counts the full instrument universe once when system and custom lists overlap', async () => {
  const lists: WatchlistRecord[] = [
    ['all-instruments', 244], ['index', 6], ['all-public-funds', 16],
    ['all-private-funds', 77], ['fund-a', 16], ['fund-b', 11], ['focus', 23],
  ].map(([id, count], index) => ({
    watchlist_id: String(id), name: String(id), description: null, item_count: Number(count),
    owner_type: index < 4 ? 'system' : 'team', owner_id: 'watchlist',
    created_by_user_id: index === 4 ? 'alice' : null, created_by_display_name: index === 4 ? 'Alice' : null,
    is_default: index < 4, is_shared: true, default_view_id: null,
  }))
  mocks.getWatchlists.mockResolvedValue(lists)
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><WatchlistEntryPage /></MemoryRouter></LanguageProvider>)
  expect(await screen.findByText('7 Watchlists · 244 Securities')).toBeTruthy()
  expect(screen.queryByText('7 Watchlists · 343 Securities')).toBeNull()
  expect(screen.getByText(/Created by Alice|创建人 Alice/)).toBeTruthy()
  expect(screen.getAllByText(/Creator not recorded|创建人未记录/)).toHaveLength(2)
})
