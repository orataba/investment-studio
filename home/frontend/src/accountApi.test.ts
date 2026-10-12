import { afterEach, describe, expect, it, vi } from 'vitest'
import { accountRequest, roleName } from './accountApi'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('account requests', () => {
  it('uses the current browser session and never caches identity data', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ display_name: '经理甲' }) })
    vi.stubGlobal('fetch', fetcher)
    expect(await accountRequest('/session')).toEqual({ display_name: '经理甲' })
    expect(fetcher).toHaveBeenCalledWith('/api/auth/session', expect.objectContaining({ credentials: 'same-origin', cache: 'no-store' }))
  })
  it('sends only the chosen member role and surfaces server authorization errors', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: '需要团队管理员权限。' }) })
    vi.stubGlobal('fetch', fetcher)
    await expect(accountRequest('/members/user-a', 'PATCH', { role: 'reader' })).rejects.toThrow('需要团队管理员权限。')
    expect(fetcher).toHaveBeenCalledWith('/api/auth/members/user-a', expect.objectContaining({ body: '{"role":"reader"}', method: 'PATCH' }))
    expect(roleName.reader).toBe('只读成员')
  })
  it('handles session revocation without expecting a response body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
    expect(await accountRequest('/logout-all', 'POST')).toBeUndefined()
  })
  it('bounds an unresponsive account read and leaves mutations outside that read deadline', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const read = expect(accountRequest('/session')).rejects.toThrow('读取超时')
    await vi.advanceTimersByTimeAsync(30_000)
    await read
    const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 204 })
    vi.stubGlobal('fetch', fetcher)
    await accountRequest('/members/member/reset', 'POST')
    expect(fetcher.mock.calls[0][1].signal).toBeUndefined()
  })
})
