// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchThemesPanel from './ResearchThemesPanel'
import { announceResearchPublication } from '../lib/researchUpdates'
import type { ResearchTheme, ResearchUpdate } from '../lib/researchDossierApi'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import { researchMessages, researchPatterns } from '../researchMessages'

const api = vi.hoisted(() => ({ get: vi.fn(), detail: vi.fn(), create: vi.fn(), update: vi.fn(), source: vi.fn(), note: vi.fn(), manualNote: vi.fn(), remove: vi.fn() }))
vi.mock('../lib/researchDossierApi', async importOriginal => ({ ...await importOriginal<typeof import('../lib/researchDossierApi')>(), getResearchThemes: api.get, getResearchTheme: api.detail, createResearchTheme: api.create, updateResearchTheme: api.update, getSavedResearchSource: api.source, addResearchThemeNote: api.manualNote, deleteResearchTheme: api.remove }))
vi.mock('../lib/api', async importOriginal => ({ ...await importOriginal<typeof import('../lib/api')>(), createInstrumentResearchNote: api.note }))
const identity = { user_id: 'pm-1', display_name: 'Shaw', mode: 'account', team_role: 'member' }
const theme = (overrides: Partial<ResearchTheme> = {}): ResearchTheme => ({
  theme_id: 'theme-credit', source_version_id: 'theme:theme-credit:1', instrument_id: 'gold', title: '美元信用与黄金', question: '长债收益率上升何时转变为信用担忧？', background: '名义收益率与黄金同步上升。', status: 'active', author_user_id: identity.user_id, author: identity.display_name,
  created_at: '2026-09-05T08:00:00Z', updated_at: '2026-09-05T08:00:00Z', revision_number: 1, notes: [], research_progress: [], kind: 'fundamental', priority: 'core', priority_reason: '影响黄金的中期定价机制', synthesis: '当前信用担忧尚未取代利率影响。', next_check: '对照后续实际利率与储备需求。', baseline_status: 'ready', ...overrides,
})
beforeEach(() => { vi.resetAllMocks(); api.detail.mockImplementation(async (instrumentId: string, id: string) => (await api.get.getMockImplementation()!(instrumentId)).themes.find((item: ResearchTheme) => item.theme_id === id)); api.get.mockResolvedValue({ identity, themes: [], active_limit: 10, target_count: 5 }) })
afterEach(() => { cleanup(); window.history.replaceState(null, '', '/') })

