// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import WatchlistEntryPage from './WatchlistEntryPage'
import type { WatchlistRecord } from '../lib/api'

const mocks = vi.hoisted(() => ({ getWatchlists: vi.fn() }))
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  getWatchlists: mocks.getWatchlists,
}))
afterEach(cleanup)

it('counts the full instrument universe once when system and custom lists overlap', async () => {
  const lists: WatchlistRecord[] = [
    ['all-instruments', 244], ['index', 6], ['all-public-funds', 16],
    ['all-private-funds', 77], ['fund-a', 16], ['fund-b', 11], ['focus', 23],
  ].map(([id, count], index) => ({
    watchlist_id: String(id), name: String(id), description: null, item_count: Number(count),
    owner_type: index < 4 ? 'system' : 'team', owner_id: 'watchlist',
    is_default: index < 4, is_shared: true, default_view_id: null,
  }))
  mocks.getWatchlists.mockResolvedValue(lists)
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><WatchlistEntryPage /></MemoryRouter></LanguageProvider>)
  expect(await screen.findByText('7 Watchlists · 244 Securities')).toBeTruthy()
  expect(screen.queryByText('7 Watchlists · 343 Securities')).toBeNull()
})
