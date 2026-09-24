// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchRecentEvents from './ResearchRecentEvents'
import ResearchUpdateCard from './ResearchUpdateCard'
import { SavedFigure } from './ResearchModules'
import type { ResearchUpdate } from '../lib/researchDossierApi'

const api = vi.hoisted(() => ({ activity: vi.fn(), themes: vi.fn(), create: vi.fn(), update: vi.fn(), note: vi.fn(), source: vi.fn() }))
vi.mock('../lib/researchDossierApi', async original => ({ ...await original<typeof import('../lib/researchDossierApi')>(), getResearchActivity: api.activity, getResearchThemes: api.themes, createResearchTheme: api.create, updateResearchTheme: api.update, getSavedResearchSource: api.source }))
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), createInstrumentResearchNote: api.note }))
const event: ResearchUpdate = { update_id: 'event:case:version', kind: 'event', title: '配售完成', body: '资金已经取得，后续用途仍待核实。', recorded_at: '2026-09-23T00:00:00Z', author: '研究员', author_role: 'researcher', theme_ids: [], sources: [{ source_id: 'original', title: '配售原文' }], reference: { instrument_id: 'fund', event_case_id: 'case', event_version_id: 'version' }, direction: 'uncertain' }
beforeEach(() => { vi.resetAllMocks(); api.themes.mockResolvedValue({ identity: { user_id: 'pm', display_name: 'PM', team_role: 'member' }, themes: [] }); api.source.mockResolvedValue({ source_id: 'computed:1', title: '已留存回报比较', data: { rows: [{ instrument_id: 'fund', return_pct: 2, max_drawdown_pct: -3 }] } }) })
afterEach(cleanup)

it('shows only current one-off events rather than duplicating the research activity stream', async () => {
  api.activity.mockResolvedValue({ instrument_id: 'fund', updates: [{ ...event, title: '不应重复的主题动态' }], recent_events: [event, { ...event, update_id: 'old', superseded: true, title: '旧事件版本' }] })
  render(<ResearchRecentEvents instrumentId="fund" />)
  expect(await screen.findByRole('heading', { name: event.title })).toBeTruthy()
  expect(screen.queryByText('不应重复的主题动态')).toBeNull()
  expect(screen.queryByText('旧事件版本')).toBeNull()
  expect(screen.queryByRole('region', { name: '独立跟进事项' })).toBeNull()
  expect(screen.queryByRole('region', { name: '研究动态' })).toBeNull()
})

it('keeps important linked events discoverable but excludes organization receipts', async () => {
  api.activity.mockResolvedValue({ instrument_id: 'fund', recent_events: [], updates: [
    { ...event, theme_ids: ['capital'], title: '融资完成后的现金用途' },
    { ...event, update_id: 'organization', title: '历史记录已整理', change: 'organized' },
  ] })
  render(<ResearchRecentEvents instrumentId="fund" />)
  expect(await screen.findByRole('heading', { name: '融资完成后的现金用途' })).toBeTruthy()
  expect(screen.queryByText('历史记录已整理')).toBeNull()
  expect(screen.queryByText(event.body)).toBeNull()
  fireEvent.click(screen.getByText('阅读这条记录'))
  expect(await screen.findByText(event.body)).toBeTruthy()
})

it('saves a PM opinion directly with the selected event version and editable background', async () => {
  api.note.mockResolvedValue({ profile: {}, notes: [] })
  const ask = vi.fn()
  render(<ResearchUpdateCard update={event} onAskAssistant={ask} />)
  fireEvent.click(screen.getByRole('button', { name: '写投资观点' }))
  expect(screen.getByLabelText('当时背景与依据')).toHaveProperty('value', expect.stringContaining(event.body))
  fireEvent.change(screen.getByLabelText('我的投资观点'), { target: { value: '关注资本回报，暂不调整立场。' } })
  fireEvent.change(screen.getByLabelText('当时背景与依据'), { target: { value: '已读配售原文；投资用途仍待披露。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存投资观点' }))
  await waitFor(() => expect(api.note).toHaveBeenCalledWith('fund', expect.objectContaining({ note: expect.objectContaining({ body: '关注资本回报，暂不调整立场。', research_context: expect.objectContaining({ research_update_id: event.update_id, event_case_id: 'case', event_version_id: 'version', source_ids: ['original'], background: '已读配售原文；投资用途仍待披露。' }) }) })))
  expect(ask).not.toHaveBeenCalled()
  expect(await screen.findByRole('status')).toHaveProperty('textContent', '投资观点已保存。')
})

