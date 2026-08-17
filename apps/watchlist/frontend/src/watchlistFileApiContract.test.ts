import { afterEach, describe, expect, it, vi } from 'vitest'

import { resolveSharedInstrumentsFile } from './lib/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('watchlist identifier file API', () => {
  it('uploads CSV or Excel as the original multipart file', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({ results: [] }), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['xlsx'], 'identifiers.xlsx')

    await resolveSharedInstrumentsFile(file)

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit
    const form = request.body as FormData
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/instruments/resolve-file')
    expect(request.method).toBe('POST')
    expect(request.headers).toBeUndefined()
    expect(form.get('file')).toBe(file)
  })
})