it('creates a title-only theme with a chosen research type and lets the backend schedule its baseline', async () => {
  api.create.mockResolvedValue(theme({ research_run_id: 'baseline-run', research_status: 'queued', research_message: '已安排主题基线研究。' }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无重要研究主题/)
  fireEvent.click(screen.getByRole('button', { name: '添加主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '利率与黄金的条件关系' } })
  fireEvent.change(screen.getByLabelText('研究类型'), { target: { value: 'quantitative' } })
  api.get.mockResolvedValue({ identity, themes: [theme({ title: '利率与黄金的条件关系', kind: 'quantitative', synthesis: '', baseline_status: 'pending' })] })
  fireEvent.click(screen.getByRole('button', { name: '创建主题' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledWith('gold', expect.objectContaining({ title: '利率与黄金的条件关系', kind: 'quantitative', question: '', responsible_user_id: identity.user_id })))
  expect(await screen.findByRole('heading', { name: '利率与黄金的条件关系' })).toBeTruthy()
  expect(screen.getByText('待建立研究基线。')).toBeTruthy()
  expect(screen.getByRole('status').textContent).toContain('已安排主题基线研究。')
})

it('shows synthesized priorities without surfacing old independent research questions', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme({ current_questions: [{ update_id: 'old-question', kind: 'question', title: '旧问题名称', body: '旧问题判断', recorded_at: '2026-09-01T00:00:00Z', author: '研究员', author_role: 'researcher', theme_ids: [], sources: [], reference: { instrument_id: 'gold' } }] })] })
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  await openTheme(theme().title)
  expect(screen.getAllByText('核心').length).toBeGreaterThan(0)
  expect(screen.queryByText('旧问题名称')).toBeNull()
  expect(screen.getAllByText(theme().synthesis!)).toHaveLength(1)
  expect(screen.getAllByText(theme().priority_reason!).length).toBeGreaterThan(0)
  expect(screen.getByText('管理主题').closest('details')?.open).toBe(false)
  fireEvent.click(screen.getByText('管理主题'))
  expect(screen.getByRole('button', { name: '结束主题跟踪' }).closest('.research-theme-expanded')).toBeNull()
})

it('preserves PM authorship and exact historical references in an actual theme timeline', async () => {
  const opinion: ResearchUpdate = { update_id: 'opinion:pm-1:2', kind: 'opinion', title: '中期信用担忧', body: '中期偏多黄金', recorded_at: '2026-09-05T10:00:00Z', author: 'Shaw', author_role: 'user', theme_ids: ['theme-credit'], sources: [], reference: { instrument_id: 'gold', theme_id: 'theme-credit', pm_note_id: 'pm-1', pm_note_revision: 2 } }
  const update: ResearchUpdate = { ...opinion, update_id: 'question:q1:1', kind: 'question', title: '实际利率仍占主导', author: '研究员', author_role: 'researcher', recorded_at: '2026-09-06T08:00:00Z', reference: { instrument_id: 'gold', theme_id: 'theme-credit', notebook_version_id: 'n1' } }
  api.get.mockResolvedValue({ identity, themes: [theme({ updates: [opinion, update] })] })
  const ask = vi.fn()
  const { container } = render(<ResearchThemesPanel instrumentId="gold" onAskAssistant={ask} />)
  await screen.findByRole('heading', { name: theme().title })
  await openTheme(theme().title)
  expect(container.querySelector('.research-timeline')).toBeTruthy()
  const timeline = container.querySelector('.research-timeline')!
  expect(screen.getByRole('heading', { name: '当前认识' })).toBeTruthy()
  expect(screen.queryByText(/判断更新/)).toBeNull()
  expect(timeline.closest('.research-theme-expanded')).toBeTruthy()
  expect(timeline.querySelectorAll('time')).toHaveLength(2)
  expect([...timeline.querySelectorAll('[data-update-id]')].map(node => node.getAttribute('data-update-id'))).toEqual([update.update_id, opinion.update_id])
  const pm = within(timeline as HTMLElement).getByRole('heading', { name: opinion.title }).closest('article')!
  expect(within(pm).getByText('人工判断')).toBeTruthy()
  expect(within(pm).getByText(opinion.body).closest('.research-theme-expanded')).toBeTruthy()
  fireEvent.click(await within(pm).findByRole('button', { name: '追问这条更新' }))
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), { ...opinion.reference, research_update_id: opinion.update_id })
  fireEvent.click(screen.getByRole('button', { name: '讨论主题' }))
  expect(ask).toHaveBeenLastCalledWith(expect.stringContaining(theme().synthesis!), { instrument_id: 'gold', theme_id: 'theme-credit', theme_version_id: 'theme:theme-credit:1', source_ids: [] })
})

