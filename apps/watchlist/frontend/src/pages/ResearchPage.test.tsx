// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import ResearchPage from './ResearchPage'
const mocks = vi.hoisted(() => ({
  read: vi.fn(),
  write: vi.fn(),
  saveNote: vi.fn(),
}))
vi.mock('../lib/workbenchApi', async (original) => ({
  ...(await original<typeof import('../lib/workbenchApi')>()),
  readWorkbench: mocks.read,
  writeWorkbench: mocks.write,
}))
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  createInstrumentResearchNote: mocks.saveNote,
}))
const topic = {
  topic_id: 'chat-1',
  title: '基金研究',
  instrument_ids: ['fund-a'],
  portfolio_id: null,
  updated_at: '2026-09-05T00:00:00Z',
}
const answer = {
  entry_id: 'reply-1',
  kind: 'analysis',
  title: '这只基金的主要风险是什么？',
  body: '**证据不足**，需要核查。',
  status: 'draft',
  created_at: '2026-09-05T00:00:00Z',
  context_json: { tool_evidence: [] },
}
let entries: (typeof answer)[] = []
beforeEach(() => {
  entries = []
  vi.clearAllMocks()
  mocks.read.mockImplementation(async (path: string) => {
    if (path === '/research/topics') return [topic]
    if (path === '/research/catalogue')
      return { instruments: [{ instrument_id: 'fund-a', name: '基金 A' }] }
    if (path === '/research/connections')
      return { assistant_available: true, portfolios: [] }
    return { topic, entries }
  })
  mocks.write.mockImplementation(async (path) =>
    path === '/research/topics' ? topic : {},
  )
  mocks.saveNote.mockResolvedValue({})
})
afterEach(cleanup)

it('sends a free-form question from the current Watchlist without fixed analysis inputs', async () => {
  render(
    <MemoryRouter initialEntries={['/watchlists/3?assistant=1']}>
      <ResearchPage watchlistId="3" />
    </MemoryRouter>,
  )
  const input = screen.getByRole('textbox', { name: '向研究助手提问' })
  fireEvent.change(input, {
    target: { value: '帮我检查当前名单，先找出需要补充资料的标的。' },
  })
  const send = screen.getByRole('button', { name: '发送' })
  await waitFor(() => expect(send.hasAttribute('disabled')).toBe(false))
  fireEvent.click(send)
  await waitFor(() =>
    expect(mocks.write).toHaveBeenCalledWith(
      '/research/topics/chat-1/analysis',
      {
        question: '帮我检查当前名单，先找出需要补充资料的标的。',
        watchlist_id: '3',
      },
    ),
  )
  expect(screen.queryByRole('button', { name: '指数增强比较' })).toBeNull()
  expect(mocks.saveNote).not.toHaveBeenCalled()
})

it('continues an existing conversation and saves a reply as a note only after editing and submission', async () => {
  entries = [answer]
  render(
    <MemoryRouter initialEntries={['/assistant?topic=chat-1']}>
      <ResearchPage />
    </MemoryRouter>,
  )
  expect(await screen.findByText('证据不足')).toBeTruthy()
  expect(mocks.saveNote).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '整理为标的笔记' }))
  fireEvent.change(screen.getByRole('textbox', { name: '研究记录' }), {
    target: { value: '人工核查后仍待补充底层敞口。' },
  })
  fireEvent.click(screen.getByRole('button', { name: '保存笔记' }))
  await waitFor(() =>
    expect(mocks.saveNote).toHaveBeenCalledWith(
      'fund-a',
      expect.objectContaining({
        note: expect.objectContaining({
          body: '人工核查后仍待补充底层敞口。',
          source_refs: 'Watchlist 助手对话 chat-1 / 回复 reply-1',
        }),
      }),
    ),
  )
  fireEvent.change(screen.getByRole('textbox', { name: '向研究助手提问' }), {
    target: { value: '那下一步该核查什么？' },
  })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() =>
    expect(mocks.write).toHaveBeenCalledWith(
      '/research/topics/chat-1/analysis',
      { question: '那下一步该核查什么？', watchlist_id: null },
    ),
  )
})

it('closes the drawer from its backdrop, while clicks inside stay open', async () => {
  const close = vi.fn()
  render(<MemoryRouter><ResearchPage watchlistId="3" onClose={close} /></MemoryRouter>)
  const dialog = screen.getByRole('dialog')
  fireEvent.click(screen.getByRole('textbox', { name: '向研究助手提问' }))
  expect(close).not.toHaveBeenCalled()
  fireEvent.click(dialog.parentElement!)
  expect(close).toHaveBeenCalledOnce()
})
