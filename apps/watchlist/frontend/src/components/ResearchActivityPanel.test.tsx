// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchActivityPanel from './ResearchActivityPanel'
import ResearchUpdateCard from './ResearchUpdateCard'
import type { ResearchUpdate } from '../lib/researchDossierApi'
import { announceResearchPublication } from '../lib/researchUpdates'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import { researchMessages, researchPatterns } from '../researchMessages'

const mocks = vi.hoisted(() => ({ get: vi.fn(), account: vi.fn() }))
vi.mock('../lib/researchDossierApi', async importOriginal => ({ ...await importOriginal<typeof import('../lib/researchDossierApi')>(), getResearchActivity: mocks.get }))
vi.mock('./AccountBoundary', () => ({ useStudioAccount: mocks.account }))
const update = (overrides: Partial<ResearchUpdate> = {}): ResearchUpdate => ({
  update_id: 'event:case-1:2', kind: 'event', title: '旧收购事件的新判断', body: '新披露的整合费用改变了此前对现金回报的判断。',
  recorded_at: '2026-09-09T09:00:00+08:00', occurred_at: '2026-08-01', published_at: '2026-08-02',
  author: '研究员', author_role: 'researcher', theme_ids: [], sources: [], analysis_depth: 'analysis', follow_up: 'watch', next_check: '核实整合成本是否继续增加。',
  reference: { instrument_id: 'xlk', event_case_id: 'case-1', event_version_id: 'case-1:2', research_update_id: 'event:case-1:2' }, ...overrides,
})
beforeEach(() => { vi.resetAllMocks(); mocks.account.mockReturnValue({ team_role: 'member' }); vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-09-09T12:00:00+08:00')); mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [] }) })
afterEach(() => { cleanup(); vi.useRealTimers(); window.history.replaceState(null, '', '/') })

it('places new research about old events in today’s timeline while retaining the actual event dates', async () => {
  const earlier = update({ update_id: 'lesson:1', kind: 'lesson', title: '昨日形成的经验', recorded_at: '2026-09-08T12:00:00+08:00' })
  mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [earlier, update()] })
  const { container } = render(<ResearchActivityPanel instrumentId="xlk" />)
  await screen.findByRole('heading', { name: update().title })
  expect([...container.querySelectorAll('[data-update-id]')].map(node => node.getAttribute('data-update-id'))).toEqual([update().update_id, earlier.update_id])
  const event = screen.getByRole('heading', { name: update().title }).closest('article')!
  expect(within(event).getByText('2026-08-01').closest('time')?.dateTime).toBe('2026-08-01')
  expect(within(event).getByText('2026-08-02').closest('time')?.dateTime).toBe('2026-08-02')
  expect(container.querySelector('.research-activity-date time')?.getAttribute('datetime')).toBe('2026-09-09')
  expect(screen.queryByText('补录与时间待核实')).toBeNull()
  fireEvent.change(screen.getByLabelText('更新类型'), { target: { value: 'lesson' } })
  expect(screen.queryByRole('heading', { name: update().title })).toBeNull()
  expect(screen.getByRole('heading', { name: earlier.title })).toBeTruthy()
})

it('filters by the displayed local research day at the seven-day boundary', async () => {
  const boundary = new Date(2026, 8, 3, 0, 30).toISOString()
  const outside = new Date(2026, 8, 2, 23, 59).toISOString()
  mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [update({ recorded_at: boundary }), update({ update_id: 'old', title: '窗口外判断', recorded_at: outside })] })
  render(<ResearchActivityPanel instrumentId="xlk" />)
  await screen.findByRole('heading', { name: update().title })
  expect(screen.queryByRole('heading', { name: '窗口外判断' })).toBeNull()
  fireEvent.change(screen.getByLabelText('浏览范围'), { target: { value: '0' } })
  expect(screen.getByRole('heading', { name: '窗口外判断' })).toBeTruthy()
})

