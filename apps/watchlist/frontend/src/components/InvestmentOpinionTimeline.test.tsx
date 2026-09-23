// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import InvestmentOpinionTimeline, { currentInvestmentOpinion } from './InvestmentOpinionTimeline'
import { emptyInstrumentResearchResponse, type InstrumentResearchNote, type InstrumentResearchResponse } from '../lib/api'
import { announceResearchPublication } from '../lib/researchUpdates'

const api = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), remove: vi.fn(), themes: vi.fn(), stance: vi.fn() }))
vi.mock('../lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  createInstrumentResearchNote: api.create,
  updateInstrumentResearchNote: api.update,
  deleteInstrumentResearchNote: api.remove,
  selectInvestmentStance: api.stance,
}))
vi.mock('../lib/researchDossierApi', async importOriginal => ({ ...await importOriginal<typeof import('../lib/researchDossierApi')>(), getResearchThemes: api.themes }))

function note(overrides: Partial<InstrumentResearchNote> = {}): InstrumentResearchNote {
  return {
    note_id: 'view-1', note_date: '2026-09-06', note_type: 'thesis_update', title: '等待需求验证', summary: '', body: '盈利改善尚需订单确认。',
    importance: 'high', tags: ['需求'], source_refs: 'https://example.com/report', people: '研究讨论', author: 'Shaw', author_user_id: 'user-shaw', follow_up_date: '2026-10-01',
    completed_at: null, created_at: '2026-09-06T08:00:00Z', updated_at: '2026-09-06T08:00:00Z', updated_by: 'terminal_ui', revision_number: 1,
    ...overrides,
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.themes.mockResolvedValue({ identity: { user_id: 'user-shaw', display_name: 'Shaw', mode: 'account', team_role: 'member' }, themes: [] })
})
afterEach(cleanup)

it('does not publish a late overall-view response into a different instrument', async () => {
  let finish!: (value: InstrumentResearchResponse) => void
  api.stance.mockReturnValue(new Promise<InstrumentResearchResponse>(resolve => { finish = resolve }))
  const research = { ...emptyInstrumentResearchResponse(), notes: [note()] }
  const onChange = vi.fn()
  const { rerender } = render(<InvestmentOpinionTimeline instrumentId="xlk" research={research} onChange={onChange} />)
  await waitFor(() => expect(screen.getByRole('button', { name: '设为当前总体观点' })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: '设为当前总体观点' }))
  rerender(<InvestmentOpinionTimeline instrumentId="gold" research={emptyInstrumentResearchResponse()} onChange={onChange} />)
  await act(async () => finish(research))
  expect(onChange).not.toHaveBeenCalled()
  expect(screen.queryByText(note().body)).toBeNull()
  expect(screen.getByRole('button', { name: '新增观点' })).toHaveProperty('disabled', false)
})

