// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchThemesPanel from './ResearchThemesPanel'
import { announceResearchPublication } from '../lib/researchUpdates'
import type { ResearchTheme, ResearchUpdate } from '../lib/researchDossierApi'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import { researchMessages, researchPatterns } from '../researchMessages'

const api = vi.hoisted(() => ({ get: vi.fn(), create: vi.fn(), update: vi.fn(), source: vi.fn(), note: vi.fn() }))
vi.mock('../lib/researchDossierApi', async importOriginal => ({ ...await importOriginal<typeof import('../lib/researchDossierApi')>(), getResearchThemes: api.get, createResearchTheme: api.create, updateResearchTheme: api.update, getSavedResearchSource: api.source }))
vi.mock('../lib/api', async importOriginal => ({ ...await importOriginal<typeof import('../lib/api')>(), createInstrumentResearchNote: api.note }))
const identity = { user_id: 'pm-1', display_name: 'Shaw', mode: 'account', team_role: 'member' }
const theme = (overrides: Partial<ResearchTheme> = {}): ResearchTheme => ({
  theme_id: 'theme-credit', source_version_id: 'theme:theme-credit:1', instrument_id: 'gold', title: '美元信用与黄金', question: '长债收益率上升何时转变为信用担忧？', background: '名义收益率与黄金同步上升。', status: 'active', author_user_id: identity.user_id, author: identity.display_name,
  created_at: '2026-09-05T08:00:00Z', updated_at: '2026-09-05T08:00:00Z', revision_number: 1, notes: [], research_progress: [], kind: 'fundamental', priority: 'core', priority_reason: '影响黄金的中期定价机制', synthesis: '当前信用担忧尚未取代利率影响。', next_check: '对照后续实际利率与储备需求。', baseline_status: 'ready', ...overrides,
})
beforeEach(() => { vi.resetAllMocks(); api.get.mockResolvedValue({ identity, themes: [], active_limit: 10, target_count: 5 }) })
afterEach(() => { cleanup(); window.history.replaceState(null, '', '/') })
const expandTheme = () => fireEvent.click(screen.getByText('分析与时间线', { selector: 'summary' }))

