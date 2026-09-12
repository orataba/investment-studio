// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchTrackingPanel from './ResearchTrackingPanel'
import type { CurrentResearchFollowup, ResearchTheme, ResearchUpdate } from '../lib/researchDossierApi'
import { announceResearchPublication } from '../lib/researchUpdates'

const mocks = vi.hoisted(() => ({ activity: vi.fn(), themes: vi.fn(), members: vi.fn() }))
vi.mock('../lib/researchDossierApi', async original => ({ ...await original<typeof import('../lib/researchDossierApi')>(), getResearchActivity: mocks.activity, getResearchThemes: mocks.themes }))
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), fetchJson: mocks.members }))
const identity = { user_id: 'pm-1', display_name: 'Shaw', team_role: 'member' }
const update = (instrumentId: string, overrides: Partial<ResearchUpdate> = {}): ResearchUpdate => ({
  update_id: 'question:pending:1', kind: 'question', title: '重要假设仍待披露验证', body: '已有证据未能解决原问题。',
  recorded_at: '2026-08-15T08:00:00Z', author: '研究员', author_role: 'researcher', theme_ids: [], sources: [],
  status: 'open', next_check: '核实下一次正式披露。', reference: { instrument_id: instrumentId, notebook_version_id: 'notebook-1' }, ...overrides,
})
const followup = (instrumentId: string, overrides: Partial<CurrentResearchFollowup> = {}): CurrentResearchFollowup => ({
  followup_id: 'question:pending', kind: 'question', title: update(instrumentId).title, assessment: update(instrumentId).body,
  next_check: update(instrumentId).next_check!, theme_ids: [], latest_update: update(instrumentId), related_updates: [],
  last_changed_at: '2026-08-15T08:00:00Z', last_reviewed_at: '2026-09-09T08:00:00Z', last_review_status: 'reviewed', ...overrides,
})
beforeEach(() => {
  vi.resetAllMocks()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-12T08:00:00Z'))
  mocks.members.mockResolvedValue([])
  mocks.themes.mockResolvedValue({ identity, themes: [] })
  mocks.activity.mockImplementation(async (id: string) => ({ instrument_id: id, current_followups: [followup(id)], updates: [update(id)] }))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

it.each(['equity', 'sector-etf', 'broad-index', 'gold-etf', 'public-fund', 'private-fund', 'bitcoin'])(
  'keeps unresolved %s research visible even with no long-term theme or recent history', async (instrumentId) => {
    const ask = vi.fn()
    render(<ResearchTrackingPanel instrumentId={instrumentId} onAskAssistant={ask} />)
    const current = screen.getByRole('region', { name: '当前跟踪' })
    await within(current).findByRole('heading', { name: '重要假设仍待披露验证' })
    expect(within(current).getByText('暂无正在跟踪的长期主题。独立事项见下方。')).toBeTruthy()
    expect(within(current).getByText('核实下一次正式披露。')).toBeTruthy()
    expect(within(current).queryByText('当前没有尚待解决的独立事项。已结束事项保留在下方研究动态。')).toBeNull()
    expect(within(screen.getByRole('region', { name: '研究动态' })).getByText(/^当前范围没有研究更新/)).toBeTruthy()
    expect(mocks.activity).toHaveBeenCalledTimes(1)
    expect(mocks.activity).toHaveBeenCalledWith(instrumentId, expect.any(AbortSignal))
    fireEvent.click(within(current).getByRole('button', { name: '继续跟进此事项' }))
    expect(ask).toHaveBeenCalledWith(expect.any(String), { ...update(instrumentId).reference, research_update_id: update(instrumentId).update_id })
  },
)

it('shows related questions inside their event and distinguishes evidence-limited review from progress', async () => {
  const event = update('equity', { update_id: 'event:acquisition:2', kind: 'event', title: '收购整合跟进', reference: { instrument_id: 'equity', event_case_id: 'acquisition', event_version_id: 'acquisition:2' } })
  mocks.activity.mockResolvedValue({ instrument_id: 'equity', updates: [event, update('equity')], current_followups: [followup('equity', {
    followup_id: 'event:acquisition', kind: 'event', title: event.title, latest_update: event, related_updates: [update('equity')], last_review_status: 'insufficient_evidence',
  })] })
  const { container } = render(<ResearchTrackingPanel instrumentId="equity" />)
  const current = screen.getByRole('region', { name: '当前跟踪' })
  await within(current).findByRole('heading', { name: '收购整合跟进' })
  expect(container.querySelectorAll('[data-followup-id]')).toHaveLength(1)
  expect(within(current).getByText('关联问题与验证 · 1').closest('details')?.open).toBe(false)
  expect(within(current).getByText('当前判断最近复核证据不足', { exact: false })).toBeTruthy()
  const clocks = container.querySelector('.research-current-followup .research-followup-clocks')!
  expect([...clocks.querySelectorAll('time')].map(node => node.dateTime)).toEqual(['2026-08-15T08:00:00Z', '2026-09-09T08:00:00Z'])
})

it('does not treat historical open questions as current follow-ups after their theme is paused', async () => {
  const old = update('gold-etf', { recorded_at: '2026-09-10T08:00:00Z' })
  const paused: ResearchTheme = { theme_id: 'paused', instrument_id: 'gold-etf', title: '已暂停的信用主题', question: '信用变化是否持续？', status: 'paused',
    author: '研究员', author_user_id: 'researcher', created_at: '2026-08-01T08:00:00Z', updated_at: '2026-09-11T08:00:00Z', revision_number: 2, notes: [], research_progress: [], updates: [old] }
  mocks.themes.mockResolvedValue({ identity, themes: [paused] })
  mocks.activity.mockResolvedValue({ instrument_id: 'gold-etf', current_followups: [], updates: [old] })
  render(<ResearchTrackingPanel instrumentId="gold-etf" />)
  await screen.findByText('当前没有尚待解决的独立事项。已结束事项保留在下方研究动态。')
  expect(screen.getByText('暂停或结束的主题 · 1').closest('details')?.open).toBe(false)
  const history = screen.getByRole('region', { name: '研究动态' })
  expect(within(history).getByText('当时待验证')).toBeTruthy()
  expect(within(history).queryByText('继续研究')).toBeNull()
})

it('refreshes current follow-ups and history together without repeating the shared read', async () => {
  render(<ResearchTrackingPanel instrumentId="private-fund" />)
  await screen.findByRole('heading', { name: '重要假设仍待披露验证' })
  mocks.activity.mockResolvedValue({ instrument_id: 'private-fund', current_followups: [], updates: [update('private-fund', { recorded_at: '2026-09-12T07:00:00Z', status: 'supported' })] })
  await act(async () => announceResearchPublication(['private-fund']))
  await waitFor(() => expect(mocks.activity).toHaveBeenCalledTimes(2))
  expect(screen.getByText('当前没有尚待解决的独立事项。已结束事项保留在下方研究动态。')).toBeTruthy()
  expect(within(screen.getByRole('region', { name: '研究动态' })).getByText('当时证据支持')).toBeTruthy()
})
