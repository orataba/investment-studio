// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import ResearchPage from './ResearchPage'
import InstrumentAssistantDrawer from '../components/InstrumentAssistantDrawer'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
const mocks = vi.hoisted(() => ({
  read: vi.fn(),
  write: vi.fn(),
  saveNote: vi.fn(),
  upload: vi.fn(),
}))
vi.mock('../lib/workbenchApi', async (original) => ({
  ...(await original<typeof import('../lib/workbenchApi')>()),
  readWorkbench: mocks.read,
  writeWorkbench: mocks.write,
  uploadTopicFile: mocks.upload,
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
  context_json: { tool_evidence: [] } as Record<string, unknown>,
}
let entries: Array<typeof answer & { source?: string }> = []
beforeEach(() => {
  entries = []
  vi.clearAllMocks()
  mocks.read.mockImplementation(async (path: string) => {
    if (path === '/research/topics' || path.startsWith('/research/topics?')) return [topic]
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
afterEach(() => { cleanup(); vi.useRealTimers() })

it('sends a free-form question from the current Watchlist without fixed analysis inputs', async () => {
  render(
    <MemoryRouter initialEntries={['/watchlists/3?assistant=1&instruments=fund-a,fund-b&currency=CNY&unrelated=value']}>
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
        page_context: { surface: 'watchlist', instrument_id: null, watchlist_id: '3', portfolio_id: null, currency: 'CNY' },
      },
    ),
  )
  expect(screen.queryByRole('button', { name: '指数增强比较' })).toBeNull()
  expect(mocks.write).toHaveBeenCalledWith('/research/topics', expect.objectContaining({ instrument_ids: ['fund-a', 'fund-b'] }))
  expect(mocks.saveNote).not.toHaveBeenCalled()
})

it('carries the selected prediction version into shared research and reports its publication', async () => {
  const reference = { instrument_id: 'fund-a', notebook_version_id: 'notebook-1', forecast_key: 'cash-return', forecast_version_id: 'forecast-1' }
  const updated = vi.fn()
  window.addEventListener(RESEARCH_UPDATED, updated)
  render(<MemoryRouter><InstrumentAssistantDrawer instrumentId="fund-a" question="这项预测有什么变化？" researchReference={reference} onClose={vi.fn()} /></MemoryRouter>)
  const send = await screen.findByRole('button', { name: '发送' })
  await waitFor(() => expect(send.hasAttribute('disabled')).toBe(false))
  mocks.write.mockImplementation(async (path: string) => {
    if (path === '/research/topics') return topic
    entries = [{ ...answer, context_json: { research_publication: { status: 'published', instrument_ids: ['fund-a'], message: '已修订预测依据。' } } }]
    return {}
  })
  fireEvent.click(send)
  await waitFor(() => expect(mocks.write).toHaveBeenCalledWith('/research/topics/chat-1/analysis', expect.objectContaining({ page_context: expect.objectContaining({ research_reference: reference }) })))
  expect(await screen.findByText('已更新共同研究记录。 已修订预测依据。')).toBeTruthy()
  await waitFor(() => expect(updated).toHaveBeenCalledOnce())
  expect((updated.mock.calls[0][0] as CustomEvent).detail).toEqual(['fund-a'])
  expect(mocks.saveNote).not.toHaveBeenCalled()
  window.removeEventListener(RESEARCH_UPDATED, updated)
})

it.each(['新对话', '历史对话'])('clears a selected research version when switching to %s', async (destination) => {
  const reference = { instrument_id: 'fund-a', theme_id: 'old-theme', research_update_id: 'old-event:1' }
  render(<MemoryRouter><ResearchPage instrumentId="fund-a" initialQuestion="核查这个事件" researchReference={reference} /></MemoryRouter>)
  await waitFor(() => expect(screen.getByRole('button', { name: '发送' }).hasAttribute('disabled')).toBe(false))
  expect(screen.getByText(/已关联研究追踪中的/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: destination }))
  if (destination === '历史对话') {
    fireEvent.click(within(screen.getByRole('navigation', { name: '历史对话' })).getByRole('button', { name: /基金研究/ }))
    await screen.findByText('重点标的：基金 A')
  }
  expect(screen.queryByText(/已关联研究追踪中的/)).toBeNull()
  fireEvent.change(screen.getByRole('textbox', { name: '向研究助手提问' }), { target: { value: '研究一个新问题' } })
  await waitFor(() => expect(screen.getByRole('button', { name: '发送' }).hasAttribute('disabled')).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(mocks.write).toHaveBeenCalledWith('/research/topics/chat-1/analysis', expect.objectContaining({
    question: '研究一个新问题',
    page_context: { surface: 'instrument', instrument_id: 'fund-a', watchlist_id: null, portfolio_id: null },
  })))
})

it.each([
  { event_case_id: 'event-1', event_version_id: 'event-1:2' },
  { risk_case_id: 'drawdown-1', risk_case_updated_at: '2026-09-12T08:00:00Z' },
])('carries the selected risk reference from a list entry to the shared assistant: %j', async (fields) => {
  const params = new URLSearchParams({ instruments: 'fund-a', watchlist: '3', question: '核查所选风险' })
  for (const [key, value] of Object.entries(fields)) if (value) params.set(key, value)
  render(<MemoryRouter initialEntries={[`/assistant?${params}`]}><ResearchPage /></MemoryRouter>)
  await waitFor(() => expect(screen.getByRole('button', { name: '发送' }).hasAttribute('disabled')).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(mocks.write).toHaveBeenCalledWith('/research/topics/chat-1/analysis', expect.objectContaining({
    page_context: expect.objectContaining({ surface: 'watchlist', watchlist_id: '3', research_reference: { instrument_id: 'fund-a', ...fields } }),
  })))
})

it('continues an existing conversation and saves a reply as a note only after editing and submission', async () => {
  entries = [{ ...answer, context_json: { page_context: { research_reference: { instrument_id: 'fund-a', theme_id: 'theme-original', pm_note_id: 'pm-original', pm_note_revision: 2, research_update_id: 'event:1:2', event_case_id: 'event-1', event_version_id: 'event-1:2' } } } }]
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
        source_entry_id: 'reply-1',
        note: expect.objectContaining({
          note_type: 'thesis_update',
          research_context: expect.objectContaining({ background: '当时讨论的问题：这只基金的主要风险是什么？', theme_id: 'theme-original', relationship: 'update', related_note_id: 'pm-original', related_revision: 2, research_update_id: 'event:1:2', event_case_id: 'event-1', event_version_id: 'event-1:2' }),
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
      { question: '那下一步该核查什么？', watchlist_id: null, page_context: { surface: 'watchlist', instrument_id: 'fund-a', watchlist_id: null, portfolio_id: null } },
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

it('prefills the current instrument and question without sending, and filters unrelated or automatic conversations', async () => {
  const defaultRead = mocks.read.getMockImplementation()!
  mocks.read.mockImplementation(async (path: string) => path.startsWith('/research/topics?') ? [
    topic,
    { ...topic, topic_id: 'other', title: '其他标的', instrument_ids: ['fund-b'] },
    { ...topic, topic_id: 'mixed', title: '多个标的', instrument_ids: ['fund-a', 'fund-b'] },
    { ...topic, topic_id: 'archived', title: '已归档会话', status: 'archived' },
    { ...topic, topic_id: 'us-sector-daily-review', title: '自动行业检查' },
    { ...topic, topic_id: 'instrument-events:fund-a', title: '自动标的检查' },
  ] : defaultRead(path))
  render(<MemoryRouter initialEntries={['/instruments/fund-a?instruments=fund-b&question=旧问题&tab=performance&currency=USD&benchmark=sp500&start=2026-01-01&end=2026-09-06&unrelated=value']}>
    <InstrumentAssistantDrawer instrumentId="fund-a" watchlistId="3" question="把基金 A 与基金 B 比较" onClose={vi.fn()} />
  </MemoryRouter>)
  expect((await screen.findByRole('textbox', { name: '向研究助手提问' }) as HTMLTextAreaElement).value).toBe('把基金 A 与基金 B 比较')
  expect(await screen.findByText('重点标的：基金 A')).toBeTruthy()
  expect(mocks.read).toHaveBeenCalledWith('/research/topics?instrument_id=fund-a')
  expect(mocks.write).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '历史对话' }))
  const history = screen.getByRole('navigation', { name: '历史对话' })
  expect(within(history).getAllByRole('button')).toHaveLength(1)
  expect(within(history).getByText('基金研究')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(mocks.write).toHaveBeenCalledWith('/research/topics', expect.objectContaining({ instrument_ids: ['fund-a'] })))
  expect(mocks.write).toHaveBeenCalledWith('/research/topics/chat-1/analysis', {
    question: '把基金 A 与基金 B 比较', watchlist_id: '3',
    page_context: { surface: 'instrument', instrument_id: 'fund-a', watchlist_id: '3', portfolio_id: null,
      tab: 'performance', currency: 'USD', benchmark: 'sp500', start: '2026-01-01', end: '2026-09-06' },
  })
})

it('uses the existing Portfolio entry query as the page context while retaining its instrument focus', async () => {
  render(<MemoryRouter initialEntries={['/assistant?portfolio=p-1&instruments=fund-a&watchlist=3&tab=risk&currency=USD&question=分析当前持仓风险']}>
    <ResearchPage />
  </MemoryRouter>)
  expect(await screen.findByText('重点标的：基金 A')).toBeTruthy()
  expect(screen.getByText('组合 p-1 · DeepSeek')).toBeTruthy()
  expect(screen.getByRole('link', { name: '返回组合' }).getAttribute('href')).toContain('/portfolios/p-1/risk')
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(mocks.write).toHaveBeenCalledWith('/research/topics/chat-1/analysis', {
    question: '分析当前持仓风险', watchlist_id: '3',
    page_context: { surface: 'portfolio', instrument_id: 'fund-a', watchlist_id: '3', portfolio_id: 'p-1', tab: 'risk', currency: 'USD' },
  }))
  expect(mocks.write).toHaveBeenCalledWith('/research/topics', expect.objectContaining({ instrument_ids: ['fund-a'], portfolio_id: 'p-1' }))
})

it('uploads material into a new scoped conversation without calling analysis', async () => {
  const file = new File(['材料正文'], 'manager-notes.txt', { type: 'text/plain' })
  mocks.upload.mockImplementation(async () => {
    const material = { ...answer, entry_id: 'material-1', kind: 'material', title: file.name, body: '', status: 'recorded', source: '/api/research/entries/material-1/file', context_json: { file_name: file.name, extraction: '已提取文本' } }
    entries = [material]
    return material
  })
  render(<MemoryRouter><InstrumentAssistantDrawer instrumentId="fund-a" question="结合材料核查策略" onClose={vi.fn()} /></MemoryRouter>)
  await screen.findByText('重点标的：基金 A')
  fireEvent.change(screen.getByLabelText('补充对话材料'), { target: { files: [file] } })
  await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith('chat-1', file))
  expect(mocks.write).toHaveBeenCalledWith('/research/topics', expect.objectContaining({ instrument_ids: ['fund-a'] }))
  expect(mocks.write.mock.calls.some(([path]) => path.endsWith('/analysis'))).toBe(false)
  expect(await screen.findByText('manager-notes.txt')).toBeTruthy()
  expect(screen.getByRole('link', { name: '查看原件' }).getAttribute('href')).toContain('/api/research/entries/material-1/file')
  expect((screen.getByRole('textbox', { name: '向研究助手提问' }) as HTMLTextAreaElement).value).toBe('结合材料核查策略')
  expect(screen.getByText('材料已收录，发送问题后才会开始分析。')).toBeTruthy()
})

it('shows real queued, running and failed states and polls only while a reply is active', async () => {
  vi.useFakeTimers()
  entries = [{ ...answer, status: 'queued' }]
  render(<MemoryRouter initialEntries={['/assistant?topic=chat-1']}><ResearchPage instrumentId="fund-a" /></MemoryRouter>)
  await act(async () => {})
  expect(screen.getByText('问题已排队，等待回复。')).toBeTruthy()
  expect((screen.getByRole('button', { name: '回复中…' }) as HTMLButtonElement).disabled).toBe(true)
  entries = [{ ...answer, status: 'running' }]
  await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
  expect(screen.getByText('正在理解问题并查阅资料…')).toBeTruthy()
  entries = [{ ...answer, status: 'failed', body: '公开资料暂时无法读取。' }]
  await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
  expect(screen.getByText('DeepSeek · 回复未完成')).toBeTruthy()
  expect(screen.getByText('公开资料暂时无法读取。')).toBeTruthy()
  const reads = mocks.read.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(8000) })
  expect(mocks.read.mock.calls.length).toBe(reads)
})

