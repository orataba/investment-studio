// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
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
