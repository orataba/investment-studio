// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchThemesPanel from './ResearchThemesPanel'
import { announceResearchPublication } from '../lib/researchUpdates'
import type { ResearchTheme } from '../lib/researchDossierApi'

const api = vi.hoisted(() => ({ get: vi.fn(), create: vi.fn(), update: vi.fn() }))
vi.mock('../lib/researchDossierApi', async importOriginal => ({ ...await importOriginal<typeof import('../lib/researchDossierApi')>(), getResearchThemes: api.get, createResearchTheme: api.create, updateResearchTheme: api.update }))
const identity = { user_id: 'pm-1', display_name: 'Shaw', mode: 'account', team_role: 'member' }
const theme = (overrides: Partial<ResearchTheme> = {}): ResearchTheme => ({
  theme_id: 'theme-credit', instrument_id: 'gold', title: '美元信用与黄金', question: '长债收益率上升何时转变为信用担忧？', background: '名义收益率与黄金同步上升。', status: 'active', author_user_id: identity.user_id, author: identity.display_name,
  created_at: '2026-09-05T08:00:00Z', updated_at: '2026-09-05T08:00:00Z', revision_number: 1, notes: [], research_progress: [], ...overrides,
})
beforeEach(() => { vi.resetAllMocks(); api.get.mockResolvedValue({ identity, themes: [] }) })
afterEach(cleanup)

it('creates a user theme without asking for or submitting an owner and refreshes the saved record', async () => {
  api.create.mockResolvedValue(theme())
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无正在关注的主题/)
  fireEvent.click(screen.getByRole('button', { name: '建立主题' }))
  expect(screen.getByText('团队共享 · 记录人：Shaw')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: '美元信用与黄金' } })
  fireEvent.change(screen.getByLabelText('持续关注的问题'), { target: { value: theme().question } })
  fireEvent.change(screen.getByLabelText('背景（可选）'), { target: { value: theme().background } })
  api.get.mockResolvedValue({ identity, themes: [theme()] })
  fireEvent.click(screen.getByRole('button', { name: '保存主题' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledWith('gold', { title: theme().title, question: theme().question, background: theme().background, responsible_user_id: identity.user_id }))
  expect(await screen.findByRole('heading', { name: '美元信用与黄金' })).toBeTruthy()
  expect(screen.getByRole('status').textContent).toContain('关注主题已保存')
  expect(screen.getByRole('status').className).toContain('investment-studio-notice-toast-success')
})

it('pauses and resumes a theme without removing its history and submits only the changed status', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme()] })
  api.update.mockResolvedValue(theme({ status: 'paused' }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  api.get.mockResolvedValue({ identity, themes: [theme({ status: 'paused' })] })
  fireEvent.click(screen.getByRole('button', { name: '暂停关注' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('gold', 'theme-credit', { status: 'paused' }))
  await screen.findByText('暂停或结束的主题 · 1')
  fireEvent.click(screen.getByText('暂停或结束的主题 · 1'))
  expect(screen.getByText(theme().question)).toBeTruthy()
  api.get.mockResolvedValue({ identity, themes: [theme()] })
  fireEvent.click(screen.getByRole('button', { name: '恢复关注' }))
  await waitFor(() => expect(api.update).toHaveBeenLastCalledWith('gold', 'theme-credit', { status: 'active' }))
  await screen.findByRole('button', { name: '暂停关注' })
})

it('keeps PM judgments distinct from researcher progress and follows up with exact canonical references', async () => {
  const pmNote = { note_id: 'pm-1', note_date: '2026-09-01', note_type: 'thesis_update' as const, title: '中期信用担忧值得关注', body: '中期偏多黄金，短期方向未判断。', summary: '', author: 'Shaw', author_user_id: identity.user_id,
    created_at: '2026-09-05T10:00:00Z', updated_at: '2026-09-05T10:00:00Z', revision_number: 2, importance: 'medium' as const, tags: [], source_refs: '', people: '', follow_up_date: null, updated_by: null,
    research_context: { theme_id: 'theme-credit', relationship: 'initial' as const, horizon: '未来一个季度', verification: '观察长债与美元是否持续背离。', invalidation: '实际利率重新占主导。' } }
  api.get.mockResolvedValue({ identity, themes: [theme({ notes: [pmNote], research_progress: [{ run_id: 'run-2', recorded_at: '2026-09-06T08:00:00Z', assessment: '当前证据仍不足以排除实际利率压制。', next_check: '核实收益率变化的分解。', status: 'open', source_ids: [] }] })] })
  const ask = vi.fn()
  render(<ResearchThemesPanel instrumentId="gold" onAskAssistant={ask} />)
  await screen.findByText('当前证据仍不足以排除实际利率压制。')
  fireEvent.click(screen.getByText('投资经理的观点与复盘 · 1'))
  expect(screen.getByText(pmNote.body)).toBeTruthy()
  expect(screen.getByText(/实际记录 2026-09-05 10:00/)).toBeTruthy()
  fireEvent.click(screen.getByText('背景、验证与经验'))
  expect(screen.getByText('未来一个季度')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '讨论这条观点' }))
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), { instrument_id: 'gold', theme_id: 'theme-credit', pm_note_id: 'pm-1', pm_note_revision: 2 })
  fireEvent.click(screen.getByRole('button', { name: '讨论这个主题' }))
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), { instrument_id: 'gold', theme_id: 'theme-credit' })
})

it('refreshes only the affected instrument and keeps team themes read-only for reader accounts', async () => {
  api.get.mockResolvedValue({ identity: { ...identity, team_role: 'reader' }, themes: [theme({ author_user_id: 'other-pm', author: 'Another PM' })] })
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  expect(screen.queryByRole('button', { name: '编辑主题' })).toBeNull()
  await act(async () => announceResearchPublication(['equity']))
  expect(api.get).toHaveBeenCalledTimes(1)
  api.get.mockResolvedValue({ identity, themes: [theme({ question: '更新后的持续问题。' })] })
  await act(async () => announceResearchPublication(['gold']))
  expect(await screen.findByText('更新后的持续问题。')).toBeTruthy()
})

it('keeps an unsaved draft visible when saving fails', async () => {
  api.create.mockRejectedValue(new Error('无法保存主题'))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByText(/尚无正在关注的主题/)
  fireEvent.click(screen.getByRole('button', { name: '建立主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: theme().title } })
  fireEvent.change(screen.getByLabelText('持续关注的问题'), { target: { value: theme().question } })
  fireEvent.click(screen.getByRole('button', { name: '保存主题' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '无法保存主题')
  expect(within(screen.getByRole('region', { name: '持续关注主题' })).getByLabelText('主题名称')).toHaveProperty('value', theme().title)
})