it('shows readable public links and source dates, including incomplete searches', async () => {
  entries = [{ ...answer, context_json: { tool_evidence: [
    { source_id: 'search-1', tool: 'search', retrieved_at: '2026-09-06T08:00:00+08:00', result: { sources: [{ source_id: 'news-1', title: '公司最新公告', url: 'https://example.com/announcement', published_at: '2026-09-05' }] } },
    { source_id: 'search-2', tool: 'search', retrieved_at: '2026-09-06T08:00:00+08:00', result: { available: false, reason: '检索暂不可用', limitation: '未能覆盖最新公开信息' } },
  ] } }]
  render(<MemoryRouter initialEntries={['/assistant?topic=chat-1']}><ResearchPage /></MemoryRouter>)
  fireEvent.click(await screen.findByText('查阅依据（2）'))
  expect(screen.getByRole('link', { name: '公司最新公告' }).getAttribute('href')).toBe('https://example.com/announcement')
  expect(screen.getByText('原文发布 2026-09-05')).toBeTruthy()
  expect(screen.getByText('公开信息检索 · 读取未完成')).toBeTruthy()
  expect(screen.getByText('未能覆盖最新公开信息')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /^对话说明:/ }))
  expect(screen.getByText('已保存的答复反映当时查阅的资料，并非实时更新。')).toBeTruthy()
})