it('uses the theme revision for its figures, saved evidence and PM context without needing a notebook', async () => {
  const source = { source_id: 'computed:theme', source_type: 'computed_metric' as const, title: '主题独立量化结果' }
  api.get.mockResolvedValue({ identity, themes: [theme({ source_version_id: 'theme:theme-credit:3', sources: [source], figure_source_ids: [source.source_id] })] })
  api.source.mockResolvedValue({ ...source, data: { rows: [{ instrument_id: 'gold', return_pct: 3 }] } })
  api.note.mockResolvedValue({})
  const ask = vi.fn()
  render(<ResearchThemesPanel instrumentId="gold" onAskAssistant={ask} />)
  await screen.findByRole('heading', { name: theme().title })
  await openTheme(theme().title)
  fireEvent.click(await screen.findByRole('button', { name: '讨论这张图表' }))
  expect(api.source).toHaveBeenCalledWith('gold', source.source_id, expect.any(AbortSignal), 'theme:theme-credit:3', 'figure')
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), { instrument_id: 'gold', theme_id: 'theme-credit', theme_version_id: 'theme:theme-credit:3', source_ids: [source.source_id] })
  fireEvent.click(screen.getByRole('button', { name: '主题背景与证据' }))
  fireEvent.click(screen.getByText('查看已保存的计算依据'))
  await waitFor(() => expect(api.source).toHaveBeenCalledTimes(2))
  expect(api.source).toHaveBeenLastCalledWith('gold', source.source_id, expect.any(AbortSignal), 'theme:theme-credit:3')
  fireEvent.keyDown(document, { key: 'Escape' })
  fireEvent.click(screen.getByRole('button', { name: '写投资观点' }))
  fireEvent.change(screen.getByLabelText('我的投资观点'), { target: { value: '基于当前主题版本继续观察。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存投资观点' }))
  await waitFor(() => expect(api.note).toHaveBeenCalled())
  const context = api.note.mock.calls[0][1].note.research_context
  expect(context).toEqual(expect.objectContaining({ theme_id: 'theme-credit', theme_version_id: 'theme:theme-credit:3', source_ids: [source.source_id] }))
  expect(context.notebook_version_id).toBeUndefined()
})

