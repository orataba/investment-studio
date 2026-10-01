// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import AccountBoundary, { accountStorageKey } from './AccountBoundary'

beforeEach(() => { window.history.replaceState(null, '', '/watchlists/test?lang=zh-Hans') })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('opens local owner content directly and labels unrestricted local access without account links', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ user_id: 'shaw', display_name: 'Shaw', team_id: 'default', team_role: 'admin', local_unrestricted: true }) }))
  render(<LanguageProvider enableDomTranslation={false}><AccountBoundary><p>已有研究记录</p></AccountBoundary></LanguageProvider>)
  expect((await screen.findByText('Shaw')).parentElement).toBeTruthy()
  expect(screen.getByText('已有研究记录')).toBeTruthy()
  expect(screen.queryByRole('link')).toBeNull()
})

it('does not turn an unavailable identity service into a login prompt', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))
  render(<LanguageProvider enableDomTranslation={false}><AccountBoundary><p>已有研究记录</p></AccountBoundary></LanguageProvider>)
  expect(await screen.findByText('暂时无法确认账号权限')).toBeTruthy()
  expect(screen.getByRole('link', { name: '首页' })).toBeTruthy()
  expect(screen.queryByRole('link', { name: '前往登录' })).toBeNull()
  expect(screen.queryByText('已有研究记录')).toBeNull()
})

it('keeps the latest account when an older identity request finishes after a switch', async () => {
  let finishOld!: (response: unknown) => void
  const fetch = vi.fn()
    .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve }))
    .mockResolvedValueOnce({ ok: true, json: async () => ({ user_id: 'bob', display_name: 'Bob', team_id: 'default', team_role: 'reader' }) })
  vi.stubGlobal('fetch', fetch)
  render(<LanguageProvider enableDomTranslation={false}><AccountBoundary><p>研究记录</p></AccountBoundary></LanguageProvider>)
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect((await screen.findByText('Bob')).parentElement).toBeTruthy()
  await act(async () => {
    finishOld({ ok: true, json: async () => ({ user_id: 'alice', display_name: 'Alice', team_id: 'default', team_role: 'admin' }) })
  })
  expect(screen.queryByText('Alice · 团队协作')).toBeNull()
  expect(screen.getByText('Bob').parentElement).toBeTruthy()
  expect(accountStorageKey('views')).toBe('views:bob')
})

it('keeps the current page and unsaved input during a temporary identity outage, then retries', async () => {
  const identity = { ok: true, json: async () => ({ user_id: 'alice', display_name: 'Alice', team_id: 'default', team_role: 'admin' }) }
  const fetch = vi.fn().mockResolvedValueOnce(identity).mockResolvedValueOnce({ ok: false, status: 503 }).mockResolvedValueOnce(identity)
  vi.stubGlobal('fetch', fetch)
  render(<LanguageProvider enableDomTranslation={false}><AccountBoundary><input aria-label="Draft" defaultValue="" /></AccountBoundary></LanguageProvider>)
  const draft = await screen.findByLabelText('Draft')
  fireEvent.change(draft, { target: { value: 'Unsaved work' } })
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect(screen.getByLabelText('Draft')).toBe(draft)
  expect(draft).toHaveProperty('value', 'Unsaved work')
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await act(async () => {})
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByLabelText('Draft')).toBe(draft)
})

it('clears protected content when the server explicitly revokes the session', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ user_id: 'alice', display_name: 'Alice', team_id: 'default', team_role: 'admin' }) }).mockResolvedValueOnce({ ok: false, status: 401 }))
  render(<LanguageProvider enableDomTranslation={false}><AccountBoundary><p>Protected content</p></AccountBoundary></LanguageProvider>)
  await screen.findByText('Protected content')
  await act(async () => { window.dispatchEvent(new Event('studio:unauthorized')) })
  expect(screen.queryByText('Protected content')).toBeNull()
  expect(screen.getByRole('link', { name: '前往登录' })).toBeTruthy()
})

it('localizes recovery and retains the deep link and diagnostic id when retrying', async () => {
  window.history.replaceState(null, '', '/watchlists/qa?view=custom&lang=en')
  const identity = { ok: true, json: async () => ({ user_id: 'alice', display_name: 'Alice', team_id: 'default', team_role: 'admin' }) }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: false, status: 503, headers: new Headers({ 'X-Request-ID': 'diagnostic-123' }) }).mockResolvedValueOnce(identity))
  render(<LanguageProvider enableDomTranslation={false}><AccountBoundary><p>Target watchlist</p></AccountBoundary></LanguageProvider>)
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Unable to confirm account permissions right now.'))
  expect(screen.getByText('diagnostic-123')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Home' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByText('Target watchlist')
  expect(window.location.search).toBe('?view=custom&lang=en')
})
