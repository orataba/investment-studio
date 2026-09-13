// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import RenameWatchlistDialog from './RenameWatchlistDialog'
import type { WatchlistRecord } from '../lib/api'

const mocks = vi.hoisted(() => ({ renameWatchlist: vi.fn() }))
vi.mock('../lib/api', () => ({ renameWatchlist: mocks.renameWatchlist }))
beforeEach(() => {
  // A preceding language-switch case persists its choice in storage and the
  // URL, both of which take precedence over the cookie on the next mount.
  window.history.replaceState({}, '', '?lang=en')
  document.cookie = 'investment_studio_language=en; path=/'
  vi.clearAllMocks()
})
afterEach(cleanup)
const watchlist: WatchlistRecord = { watchlist_id: 'stable-id', name: 'Risk', description: null,
  item_count: 3, owner_type: 'team', owner_id: 'default', is_default: false, is_shared: true, default_view_id: 'overview' }

it('saves the trimmed name against the existing ID and reports the saved record', async () => {
  const onSaved = vi.fn()
  mocks.renameWatchlist.mockResolvedValue({ ...watchlist, name: 'Focus' })
  render(<LanguageProvider><RenameWatchlistDialog watchlist={watchlist} onSaved={onSaved} onCancel={vi.fn()} /></LanguageProvider>)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '  Focus  ' } })
  fireEvent.submit(screen.getByRole('textbox').closest('form')!)
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ ...watchlist, name: 'Focus' }))
  expect(mocks.renameWatchlist).toHaveBeenCalledWith('stable-id', 'Focus')
})

it('preserves user text across languages and cancels without saving', async () => {
  const onCancel = vi.fn()
  render(<LanguageProvider><LanguageSelector /><RenameWatchlistDialog watchlist={watchlist} onSaved={vi.fn()} onCancel={onCancel} /></LanguageProvider>)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await screen.findByRole('button', { name: '取消' })
  expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('Risk')
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(onCancel).toHaveBeenCalledOnce()
  expect(mocks.renameWatchlist).not.toHaveBeenCalled()
})

it('retains the edited name after a failed save and permits retry', async () => {
  const onSaved = vi.fn()
  mocks.renameWatchlist.mockRejectedValueOnce(new Error('Save failed')).mockResolvedValueOnce({ ...watchlist, name: 'Focus' })
  render(<LanguageProvider enableDomTranslation={false}><RenameWatchlistDialog watchlist={watchlist} onSaved={onSaved} onCancel={vi.fn()} /></LanguageProvider>)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Focus' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Save failed')
  expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('Focus')
  expect(mocks.renameWatchlist).toHaveBeenCalledTimes(1)
  expect(onSaved).not.toHaveBeenCalled()
  const retryButton = await waitFor(() => {
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toHaveProperty('disabled', false)
    return button
  })
  fireEvent.click(retryButton)
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce())
  expect(onSaved).toHaveBeenCalledWith({ ...watchlist, name: 'Focus' })
  expect(mocks.renameWatchlist).toHaveBeenCalledTimes(2)
  expect(mocks.renameWatchlist).toHaveBeenNthCalledWith(1, 'stable-id', 'Focus')
  expect(mocks.renameWatchlist).toHaveBeenNthCalledWith(2, 'stable-id', 'Focus')
})