it('adds a new dated view without requiring a title or fixed research fields and leaves previous views intact', async () => {
  const research = { ...emptyInstrumentResearchResponse(), notes: [note()] }
  const response = { ...research, notes: [note({ note_id: 'view-2', title: '投资观点', body: '订单开始改善，继续观察现金流。' }), ...research.notes] }
  api.create.mockResolvedValue(response)
  function Parent() {
    const [value, setValue] = useState(research)
    return <InvestmentOpinionTimeline instrumentId="xlk" research={value} onChange={setValue} />
  }
  render(<Parent />)
  await waitFor(() => expect(screen.getByRole('button', { name: '新增观点' })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: '新增观点' }))
  fireEvent.change(screen.getByLabelText('日期'), { target: { value: '2026-09-06' } })
  fireEvent.change(screen.getByRole('textbox', { name: '观点' }), { target: { value: '订单开始改善，继续观察现金流。' } })
  fireEvent.change(screen.getByLabelText('当时背景与依据'), { target: { value: '季度订单披露已发布。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存观点' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledWith('xlk', expect.objectContaining({
    note: expect.objectContaining({ note_date: '2026-09-06', title: '投资观点', body: '订单开始改善，继续观察现金流。', note_type: 'thesis_update', source_refs: '' }),
  })))
  expect(await screen.findByText('订单开始改善，继续观察现金流。')).toBeTruthy()
  expect(screen.getByText('盈利改善尚需订单确认。')).toBeTruthy()
  expect(screen.queryByRole('combobox')).toBeNull()
  expect(screen.queryByText('研究评分')).toBeNull()
  expect(api.update).not.toHaveBeenCalled()
})

it('orders views by their stated date and keeps existing profile text available as a read-only previous record', () => {
  const research: InstrumentResearchResponse = {
    ...emptyInstrumentResearchResponse(),
    notes: [note({ note_id: 'older', note_date: '2026-08-31', title: '谨慎观察', body: '价格尚未反映需求放缓。' }), note()],
  }
  research.profile.current_view = '原判断：等待更清晰的证据。'
  research.profile.key_risks = '原记录：资本开支回报的不确定性。'
  research.profile.updated_at = '2026-08-30T08:00:00Z'
  render(<InvestmentOpinionTimeline instrumentId="xlk" research={research} onChange={vi.fn()} />)
  const entries = screen.getAllByRole('listitem')
  expect(within(entries[0]).getByText('等待需求验证')).toBeTruthy()
  expect(within(entries[1]).getByText('谨慎观察')).toBeTruthy()
  expect(screen.getByText('原研究记录 · 2026-08-30')).toBeTruthy()
  expect(screen.getByText(research.profile.current_view)).toBeTruthy()
  expect(screen.getByText(research.profile.key_risks)).toBeTruthy()
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(currentInvestmentOpinion(research)).toBeNull()
  expect(currentInvestmentOpinion({ ...research, notes: [] })).toBeNull()
  expect(api.create).not.toHaveBeenCalled()
})

it('corrects or deletes the selected note through existing APIs while preserving its hidden metadata', async () => {
  const original = note({ note_type: 'risk', summary: '需要验证需求。', completed_at: '2026-09-06T08:00:00Z' })
  const research = { ...emptyInstrumentResearchResponse(), notes: [original] }
  const onChange = vi.fn()
  api.update.mockResolvedValue(research)
  api.remove.mockResolvedValue(emptyInstrumentResearchResponse())
  render(<InvestmentOpinionTimeline instrumentId="xlk" research={research} onChange={onChange} />)
  await waitFor(() => expect(screen.getByRole('button', { name: /更正观点/ })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: /更正观点/ }))
  fireEvent.change(screen.getByRole('textbox', { name: '观点' }), { target: { value: '订单确认仍需两个季度。' } })
  fireEvent.change(screen.getByRole('textbox', { name: '来源（可选）' }), { target: { value: '季度业绩说明会' } })
  fireEvent.click(screen.getByRole('button', { name: '保存观点' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('xlk', 'view-1', {
    updated_by: 'terminal_ui',
    note: {
      note_date: original.note_date, note_type: 'risk', title: original.title, body: '订单确认仍需两个季度。', source_refs: '季度业绩说明会',
      summary: original.summary, importance: original.importance, tags: original.tags, people: original.people, author: original.author,
      follow_up_date: original.follow_up_date, completed_at: original.completed_at,
    },
  }))
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(research))
  await waitFor(() => expect(screen.getByRole('button', { name: /更正观点/ })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: /更正观点/ }))
  fireEvent.click(screen.getByRole('button', { name: '删除记录' }))
  await waitFor(() => expect(api.remove).toHaveBeenCalledWith('xlk', 'view-1'))
  expect(api.create).not.toHaveBeenCalled()
})