it('separates withdrawn and superseded records from current research without losing their references', async () => {
  const old = update({ update_id: 'event:old', title: '已纠正的判断', body: '当时错误的推论。', superseded: true })
  const withdrawn = update({ update_id: 'event:withdrawn', title: '已撤回的判断', withdrawn: true })
  mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [old, withdrawn, update()] })
  const ask = vi.fn()
  render(<ResearchActivityPanel instrumentId="xlk" onAskAssistant={ask} />)
  await screen.findByRole('heading', { name: update().title })
  expect(screen.queryByText(old.body)).toBeNull()
  fireEvent.click(screen.getByRole('checkbox'))
  const article = screen.getByText(old.title).closest('article')!
  expect(within(article).getByText('已修订 · 当时版本')).toBeTruthy()
  expect(within(article).getByText(old.body).closest('details')?.open).toBe(false)
  fireEvent.click(within(article).getByText('查看当时记录'))
  fireEvent.click(within(article).getByRole('button', { name: '讨论当时判断' }))
  expect(ask).toHaveBeenCalledWith(expect.stringContaining('已被修订或撤回'), { ...old.reference, research_update_id: old.update_id })
  expect(within(article).queryByRole('button', { name: '写投资观点' })).toBeNull()
  expect(screen.getByText('已撤回 · 仅供追溯')).toBeTruthy()
})

it('uses exact update and event versions for questions, themes and the user’s explicitly entered opinion', () => {
  const ask = vi.fn()
  render(<ResearchUpdateCard update={update()} onAskAssistant={ask} />)
  fireEvent.click(screen.getByRole('button', { name: '追问这条更新' }))
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), update().reference)
  fireEvent.click(screen.getByRole('button', { name: '请助手建立主题' }))
  expect(ask).toHaveBeenLastCalledWith(expect.stringContaining('避免重复'), update().reference)
  fireEvent.click(screen.getByRole('button', { name: '写投资观点' }))
  expect(screen.getByLabelText('我的投资判断')).toHaveProperty('value', '')
  expect(screen.getByRole('button', { name: '交给助手保存' }).hasAttribute('disabled')).toBe(true)
  fireEvent.change(screen.getByLabelText('我的投资判断'), { target: { value: '整合成本上升，但目前不改变我的中期判断。' } })
  fireEvent.click(screen.getByRole('button', { name: '交给助手保存' }))
  expect(ask).toHaveBeenLastCalledWith(expect.stringContaining('以下是我的判断：\n整合成本上升，但目前不改变我的中期判断。'), update().reference)
})

it.each(['question', 'judgment'] as const)('identifies a %s citation correction without changing the original assessment or date', async kind => {
  const originalTime = '2026-08-01T09:00:00+08:00'
  const corrected = update({
    update_id: 'research:correction-1', kind, change: 'citation_corrected', author: '系统', author_role: 'system',
    title: '原判断标题', body: '原判断正文保持原样。', follow_up: undefined,
    occurred_at: undefined, published_at: undefined,
    sources: [{ source_id: 'original', title: '已核证依据', url: 'https://example.com/original' }],
    reference: { instrument_id: '600036-sh', research_update_id: 'research:correction-1' },
    citation_correction: { source_run_id: 'old-run', source_notebook_version_id: 'old-notebook',
      source_update_id: 'research:old', reason: '恢复独立核证结果中已保存的依据引用。',
      corrected_at: update().recorded_at, original_recorded_at: originalTime },
  })
  mocks.get.mockResolvedValue({ instrument_id: '600036-sh', updates: [corrected] })
  const ask = vi.fn()
  render(<ResearchActivityPanel instrumentId="600036-sh" onAskAssistant={ask} />)
  const article = (await screen.findByRole('heading', { name: '原判断标题' })).closest('article')!
  expect(within(article).getByText('引用修正')).toBeTruthy()
  expect(within(article).getByText('系统')).toBeTruthy()
  expect(within(article).queryByText('研究员记录')).toBeNull()
  expect(within(article).getByText('原判断正文保持原样。')).toBeTruthy()
  expect(within(article).getByText(/^修正时间/).querySelector('time')?.dateTime).toBe(corrected.recorded_at)
  expect(within(article).getByText(/^原判断时间/).querySelector('time')?.dateTime).toBe(originalTime)
  expect(within(article).getByText('原下一步观察')).toBeTruthy()
  expect(within(article).getByText(corrected.citation_correction!.reason)).toBeTruthy()
  fireEvent.click(within(article).getByText('分析与研究依据'))
  expect(within(article).getByRole('link', { name: '已核证依据' }).getAttribute('href')).toBe('https://example.com/original')
  fireEvent.click(within(article).getByRole('button', { name: '追问这条更新' }))
  expect(ask).toHaveBeenLastCalledWith(expect.any(String), corrected.reference)
})