function CurrentQuery() { return <output data-testid="query">{useLocation().search}</output> }

it('isolates a different instrument and clears only assistant query parameters on Escape', async () => {
  const close = vi.fn()
  entries = [answer]
  const view = (instrumentId: string, question: string) => <MemoryRouter initialEntries={['/instruments/fund-a?tab=performance&currency=USD&watchlist=3&assistant=1&topic=chat-1&question=old&instruments=fund-a']}>
    <InstrumentAssistantDrawer instrumentId={instrumentId} question={question} onClose={close} /><CurrentQuery />
  </MemoryRouter>
  const { rerender } = render(view('fund-a', '基金 A 问题'))
  await screen.findByText('重点标的：基金 A')
  expect(await screen.findByText('证据不足')).toBeTruthy()
  rerender(view('fund-b', '基金 B 问题'))
  await screen.findByText('重点标的：fund-b')
  expect((screen.getByRole('textbox', { name: '向研究助手提问' }) as HTMLTextAreaElement).value).toBe('基金 B 问题')
  expect(screen.queryByText('重点标的：基金 A')).toBeNull()
  expect(screen.queryByText('证据不足')).toBeNull()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(close).toHaveBeenCalledOnce()
  expect(screen.getByTestId('query').textContent).toBe('?tab=performance&currency=USD&watchlist=3')
})