it('reports an unavailable research runtime without claiming a pending baseline is running', async () => {
  const message = '主题已保存；研究运行环境尚不可用，请稍后发起研究。'
  api.create.mockResolvedValue(theme({ research_status: 'unavailable', research_message: message }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无重要研究主题/)
  fireEvent.click(screen.getByRole('button', { name: '添加主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '新主题' } })
  api.get.mockResolvedValue({ identity, themes: [theme({ synthesis: '', baseline_status: 'pending' })] })
  fireEvent.click(screen.getByRole('button', { name: '创建主题' }))
  expect(await screen.findByText(message)).toBeTruthy()
  expect(await screen.findByText('待建立研究基线。')).toBeTruthy()
  expect(screen.queryByText('研究员正在建立主题基线。')).toBeNull()
})

it('keeps an open opinion bound to the theme version and evidence that formed its background', async () => {
  const original = theme({ sources: [{ source_id: 'original-source', title: '原始依据' }] })
  api.get.mockResolvedValue({ identity, themes: [original] })
  api.note.mockResolvedValue({})
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: original.title })
  await openTheme(original.title)
  fireEvent.click(screen.getByRole('button', { name: '写投资观点' }))
  fireEvent.change(screen.getByLabelText('我的投资观点'), { target: { value: '依照当时资料保留判断。' } })
  api.get.mockResolvedValue({ identity, themes: [theme({ title: '更新后的主题', source_version_id: 'theme:theme-credit:2', synthesis: '研究已形成新认识。', sources: [{ source_id: 'new-source' }] })] })
  await act(async () => announceResearchPublication(['gold']))
  await screen.findByRole('heading', { name: '更新后的主题' })
  fireEvent.click(screen.getByRole('button', { name: '保存投资观点' }))
  await waitFor(() => expect(api.note).toHaveBeenCalledWith('gold', expect.objectContaining({ note: expect.objectContaining({
    title: `关于${original.title}的观点`,
    research_context: expect.objectContaining({ theme_version_id: original.source_version_id, source_ids: ['original-source'], background: expect.stringContaining(original.synthesis!) }),
  }) })))
})

it('does not let a completed save for a previous instrument erase the current theme draft', async () => {
  let finish!: (value: ResearchTheme) => void
  api.create.mockReturnValue(new Promise<ResearchTheme>(resolve => { finish = resolve }))
  const { rerender } = render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无重要研究主题/)
  fireEvent.click(screen.getByRole('button', { name: '添加主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '黄金主题' } })
  fireEvent.click(screen.getByRole('button', { name: '创建主题' }))
  rerender(<ResearchThemesPanel instrumentId="equity" />)
  await waitFor(() => expect(screen.getByRole('button', { name: '添加主题' })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: '添加主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '股票主题草稿' } })
  await act(async () => finish(theme()))
  expect(screen.getByLabelText('主题名称')).toHaveProperty('value', '股票主题草稿')
  expect(screen.queryByText('主题已建立。')).toBeNull()
})

it('closes a theme with an explicit reason while preserving its record', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme()] }); api.update.mockResolvedValue(theme({ status: 'closed' }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  expect(api.detail).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('管理主题'))
  fireEvent.click(screen.getByRole('button', { name: '结束主题跟踪' }))
  fireEvent.change(screen.getByLabelText('结束原因'), { target: { value: '机制已不适用' } })
  fireEvent.click(screen.getByRole('button', { name: '保存并结束' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('gold', 'theme-credit', { status: 'closed', close_reason: '机制已不适用' }))
})

it('keeps reader accounts read-only and refreshes only affected instruments', async () => {
  api.get.mockResolvedValue({ identity: { ...identity, team_role: 'reader' }, themes: [theme()] })
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  await openTheme(theme().title)
  expect(screen.queryByRole('button', { name: '写投资观点' })).toBeNull()
  expect(screen.getByRole('button', { name: '添加主题' }).hasAttribute('disabled')).toBe(true)
  await act(async () => announceResearchPublication(['equity'])); expect(api.get).toHaveBeenCalledTimes(1)
  await act(async () => announceResearchPublication(['gold'])); expect(api.get).toHaveBeenCalledTimes(2)
})

it('respects the active-theme limit and preserves a failed draft', async () => {
  api.get.mockResolvedValue({ identity, active_limit: 1, themes: [theme()] })
  const { unmount } = render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  await openTheme(theme().title)
  expect(screen.getByRole('button', { name: '添加主题' }).hasAttribute('disabled')).toBe(true)
  unmount(); api.get.mockResolvedValue({ identity, themes: [] }); api.create.mockRejectedValue(new Error('主题达到上限'))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无重要研究主题/)
  fireEvent.click(screen.getByRole('button', { name: '添加主题' })); fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '新主题' } })
  fireEvent.click(screen.getByRole('button', { name: '创建主题' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '主题达到上限')
  expect(screen.getByLabelText('主题名称')).toHaveProperty('value', '新主题')
})

it('translates the working surface while preserving original research text', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  api.get.mockResolvedValue({ identity, themes: [theme({ title: 'Risk', synthesis: 'Research' })] })
  render(<LanguageProvider messages={researchMessages} patterns={researchPatterns}><LanguageSelector /><ResearchThemesPanel instrumentId="gold" onAskAssistant={vi.fn()} /></LanguageProvider>)
  expect(await screen.findByRole('heading', { name: /Key research themes/ })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Risk' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Research context and timeline' }))
  expect(await screen.findByRole('button', { name: 'Discuss theme' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Create theme' })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  expect(await screen.findByRole('button', { name: '讨论主题' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Risk' })).toBeTruthy()
})


