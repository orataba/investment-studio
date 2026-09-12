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
  await screen.findByText(/暂无正在跟踪的长期主题/)
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

it('keeps PM judgments and researcher updates in one theme thread with exact canonical references', async () => {
  const assessment = '当前证据仍不足以排除实际利率压制。'
  const opinion = { update_id: 'opinion:pm-1:2', kind: 'opinion' as const, title: '中期信用担忧值得关注', body: '中期偏多黄金，短期方向未判断。', recorded_at: '2026-09-05T10:00:00Z', author: 'Shaw', author_role: 'user' as const, theme_ids: ['theme-credit'], sources: [], reference: { instrument_id: 'gold', theme_id: 'theme-credit', pm_note_id: 'pm-1', pm_note_revision: 2 }, details: [{ label: '判断期限', text: '未来一个季度' }] }
  const update = { ...opinion, update_id: 'question:q1:1', kind: 'question' as const, title: '实际利率是否仍占主导', body: assessment, author: '研究员', author_role: 'researcher' as const, recorded_at: '2026-09-06T08:00:00Z', reference: { instrument_id: 'gold', theme_id: 'theme-credit', notebook_version_id: 'n1' } }
  api.get.mockResolvedValue({ identity, themes: [theme({ origin: 'user', current_questions: [{ ...update, next_check: '核实收益率变化的分解。', status: 'open', tracking_status: 'active', last_changed_at: update.recorded_at }], updates: [opinion, update] })] })
  const ask = vi.fn()
  const { container } = render(<ResearchThemesPanel instrumentId="gold" onAskAssistant={ask} />)
  await screen.findByText('核实收益率变化的分解。')
  const thread = screen.getByText('主题研究时间线 · 2').closest('details')!
  expect(thread.open).toBe(false)
  fireEvent.click(screen.getByText('主题研究时间线 · 2'))
  expect([...thread.querySelectorAll('[data-update-id]')].map(node => node.getAttribute('data-update-id'))).toEqual([update.update_id, opinion.update_id])
  const pm = within(thread).getByRole('heading', { name: opinion.title }).closest('article')!
  expect(within(pm).getByText('人工判断')).toBeTruthy()
  expect(within(pm).getByText(opinion.body)).toBeTruthy()
  fireEvent.click(within(pm).getByText('分析与研究依据'))
  expect(within(pm).getByText('未来一个季度')).toBeTruthy()
  fireEvent.click(within(pm).getByRole('button', { name: '追问这条更新' }))
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), { ...opinion.reference, research_update_id: opinion.update_id })
  fireEvent.click(screen.getByRole('button', { name: '讨论这个主题' }))
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), { instrument_id: 'gold', theme_id: 'theme-credit' })
  expect(container.querySelector('#research-theme-theme-credit')).not.toBeNull()
})

it('records the reason when a person ends a theme and keeps its history', async () => {
  api.get.mockResolvedValue({ identity, themes: [theme()] })
  api.update.mockResolvedValue(theme({ status: 'closed', close_reason: '研究问题已经解决。' }))
  render(<ResearchThemesPanel instrumentId="gold" />)
  await screen.findByRole('heading', { name: theme().title })
  fireEvent.click(screen.getByText('背景与主题管理'))
  fireEvent.click(screen.getByRole('button', { name: '结束主题' }))
  fireEvent.change(screen.getByLabelText('结束原因'), { target: { value: '研究问题已经解决。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存并结束' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('gold', 'theme-credit', { status: 'closed', close_reason: '研究问题已经解决。' }))
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
  await screen.findByText(/暂无正在跟踪的长期主题/)
  fireEvent.click(screen.getByRole('button', { name: '建立主题' }))
  fireEvent.change(screen.getByLabelText('主题名称'), { target: { value: theme().title } })
  fireEvent.change(screen.getByLabelText('持续关注的问题'), { target: { value: theme().question } })
  fireEvent.click(screen.getByRole('button', { name: '保存主题' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '无法保存主题')
  expect(within(screen.getByRole('region', { name: '长期主题' })).getByLabelText('主题名称')).toHaveProperty('value', theme().title)
})