it('creates a title-only theme with a chosen research type and lets the backend schedule its baseline', async () => {
  api.create.mockResolvedValue(theme({ research_run_id: 'baseline-run', research_status: 'queued', research_message: '已安排主题基线研究。' }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无重点主题/)
  fireEvent.click(screen.getByRole('button', { name: '建立主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '利率与黄金的条件关系' } })
  fireEvent.change(screen.getByLabelText('研究类型'), { target: { value: 'quantitative' } })
  api.get.mockResolvedValue({ identity, themes: [theme({ title: '利率与黄金的条件关系', kind: 'quantitative', synthesis: '', baseline_status: 'pending' })] })
  fireEvent.click(screen.getByRole('button', { name: '创建并开始研究' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledWith('gold', expect.objectContaining({ title: '利率与黄金的条件关系', kind: 'quantitative', question: '', responsible_user_id: identity.user_id })))
  expect(await screen.findByRole('heading', { name: '利率与黄金的条件关系' })).toBeTruthy()
  expect(screen.getByText('待补充研究基线。')).toBeTruthy()
  expect(screen.getByRole('status').textContent).toContain('已安排主题基线研究。')
})

it('shows synthesized priorities without surfacing old independent research questions', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme({ current_questions: [{ update_id: 'old-question', kind: 'question', title: '旧问题名称', body: '旧问题判断', recorded_at: '2026-09-01T00:00:00Z', author: '研究员', author_role: 'researcher', theme_ids: [], sources: [], reference: { instrument_id: 'gold' } }] })] })
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  expect(screen.getByText('核心')).toBeTruthy()
  expect(screen.queryByText('旧问题名称')).toBeNull()
  expect(screen.getAllByText(theme().synthesis!)[0].closest('details')).toBeNull()
  expect(screen.queryByText(theme().priority_reason!)).toBeNull()
  expect(screen.queryByRole('button', { name: '编辑主题' })).toBeNull()
})

it('preserves PM authorship and exact historical references in an actual theme timeline', async () => {
  const opinion: ResearchUpdate = { update_id: 'opinion:pm-1:2', kind: 'opinion', title: '中期信用担忧', body: '中期偏多黄金', recorded_at: '2026-09-05T10:00:00Z', author: 'Shaw', author_role: 'user', theme_ids: ['theme-credit'], sources: [], reference: { instrument_id: 'gold', theme_id: 'theme-credit', pm_note_id: 'pm-1', pm_note_revision: 2 } }
  const update: ResearchUpdate = { ...opinion, update_id: 'question:q1:1', kind: 'question', title: '实际利率仍占主导', author: '研究员', author_role: 'researcher', recorded_at: '2026-09-06T08:00:00Z', reference: { instrument_id: 'gold', theme_id: 'theme-credit', notebook_version_id: 'n1' } }
  api.get.mockResolvedValue({ identity, themes: [theme({ updates: [opinion, update] })] })
  const ask = vi.fn()
  const { container } = render(<ResearchThemesPanel instrumentId="gold" onAskAssistant={ask} />)
  await screen.findByRole('heading', { name: theme().title }); expandTheme()
  await screen.findByRole('heading', { name: '主题时间线 · 2' })
  const timeline = container.querySelector('.research-timeline')!
  expect(screen.getByText(/最近记录/)).toBeTruthy()
  expect(screen.queryByText(/判断更新/)).toBeNull()
  expect(timeline.closest('details')).toBe(container.querySelector('.research-theme-expanded'))
  expect(timeline.querySelectorAll('time')).toHaveLength(2)
  expect([...timeline.querySelectorAll('[data-update-id]')].map(node => node.getAttribute('data-update-id'))).toEqual([update.update_id, opinion.update_id])
  const pm = within(timeline as HTMLElement).getByRole('heading', { name: opinion.title }).closest('article')!
  expect(within(pm).getByText('人工判断')).toBeTruthy()
  fireEvent.click(within(pm).getByRole('button', { name: '追问这条更新' }))
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
  await screen.findByRole('heading', { name: theme().title }); expandTheme()
  fireEvent.click(await screen.findByRole('button', { name: '讨论这张图表' }))
  expect(api.source).toHaveBeenCalledWith('gold', source.source_id, expect.any(AbortSignal), 'theme:theme-credit:3')
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), { instrument_id: 'gold', theme_id: 'theme-credit', theme_version_id: 'theme:theme-credit:3', source_ids: [source.source_id] })
  fireEvent.click(screen.getByText('查看已保存的计算依据'))
  await waitFor(() => expect(api.source).toHaveBeenCalledTimes(2))
  expect(api.source).toHaveBeenLastCalledWith('gold', source.source_id, expect.any(AbortSignal), 'theme:theme-credit:3')
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
  await screen.findByText(/尚无重点主题/)
  fireEvent.click(screen.getByRole('button', { name: '建立主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '新主题' } })
  api.get.mockResolvedValue({ identity, themes: [theme({ synthesis: '', baseline_status: 'pending' })] })
  fireEvent.click(screen.getByRole('button', { name: '创建并开始研究' }))
  expect(await screen.findByText(message)).toBeTruthy()
  expect(await screen.findByText('待补充研究基线。')).toBeTruthy()
  expect(screen.queryByText('研究员正在建立主题基线。')).toBeNull()
})

it('keeps an open opinion bound to the theme version and evidence that formed its background', async () => {
  const original = theme({ sources: [{ source_id: 'original-source', title: '原始依据' }] })
  api.get.mockResolvedValue({ identity, themes: [original] })
  api.note.mockResolvedValue({})
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: original.title })
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
  await screen.findByText(/尚无重点主题/)
  fireEvent.click(screen.getByRole('button', { name: '建立主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '黄金主题' } })
  fireEvent.click(screen.getByRole('button', { name: '创建并开始研究' }))
  rerender(<ResearchThemesPanel instrumentId="equity" />)
  await waitFor(() => expect(screen.getByRole('button', { name: '建立主题' })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: '建立主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '股票主题草稿' } })
  await act(async () => finish(theme()))
  expect(screen.getByLabelText('主题名称')).toHaveProperty('value', '股票主题草稿')
  expect(screen.queryByText('主题已建立。')).toBeNull()
})

it('closes a theme with an explicit reason while preserving its record', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme()] }); api.update.mockResolvedValue(theme({ status: 'closed' }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title }); expandTheme()
  fireEvent.click(await screen.findByText('管理主题'))
  fireEvent.click(screen.getByRole('button', { name: '结束主题' }))
  fireEvent.change(screen.getByLabelText('结束原因'), { target: { value: '机制已不适用' } })
  fireEvent.click(screen.getByRole('button', { name: '保存并结束' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('gold', 'theme-credit', { status: 'closed', close_reason: '机制已不适用' }))
})

it('keeps reader accounts read-only and refreshes only affected instruments', async () => {
  api.get.mockResolvedValue({ identity: { ...identity, team_role: 'reader' }, themes: [theme()] })
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  expect(screen.queryByRole('button', { name: '写投资观点' })).toBeNull()
  expect(screen.getByRole('button', { name: '建立主题' }).hasAttribute('disabled')).toBe(true)
  await act(async () => announceResearchPublication(['equity'])); expect(api.get).toHaveBeenCalledTimes(1)
  await act(async () => announceResearchPublication(['gold'])); expect(api.get).toHaveBeenCalledTimes(2)
})

it('respects the active-theme limit and preserves a failed draft', async () => {
  api.get.mockResolvedValue({ identity, active_limit: 1, themes: [theme()] })
  const { unmount } = render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  expect(screen.getByRole('button', { name: '建立主题' }).hasAttribute('disabled')).toBe(true)
  unmount(); api.get.mockResolvedValue({ identity, themes: [] }); api.create.mockRejectedValue(new Error('主题达到上限'))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无重点主题/)
  fireEvent.click(screen.getByRole('button', { name: '建立主题' })); fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '新主题' } })
  fireEvent.click(screen.getByRole('button', { name: '创建并开始研究' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '主题达到上限')
  expect(screen.getByLabelText('主题名称')).toHaveProperty('value', '新主题')
})

it('translates the working surface while preserving original research text', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  api.get.mockResolvedValue({ identity, themes: [theme({ title: 'Risk', synthesis: 'Research' })] })
  render(<LanguageProvider messages={researchMessages} patterns={researchPatterns}><LanguageSelector /><ResearchThemesPanel instrumentId="gold" onAskAssistant={vi.fn()} /></LanguageProvider>)
  expect(await screen.findByRole('heading', { name: /Research priorities/ })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Risk' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Discuss theme' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Create theme' })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  expect(await screen.findByRole('button', { name: '讨论主题' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Risk' })).toBeTruthy()
})