it('lets readers discuss records while keeping shared opinion and theme writes unavailable', () => {
  mocks.account.mockReturnValue({ team_role: 'reader' })
  render(<ResearchUpdateCard update={update()} onAskAssistant={vi.fn()} />)
  expect(screen.getByRole('button', { name: '追问这条更新' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: '写投资观点' })).toBeNull()
  expect(screen.queryByRole('button', { name: '请助手建立主题' })).toBeNull()
})

it('refreshes only the relevant instrument and retains current records when a later load fails', async () => {
  mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [update()] })
  render(<ResearchActivityPanel instrumentId="xlk" />)
  await screen.findByRole('heading', { name: update().title })
  await act(async () => announceResearchPublication(['other']))
  expect(mocks.get).toHaveBeenCalledTimes(1)
  mocks.get.mockRejectedValue(new Error('资料服务暂不可用'))
  await act(async () => announceResearchPublication(['xlk']))
  expect(screen.getByRole('heading', { name: update().title })).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('资料服务暂不可用')
})

it('shows brief events without inventing follow-up requirements and rejects unsafe source links', () => {
  render(<ResearchUpdateCard update={update({ analysis_depth: 'brief', follow_up: 'none', next_check: '', sources: [{ source_id: 'link', title: '不安全链接', url: 'javascript:alert(1)' }] })} />)
  expect(screen.getByText('简讯').parentElement?.textContent).toBe('事件 · 简讯')
  expect(screen.getByText('当时无需跟进')).toBeTruthy()
  expect(screen.queryByText('下一步观察')).toBeNull()
  fireEvent.click(screen.getByText('分析与研究依据'))
  expect(screen.queryByRole('link', { name: '不安全链接' })).toBeNull()
})

it('translates activity controls while preserving the saved analyst text', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [update({ title: 'Risk', body: 'Research' })] })
  render(<LanguageProvider messages={researchMessages} patterns={researchPatterns}><LanguageSelector /><ResearchActivityPanel instrumentId="xlk" onAskAssistant={vi.fn()} /></LanguageProvider>)
  await screen.findByRole('region', { name: 'Research activity' })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Discuss this update' })).toBeTruthy())
  expect(screen.getByRole('heading', { name: 'Risk' })).toBeTruthy()
  expect(screen.getByText('Research')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await screen.findByRole('region', { name: '研究动态' })
  expect(screen.getByRole('heading', { name: 'Risk' })).toBeTruthy()
  expect(screen.getByText('Research')).toBeTruthy()
})

it.each(['collection', 'published'])('shows FMP estimate fiscal periods and the retained clock (%s)', async (sourceKind) => {
  const previousCollected = '2026-09-04T08:00:00+08:00'
  const currentCollected = '2026-09-05T08:00:00+08:00'
  mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [update({ sources: [{
    source_id: 'estimates:run-1:xlk-us', source_type: 'analyst_estimate_changes',
    previous_snapshot: sourceKind === 'collection' ? { observation_id: 'observation-0', collected_at: '2026-09-05T09:00:00+08:00' }
      : { run_id: 'run-0', read_at: '2026-09-05T09:00:00+08:00', cutoff: '2026-09-05T08:00:00+08:00' },
    current_snapshot: sourceKind === 'collection' ? { observation_id: 'observation-1', collected_at: '2026-09-06T09:00:00+08:00' }
      : { run_id: 'run-1', read_at: '2026-09-06T09:00:00+08:00', cutoff: '2026-09-06T08:00:00+08:00' },
    changes: [{ symbol: 'MSFT', name: 'Microsoft', frequency: 'annual', target_period_end: '2027-06-30', metric: 'eps_avg', currency: 'USD',
      previous_value: 12, current_value: 13.2, delta_pct: 10, previous_collected_at: previousCollected,
      current_collected_at: currentCollected, analyst_count_changed: true }],
  }] })] })
  render(<ResearchActivityPanel instrumentId="xlk" />)
  await screen.findByRole('heading', { name: update().title })
  fireEvent.click(screen.getByText('分析与研究依据'))
  const source = screen.getByText('已保存的来源记录').closest('li')!
  expect(within(source).getByText(sourceKind === 'collection' ? /^快照采集：前次/ : /^快照读取：前次/)).toBeTruthy()
  fireEvent.click(within(source).getByText('同财期预期变动 · 1 项'))
  expect(within(source).getByText('MSFT · Microsoft · 年度财期截至 2027-06-30')).toBeTruthy()
  expect(within(source).getByText('平均每股收益预期：12 → 13.2 USD/股 · +10.00%')).toBeTruthy()
  expect(within(source).getByText(/^源数据采集区间：/)).toBeTruthy()
  expect(Array.from(source.querySelectorAll('time')).map((node) => node.dateTime)).toEqual([previousCollected, currentCollected])
  expect(within(source).getByText('分析师样本数量发生变化，共识变化不等于每位分析师均调整预测。')).toBeTruthy()
  expect(within(source).queryByText(/^原文发布/)).toBeNull()
  expect(within(source).queryByRole('link')).toBeNull()
})