it('loads theme detail only when expanded and opens an archived theme from a link', async () => {
  const second = theme({ theme_id: 'second', title: '盈利能否覆盖投入？', synthesis: '第二个主题的完整分析。', status: 'paused' })
  api.get.mockResolvedValue({ identity, themes: [theme(), second] })
  const namesChanged = vi.fn()
  const { container } = render(<ResearchThemesPanel instrumentId="gold" onThemeNamesChange={namesChanged} />)
  await screen.findByRole('heading', { name: theme().title })
  expect(api.get).toHaveBeenCalledWith('gold', expect.any(AbortSignal), false)
  await waitFor(() => expect(namesChanged).toHaveBeenLastCalledWith({ [theme().theme_id]: theme().title, second: second.title }))
  expect(api.get).toHaveBeenCalledTimes(1)
  expect(api.detail).not.toHaveBeenCalled()
  expect(container.querySelectorAll('.research-theme-record')).toHaveLength(0)
  await openTheme(theme().title)
  expect(api.detail).toHaveBeenCalledWith('gold', 'theme-credit', expect.any(AbortSignal))
  window.history.replaceState(null, '', '#research-theme-second')
  fireEvent(window, new HashChangeEvent('hashchange'))
  await waitFor(() => expect(api.detail).toHaveBeenCalledWith('gold', 'second', expect.any(AbortSignal)))
  expect(screen.getByText('暂停或结束的主题 · 1').closest('details')).toHaveProperty('open', true)
  expect(container.querySelectorAll('.research-theme-record')).toHaveLength(2)
  expect(screen.queryByRole('tablist')).toBeNull()
})

it('retains loaded theme evidence and its exact version if a later detail refresh fails', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme()] })
  const ask = vi.fn()
  render(<ResearchThemesPanel instrumentId="gold" onAskAssistant={ask} />)
  await screen.findByRole('heading', { name: theme().title })
  await openTheme(theme().title)
  api.get.mockResolvedValue({ identity, themes: [theme({ source_version_id: 'theme:theme-credit:2', latest_development: '更新后的摘要。' })] })
  api.detail.mockRejectedValue(new Error('详情读取暂不可用'))
  act(() => announceResearchPublication(['gold']))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '详情读取暂不可用。以下保留上次有效内容。')
  expect(screen.getByText('更新后的摘要。')).toBeTruthy()
  expect(screen.getByText(theme().synthesis!)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '讨论主题' }))
  expect(ask).toHaveBeenLastCalledWith(expect.stringContaining(theme().synthesis!), expect.objectContaining({ theme_version_id: 'theme:theme-credit:1' }))
})

it('keeps migration bookkeeping out of analysis changes and the default evolution while retaining access', async () => {
  const migrated: ResearchUpdate = { update_id: 'theme:theme-credit:1', kind: 'theme', title: '历史主题整理记录', body: '原研究问题', recorded_at: '2026-09-20T00:00:00Z', author: '研究员', author_role: 'researcher', theme_ids: ['theme-credit'], sources: [], reference: { instrument_id: 'gold', theme_version_id: 'theme:theme-credit:1' } }
  const correction: ResearchUpdate = { ...migrated, update_id: 'correction:1', kind: 'question', title: '保留来源修正记录', change: 'citation_corrected', author_role: 'system' }
  api.get.mockResolvedValue({ identity, themes: [theme({ baseline_status: 'pending', latest_development: '历史持续跟进已纳入统一主题。', migration_origin: { revision: '0061', original_key: 'old:1', recorded_at: '2026-09-20T00:00:00Z' }, updates: [migrated, correction] })] })
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  await openTheme(theme().title)
  expect(screen.queryByText('历史持续跟进已纳入统一主题。')).toBeNull()
  expect(screen.queryByRole('heading', { name: migrated.title })).toBeNull()
  expect(screen.queryByRole('heading', { name: correction.title })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '主题背景与证据' }))
  fireEvent.click(screen.getByLabelText('包括整理与资料修订'))
  expect(screen.getByRole('heading', { name: migrated.title })).toBeTruthy()
  expect(screen.getByRole('heading', { name: correction.title })).toBeTruthy()
})

async function openTheme(title: string) {
  const card = screen.getByRole('heading', { name: title }).closest('article')!
  const trigger = within(card).queryByRole('button', { name: '研究脉络与时间线' })
  if (trigger) fireEvent.click(trigger)
  await waitFor(() => expect(card.querySelector('.research-theme-record')).toBeTruthy())
}