it('keeps Portfolio assistant history and association inside the originating portfolio', async () => {
  const defaultRead = mocks.read.getMockImplementation()!
  mocks.read.mockImplementation(async (path: string) => {
    if (path === '/research/topics') return [
      { ...topic, topic_id: 'p1-chat', title: '当前组合历史', portfolio_id: 'p-1' },
      { ...topic, topic_id: 'p2-chat', title: '其他组合历史', portfolio_id: 'p-2' },
      topic,
    ]
    if (path === '/research/connections') return { assistant_available: true, portfolios: [
      { portfolio_id: 'p-1', portfolio_name: '组合一' }, { portfolio_id: 'p-2', portfolio_name: '组合二' },
    ] }
    return defaultRead(path)
  })
  render(<MemoryRouter initialEntries={['/assistant?portfolio=p-1']}><ResearchPage /></MemoryRouter>)
  await screen.findByText('组合一 · DeepSeek')
  expect((screen.getByRole('combobox', { name: '关联组合' }) as HTMLSelectElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '历史对话' }))
  const history = screen.getByRole('navigation', { name: '历史对话' })
  expect(within(history).getAllByRole('button')).toHaveLength(1)
  expect(within(history).getByText('当前组合历史')).toBeTruthy()
})

it('resets a new conversation to page scope instead of retaining a historical conversation association', async () => {
  const defaultRead = mocks.read.getMockImplementation()!
  mocks.read.mockImplementation(async (path: string) => path === '/research/topics/chat-1'
    ? { topic: { ...topic, portfolio_id: 'p-other' }, entries: [answer] } : defaultRead(path))
  render(<MemoryRouter initialEntries={['/assistant?topic=chat-1&instruments=fund-b']}><ResearchPage /></MemoryRouter>)
  await screen.findByText('证据不足')
  fireEvent.click(screen.getByRole('button', { name: '新对话' }))
  expect(await screen.findByText('重点标的：fund-b')).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: '向研究助手提问' }), { target: { value: '分析当前标的' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(mocks.write).toHaveBeenCalledWith('/research/topics', expect.objectContaining({
    instrument_ids: ['fund-b'], portfolio_id: null,
  })))
})


it('shows committed user records and refreshes their instrument even when the later answer fails', async () => {
  const updated = vi.fn()
  window.addEventListener(RESEARCH_UPDATED, updated)
  entries = [{ ...answer, status: 'failed', body: '后续分析未完成', context_json: {
    user_records: [{ kind: 'investment_view', instrument_id: 'fund-a', id: 'pm-1', title: '我的流动性判断' }],
  } }]
  render(<MemoryRouter initialEntries={['/assistant?topic=chat-1']}><ResearchPage /></MemoryRouter>)
  expect(await screen.findByText('已保存你的投资观点：我的流动性判断')).toBeTruthy()
  expect(screen.getByText('后续分析未完成')).toBeTruthy()
  await waitFor(() => expect(updated).toHaveBeenCalledOnce())
  expect((updated.mock.calls[0][0] as CustomEvent).detail).toEqual(['fund-a'])
  expect(mocks.saveNote).not.toHaveBeenCalled()
  window.removeEventListener(RESEARCH_UPDATED, updated)
})