it('shows computed volatility observations separately from publication dates and avoids inventing values for other calculations', async () => {
  mocks.get.mockResolvedValue({ instrument_id: 'xlk', updates: [update({ sources: [
    { source_id: 'risk-metric', source_type: 'computed_metric', title: 'XLK EWMA 波动观察', as_of: '2026-09-06T08:00:00+08:00',
      measurement: { current: { date: '2026-09-04', volatility_pct: 24.5 }, previous: { date: '2026-09-03', volatility_pct: 22.5 }, change_pp: 2, five_session_change_pp: 3.2 },
      methodology: { half_life_sessions: 21, annualization: 252 } },
    { source_id: 'macro-comparison', source_type: 'computed_metric', title: '宏观序列比较', as_of: '2026-09-06T08:00:00+08:00', measurement: { current: null } },
  ] })] })
  render(<ResearchActivityPanel instrumentId="xlk" />)
  await screen.findByRole('heading', { name: update().title })
  fireEvent.click(screen.getByText('分析与研究依据'))
  const risk = screen.getByText('XLK EWMA 波动观察').closest('li')!
  expect(within(risk).getByText(/当前波动率 24.50%/)).toBeTruthy()
  expect(within(risk).getByText('较前次 +2.00 个百分点')).toBeTruthy()
  expect(within(risk).getByText('近 5 个交易观察 +3.20 个百分点')).toBeTruthy()
  expect(Array.from(risk.querySelectorAll('time')).map(node => node.dateTime)).toEqual(['2026-09-06T08:00:00+08:00', '2026-09-04'])
  const macro = screen.getByText('宏观序列比较').closest('li')!
  expect(within(macro).getByText(/宏观序列比较/)).toBeTruthy()
  expect(within(macro).queryByText(/波动率/)).toBeNull()
  expect(within(risk).queryByText(/原文发布/)).toBeNull()
  expect(within(macro).queryByText(/原文发布/)).toBeNull()
})



it('makes status-only forecast revisions and calendar outcomes visible and preserves historical clocks', () => {
  const { rerender } = render(<ResearchUpdateCard update={update({ kind: 'forecast', status: 'refuted', body: '事前预测。' })} />)
  expect(screen.getByText('预测未成立')).toBeTruthy()
  rerender(<ResearchUpdateCard update={update({ kind: 'question', status: 'refuted' })} />)
  expect(screen.getByText('当时证据不支持')).toBeTruthy()
  rerender(<ResearchUpdateCard update={update({ kind: 'schedule', status: 'scheduled', scheduled_at: '2026-09-09' })} />)
  expect(screen.getByText('预定事件')).toBeTruthy()
  expect(screen.queryByText('结果待核实')).toBeNull()
  rerender(<ResearchUpdateCard update={update({ kind: 'schedule', status: 'scheduled', scheduled_at: '2026-09-08' })} />)
  expect(screen.getByText('结果待核实')).toBeTruthy()
  rerender(<ResearchUpdateCard update={update({ kind: 'schedule', status: 'scheduled', recorded_at: '2026-09-06T08:00:00+08:00', scheduled_at: '2026-09-08', superseded: true })} />)
  expect(screen.getByText('预定事件')).toBeTruthy()
  expect(screen.queryByText('结果待核实')).toBeNull()
  rerender(<ResearchUpdateCard update={update({ kind: 'schedule', status: 'cancelled', scheduled_at: '2026-09-09' })} />)
  expect(screen.getByText('已取消')).toBeTruthy()
})
