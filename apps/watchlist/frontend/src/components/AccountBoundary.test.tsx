// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import AccountBoundary, { accountStorageKey } from './AccountBoundary'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('opens local owner content directly and labels unrestricted local access without account links', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ user_id: 'shaw', display_name: 'Shaw', team_id: 'default', team_role: 'admin', local_unrestricted: true }) }))
  render(<AccountBoundary><p>已有研究记录</p></AccountBoundary>)
  expect(await screen.findByText('Shaw · 本机全权限')).toBeTruthy()
  expect(screen.getByText('已有研究记录')).toBeTruthy()
  expect(screen.queryByRole('link')).toBeNull()
})

it('does not turn an unavailable identity service into a login prompt', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))
  render(<AccountBoundary><p>已有研究记录</p></AccountBoundary>)
  expect(await screen.findByText('暂时无法确认账号权限')).toBeTruthy()
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.queryByText('已有研究记录')).toBeNull()
})

it('keeps the latest account when an older identity request finishes after a switch', async () => {
  let finishOld!: (response: unknown) => void
  const fetch = vi.fn()
    .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve }))
    .mockResolvedValueOnce({ ok: true, json: async () => ({ user_id: 'bob', display_name: 'Bob', team_id: 'default', team_role: 'reader' }) })
  vi.stubGlobal('fetch', fetch)
  render(<AccountBoundary><p>研究记录</p></AccountBoundary>)
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect(await screen.findByText('Bob · 团队只读')).toBeTruthy()
  await act(async () => {
    finishOld({ ok: true, json: async () => ({ user_id: 'alice', display_name: 'Alice', team_id: 'default', team_role: 'admin' }) })
  })
  expect(screen.queryByText('Alice · 团队协作')).toBeNull()
  expect(screen.getByText('Bob · 团队只读')).toBeTruthy()
  expect(accountStorageKey('views')).toBe('views:bob')
})

it('keeps the current page and unsaved input during a temporary identity outage, then retries', async () => {
  const identity = { ok: true, json: async () => ({ user_id: 'alice', display_name: 'Alice', team_id: 'default', team_role: 'admin' }) }
  const fetch = vi.fn().mockResolvedValueOnce(identity).mockResolvedValueOnce({ ok: false, status: 503 }).mockResolvedValueOnce(identity)
  vi.stubGlobal('fetch', fetch)
  render(<AccountBoundary><input aria-label="Draft" defaultValue="" /></AccountBoundary>)
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
  render(<AccountBoundary><p>Protected content</p></AccountBoundary>)
  await screen.findByText('Protected content')
  await act(async () => { window.dispatchEvent(new Event('studio:unauthorized')) })
  expect(screen.queryByText('Protected content')).toBeNull()
  expect(screen.getByRole('link', { name: '前往登录' })).toBeTruthy()
})