it('links an event to an existing theme without creating a duplicate theme', async () => {
  api.themes.mockResolvedValue({ identity: {}, themes: [{ theme_id: 'financing', title: '资本配置', status: 'active' }] })
  api.update.mockResolvedValue({})
  render(<ResearchUpdateCard update={event} />)
  fireEvent.click(screen.getByRole('button', { name: '转为跟踪主题' }))
  fireEvent.change(await screen.findByLabelText('跟踪主题'), { target: { value: 'financing' } })
  fireEvent.click(screen.getByRole('button', { name: '关联并继续研究' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('fund', 'financing', { reference: expect.objectContaining({ research_update_id: event.update_id, event_case_id: 'case', event_version_id: 'version', source_ids: ['original'] }) }))
  expect(api.create).not.toHaveBeenCalled()
})

it.each(['notebook', 'theme'] as const)('uses precise source and %s references for chart discussion, PM opinion and theme creation', async kind => {
  const ask = vi.fn(); api.create.mockResolvedValue({}); api.note.mockResolvedValue({})
  const versionId = kind === 'theme' ? 'theme:original:3' : 'original-notebook'
  const props = kind === 'theme' ? { themeVersionId: versionId } : { notebookVersionId: versionId }
  const versionReference = kind === 'theme' ? { theme_version_id: versionId } : { notebook_version_id: versionId }
  render(<SavedFigure instrumentId="fund" {...props} source={{ source_id: 'computed:1', title: '已留存回报比较' }} onAskAssistant={ask} />)
  fireEvent.click(await screen.findByRole('button', { name: '讨论这张图表' }))
  expect(api.source).toHaveBeenCalledWith('fund', 'computed:1', expect.any(AbortSignal), versionId)
  expect(ask).toHaveBeenCalledWith(expect.stringContaining('已留存回报比较'), expect.objectContaining({ instrument_id: 'fund', ...versionReference, source_ids: ['computed:1'] }))
  fireEvent.click(screen.getByRole('button', { name: '基于图表记录观点' }))
  const background = (screen.getByLabelText('当时背景与依据') as HTMLTextAreaElement).value
  expect(background).not.toContain(versionId)
  expect(background).not.toContain('computed:1')
  fireEvent.change(screen.getByLabelText('我的投资观点'), { target: { value: '短样本不足以确认长期优势。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存投资观点' }))
  await waitFor(() => expect(api.note).toHaveBeenCalledWith('fund', expect.objectContaining({ note: expect.objectContaining({ research_context: expect.objectContaining({ ...versionReference, source_ids: ['computed:1'], background: expect.stringContaining('已留存回报比较') }) }) })))
  if (kind === 'theme') expect(api.note.mock.calls[0][1].note.research_context.notebook_version_id).toBeUndefined()
  fireEvent.click(screen.getByRole('button', { name: '基于数据建立主题' }))
  fireEvent.click(screen.getByRole('button', { name: '创建并开始研究' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledWith('fund', expect.objectContaining({ kind: 'quantitative', reference: { ...versionReference, source_ids: ['computed:1'] } })))
})

it('retains theme-version evidence and opinion context in a historical timeline card', async () => {
  api.note.mockResolvedValue({})
  render(<ResearchUpdateCard update={{ ...event, kind: 'theme', theme_ids: ['theme-1'], sources: [{ source_id: 'computed:1', source_type: 'computed_metric' }], reference: { instrument_id: 'fund', theme_id: 'theme-1', theme_version_id: 'theme:theme-1:2' } }} inTheme />)
  fireEvent.click(screen.getByText('分析与研究依据'))
  fireEvent.click(screen.getByText('查看已保存的计算依据'))
  await waitFor(() => expect(api.source).toHaveBeenCalledWith('fund', 'computed:1', expect.any(AbortSignal), 'theme:theme-1:2'))
  fireEvent.click(screen.getByRole('button', { name: '写投资观点' }))
  fireEvent.change(screen.getByLabelText('我的投资观点'), { target: { value: '保留当时依据。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存投资观点' }))
  await waitFor(() => expect(api.note).toHaveBeenCalledWith('fund', expect.objectContaining({ note: expect.objectContaining({ research_context: expect.objectContaining({ theme_version_id: 'theme:theme-1:2', source_ids: ['computed:1'] }) }) })))
})

it('shows the backend research availability message after creating a theme from an event', async () => {
  const message = '主题已保存；研究运行环境尚不可用。'
  api.create.mockResolvedValue({ research_status: 'unavailable', research_message: message })
  render(<ResearchUpdateCard update={event} />)
  fireEvent.click(screen.getByRole('button', { name: '转为跟踪主题' }))
  fireEvent.click(screen.getByRole('button', { name: '创建并开始研究' }))
  expect(await screen.findByRole('status')).toHaveProperty('textContent', message)
})

it('preserves the draft on a failed direct save and does not offer theme creation for linked events', async () => {
  api.note.mockRejectedValue(new Error('来源版本不可用'))
  render(<ResearchUpdateCard update={{ ...event, theme_ids: ['existing'] }} />)
  expect(screen.queryByRole('button', { name: '转为跟踪主题' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '写投资观点' }))
  fireEvent.change(screen.getByLabelText('我的投资观点'), { target: { value: '等待核实。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存投资观点' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '来源版本不可用')
  expect(screen.getByLabelText('我的投资观点')).toHaveProperty('value', '等待核实。')
})
