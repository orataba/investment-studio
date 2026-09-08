import { afterEach, expect, it, vi } from 'vitest'
import { readResearchAssistant, researchAssistantUrl, uploadResearchAssistantFile, writeResearchAssistant } from './researchAssistantApi'

afterEach(() => vi.unstubAllGlobals())

it('reads live conversation state through the same-origin service without caching polling results', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"status":"running"}')).mockResolvedValueOnce(new Response('{"status":"draft"}'))
  vi.stubGlobal('fetch', fetcher)
  expect(await readResearchAssistant('/research/topics/chat-1')).toEqual({ status: 'running' })
  expect(await readResearchAssistant('/research/topics/chat-1')).toEqual({ status: 'draft' })
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(fetcher).toHaveBeenCalledWith('/api/research-assistant/topics/chat-1', expect.objectContaining({ credentials: 'include' }))
  expect(researchAssistantUrl('/api/research/entries/file-1/file')).toBe('/api/research-assistant/entries/file-1/file')
})

it('sends JSON and multipart files using the existing user session', async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response('{}'))
  vi.stubGlobal('fetch', fetcher)
  await writeResearchAssistant('/research/topics/chat-1/analysis', { question: '核对组合风险' })
  expect(fetcher).toHaveBeenLastCalledWith('/api/research-assistant/topics/chat-1/analysis', expect.objectContaining({ method: 'POST', body: '{"question":"核对组合风险"}', headers: { 'Content-Type': 'application/json' }, credentials: 'include' }))
  const file = new File(['example'], '资料.txt', { type: 'text/plain' })
  await uploadResearchAssistantFile('chat-1', file)
  const [url, init] = fetcher.mock.calls[fetcher.mock.calls.length - 1]
  expect(url).toBe('/api/research-assistant/topics/chat-1/files')
  expect(init.headers).toEqual({})
  expect((init.body as FormData).get('file')).toBe(file)
  expect(init.credentials).toBe('include')
})

it('shows downstream permission failures without treating them as empty conversation history', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"无权访问该组合"}', { status: 403 })))
  await expect(readResearchAssistant('/research/topics/chat-1')).rejects.toThrow('无权访问该组合')
})