it('pauses and restores a theme from its card without fetching detailed research', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme()] })
  api.update.mockResolvedValue(theme({ status: 'paused' }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  expect(screen.getByText(theme().synthesis!)).toBeTruthy()
  api.get.mockResolvedValue({ identity, themes: [theme({ status: 'paused' })] })
  fireEvent.click(screen.getByText('管理主题'))
  fireEvent.click(screen.getByRole('button', { name: '暂停后续跟踪' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('gold', 'theme-credit', { status: 'paused' }))
  fireEvent.click(await screen.findByText('暂停或结束的主题 · 1'))
  fireEvent.click(screen.getByRole('button', { name: '恢复后续跟踪' }))
  await waitFor(() => expect(api.update).toHaveBeenLastCalledWith('gold', 'theme-credit', { status: 'active' }))
  expect(api.detail).not.toHaveBeenCalled()
})


it('does not mark a selected but unreviewed theme as freshly reviewed', async () => {
  const saved = theme({ last_reviewed_at: '2026-09-05T08:00:00Z' })
  api.get.mockResolvedValue({ identity, themes: [saved] })
  render(<ResearchThemesPanel instrumentId="gold" workingSet={{ mode: 'incremental', cutoff: '2026-10-12', selected_theme_ids: [saved.theme_id], reviewed_theme_ids: [] }} />)
  await screen.findByRole('heading', { name: saved.title })
  expect(screen.getByText('本轮未复核 · 判断日期保留原记录')).toBeTruthy()
  expect(api.update).not.toHaveBeenCalled()
})

it('adds a manual record with the viewed revision and displays it in the theme timeline', async () => {
  const original = theme()
  api.get.mockResolvedValue({ identity, themes: [original] })
  api.manualNote.mockImplementation(async () => {
    const saved = { ...original, revision_number: 2, source_version_id: 'theme:theme-credit:2', manual_notes: [{ note_id: 'note-1', body: '订单数据已经披露；现金转化仍待观察。', author: 'Shaw', author_user_id: identity.user_id, created_at: '2026-10-13T08:00:00Z' }] }
    api.get.mockResolvedValue({ identity, themes: [saved] }); return saved
  })
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: original.title })
  await openTheme(original.title)
  fireEvent.click(screen.getByRole('button', { name: '添加人工记录' }))
  fireEvent.change(screen.getByLabelText('事实、分析与来源'), { target: { value: '订单数据已经披露；现金转化仍待观察。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存人工记录' }))
  await waitFor(() => expect(api.manualNote).toHaveBeenCalledWith('gold', original.theme_id, '订单数据已经披露；现金转化仍待观察。', 1))
  expect(await screen.findByText('订单数据已经披露；现金转化仍待观察。')).toBeTruthy()
})

it('deletes the selected theme only after confirmation and preserves it when a revision conflict is returned', async () => {
  const original = theme()
  api.get.mockResolvedValue({ identity, themes: [original] })
  api.remove.mockRejectedValue(new Error('主题已更新，请刷新后重试。'))
  render(<LanguageProvider><ResearchThemesPanel instrumentId="gold" /></LanguageProvider>)
  fireEvent.click(await screen.findByText('管理主题'))
  fireEvent.click(screen.getByRole('button', { name: '删除主题' }))
  expect(api.remove).not.toHaveBeenCalled()
  const dialog = screen.getByRole('alertdialog', { name: '删除研究主题' })
  expect(dialog.textContent).toContain(original.title)
  fireEvent.click(within(dialog).getByRole('button', { name: '删除主题' }))
  await waitFor(() => expect(api.remove).toHaveBeenCalledWith('gold', original.theme_id, 1))
  expect(await within(dialog).findByText('主题已更新，请刷新后重试。')).toBeTruthy()
  expect(screen.getByRole('heading', { name: original.title })).toBeTruthy()
})