it('retains the draft and shows an error when saving fails', async () => {
  api.create.mockRejectedValue(new Error('暂时无法保存'))
  const onChange = vi.fn()
  render(<InvestmentOpinionTimeline instrumentId="xlk" research={emptyInstrumentResearchResponse()} onChange={onChange} />)
  await waitFor(() => expect(screen.getByRole('button', { name: '新增观点' })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: '新增观点' }))
  fireEvent.change(screen.getByRole('textbox', { name: '观点' }), { target: { value: '等待新的订单数据。' } })
  fireEvent.change(screen.getByLabelText('当时背景与依据'), { target: { value: '季度披露前的判断。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存观点' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '暂时无法保存')
  expect(screen.getByRole('textbox', { name: '观点' })).toHaveProperty('value', '等待新的订单数据。')
  expect(onChange).not.toHaveBeenCalled()
})

it('starts a new view at the requested chart date and can return an existing view to its chart date', async () => {
  const handled = vi.fn()
  const openChart = vi.fn()
  render(<InvestmentOpinionTimeline instrumentId="fund-1" research={{ ...emptyInstrumentResearchResponse(), notes: [note()] }} onChange={vi.fn()}
    requestedNoteDate="2026-08-31" onRequestedNoteHandled={handled} onOpenNote={openChart} />)
  expect(await screen.findByLabelText('日期')).toHaveProperty('value', '2026-08-31')
  expect(handled).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  fireEvent.click(screen.getByRole('button', { name: '在业绩图中查看' }))
  expect(openChart).toHaveBeenCalledWith('2026-09-06')
  expect(api.create).not.toHaveBeenCalled()
  expect(api.update).not.toHaveBeenCalled()
})

it('preserves PM provenance on correction and links an explicitly edited context without submitting identity fields', async () => {
  const original = note({ author_user_id: 'user-shaw', research_context: { theme_id: 'gold-credit', background: '当时名义收益率上升。', horizon: '中期', verification: '观察美元与长债。', outcome: '尚待验证', author_role: 'pm', recorded_via: 'assistant', source_run_id: 'chat-1', source_quote: '中期偏多，短期没判断。', research_snapshot: { notebook_version_id: 'notebook-1' }, information_cutoff: '2026-09-05T12:00:00Z' } })
  const research = { ...emptyInstrumentResearchResponse(), notes: [original] }
  api.themes.mockResolvedValue({ identity: { user_id: 'user-shaw', display_name: 'Shaw', mode: 'account', team_role: 'member' }, themes: [{ theme_id: 'gold-credit', title: '美元信用', status: 'active' }] })
  api.update.mockResolvedValue(research)
  render(<InvestmentOpinionTimeline instrumentId="gold" research={research} onChange={vi.fn()} />)
  await waitFor(() => expect(screen.getByRole('button', { name: /更正观点/ })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: /更正观点/ }))
  fireEvent.change(screen.getByRole('textbox', { name: '观点' }), { target: { value: '文字更正。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存观点' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledOnce())
  expect(api.update.mock.calls[0][2].note).not.toHaveProperty('research_context')
  expect(api.update.mock.calls[0][2].note).not.toHaveProperty('author_user_id')
  await screen.findByRole('status')
  await waitFor(() => expect(screen.getByRole('button', { name: /更正观点/ })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: /更正观点/ }))
  fireEvent.click(screen.getByText('关联主题与验证条件（可选）'))
  fireEvent.change(screen.getByRole('textbox', { name: '判断期限' }), { target: { value: '未来三个月' } })
  fireEvent.click(screen.getByRole('button', { name: '保存观点' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledTimes(2))
  expect(api.update.mock.calls[1][2].note.research_context).toEqual({ theme_id: 'gold-credit', background: '当时名义收益率上升。', horizon: '未来三个月', verification: '观察美元与长债。', outcome: '尚待验证' })
})

it('adds a related judgment as a new record and binds assistant review to the original revision', async () => {
  const original = note({ research_context: { theme_id: 'demand' }, revision_number: 3 })
  const research = { ...emptyInstrumentResearchResponse(), notes: [original] }
  api.create.mockResolvedValue(research)
  const ask = vi.fn()
  render(<InvestmentOpinionTimeline instrumentId="xlk" research={research} onChange={vi.fn()} onAskAssistant={ask} />)
  fireEvent.click(screen.getByRole('button', { name: '与助手讨论 / 复盘' }))
  expect(ask).toHaveBeenCalledWith(expect.any(String), { instrument_id: 'xlk', pm_note_id: 'view-1', pm_note_revision: 3, theme_id: 'demand' })
  await waitFor(() => expect(screen.getByRole('button', { name: '补充判断' })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: '补充判断' }))
  fireEvent.change(screen.getByRole('textbox', { name: '观点' }), { target: { value: '新证据使我调整原判断。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存观点' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledWith('xlk', expect.objectContaining({ note: expect.objectContaining({ research_context: expect.objectContaining({ relationship: 'update', related_note_id: 'view-1', related_revision: 3, theme_id: 'demand', background: expect.stringContaining('盈利改善尚需订单确认。') }) }) })))
  expect(api.update).not.toHaveBeenCalled()
})

it('refreshes its theme catalogue after an update to this instrument', async () => {
  const change = vi.fn()
  render(<InvestmentOpinionTimeline instrumentId="xlk" research={emptyInstrumentResearchResponse()} onChange={change} />)
  await waitFor(() => expect(api.themes).toHaveBeenCalledTimes(1))
  announceResearchPublication(['gold'])
  expect(api.themes).toHaveBeenCalledTimes(1)
  announceResearchPublication(['xlk'])
  await waitFor(() => expect(api.themes).toHaveBeenCalledTimes(2))
  expect(change).not.toHaveBeenCalled()
})


it('selects an explicit overall view and never replaces it with a later note or review', async () => {
  const selected = note()
  const review = note({ note_id: 'review', note_type: 'review', title: '事后复盘', note_date: '2026-09-20' })
  const research = { ...emptyInstrumentResearchResponse(), notes: [review, selected] }
  const response = { ...research, current_stance: { selection_id: 'selection', selected_at: '2026-09-23T00:00:00Z', selected_by: 'user-shaw', selected_by_name: 'Shaw', note: selected, has_later_revision: false } }
  api.stance.mockResolvedValue(response)
  const onChange = vi.fn()
  render(<InvestmentOpinionTimeline instrumentId="xlk" research={research} onChange={onChange} />)
  await waitFor(() => expect(screen.getByRole('button', { name: '设为当前总体观点' })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: '设为当前总体观点' }))
  await waitFor(() => expect(api.stance).toHaveBeenCalledWith('xlk', selected))
  expect(currentInvestmentOpinion(response)?.body).toBe(selected.body)
  expect(currentInvestmentOpinion({ ...research, notes: [review] })).toBeNull()
})