it('previews the retained eight-condition XLK observation text without truncating it or expanding the synthesis', async () => {
  // Retained reading fixture from the October 14 UI review, not verified investment facts.
  const nextCheck = "结构保持上一版第1–8项，按本轮核实结果确认第1、2、4项仍为待观察：1) 2026-10-15（北京时间14:00）台积电Q3法说会＋2026-10-14 ASML业绩（cutoff前均未发布）：核对2027年资本开支（前瞻：美银约830亿美元、汇丰850亿美元）、2027年初先进节点涨价5%–10%是否确认、CoWoS与硅中介层产能及委外节奏、毛利率指引。（失效条件：以电力、许可、客户部署或存储成本为由下调2027年拉货能见度。）2) 10月底–11月初超大规模厂商Q3财报与10-Q（cutoff前未发布，不以推测替代事实）：capex指引、折旧/有用寿命口径、租赁与采购承诺（表外）、替代融资；并对照布鲁金斯口径（10.3万亿/3.63%GDP、约1.4万亿结构债务中7000–8000亿由银行持有）观察是否出现正式披露口径。（失效条件：capex指引下修，或10-Q中租赁/采购承诺环比收缩。）3) 2026-11-16至20当周英伟达FY2027三季报与下季指引：数据中心收入环比、10-Q表外担保科目是否继续扩张；备选2026-10-20英伟达GTC柏林。4) 内存合约价与扩产：2027年HBM4年度合约价能否在10月内收尾、三星「4美元/Gb中高区间」报价是否被客户接受（若成交价显著低于报价则HBM4溢价假设走弱）；三星与SK海力士Q3正式财报；美光FY27 capex落地与DIO回落；并观察10-12存储股回落、韩股外资流出与回购退潮是否延续。5) 融资与信用：Anthropic投资者日与其180亿美元次级债定价、IPO进度，博通为OpenAI定制芯片逾500亿美元融资进展，甲骨文5年期CDS能否持续高于250bp，Volta 50亿美元AI贷款分销结果。（失效条件：次级债需大幅折价发行或IPO推迟。）6) 月度现金读数：GPU租赁价与DRAM/NAND现货；若DRAM/NAND现货连续两月回落而合约价仍在上涨，需把「现货与合约分层」写入本主题主判断。7) 苹果链量价验证：iPhone 18 Pro/Pro Max 10–11月订单与出货跟踪（日经口径削减15%–20% vs 证券时报口径总量不变/结构上移，须由后续渠道数据裁决）、iPhone Duo 10-23上市后销售、苹果是否在FY4Q给出毛利率指引区间。8) AWS「Trainium产能大致售罄至明年年底」与「未来几年再购200万块英伟达GPU」的后续可验证口径；若AWS/超大规模厂商公开口径低于上述自述，则需求侧证据需下修。"
  const longTheme = theme({ next_check: nextCheck, synthesis: '当前认识保持独立的阅读层级。'.repeat(25) })
  api.get.mockResolvedValue({ identity, themes: [longTheme] })
  const { container } = render(<ResearchThemesPanel instrumentId="xlk" />)
  const expand = await screen.findByRole('button', { name: '展开下一观察' })
  const original = container.querySelector('.research-theme-card-next p')!
  expect(original.textContent).toBe(nextCheck)
  expect(original.classList.contains('research-theme-next-preview')).toBe(true)
  expect(expand.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(expand)
  expect(screen.getByRole('button', { name: '收起下一观察' }).getAttribute('aria-expanded')).toBe('true')
  expect(original.classList.contains('research-theme-next-preview')).toBe(false)
  expect(original.textContent).toBe(nextCheck)
  expect(screen.getByRole('button', { name: '展开当前认识' }).getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(screen.getByRole('button', { name: '收起下一观察' }))
  expect(original.classList.contains('research-theme-next-preview')).toBe(true)
  expect(longTheme.next_check).toBe(nextCheck)
  expect(api.update).not.toHaveBeenCalled()
})

it('shows a short next observation without an expansion control', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme()] })
  render(<ResearchThemesPanel instrumentId="gold" />)
  expect(await screen.findByText(theme().next_check!)).toBeTruthy()
  expect(screen.queryByRole('button', { name: '展开下一观察' })).toBeNull()
})
