import { fireEvent, render as renderTestingLibrary, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import PortfolioInstrumentRisk from './PortfolioInstrumentRisk'
import PortfolioRiskDrawer from './PortfolioRiskDrawer'
import RiskPanel from '../../../../../packages/ui/src/InstrumentRiskPanel'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import {
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
} from '../test/portfolioFixtures'
const render = (element: React.ReactElement) => renderTestingLibrary(element, { wrapper: ({ children }) => <LanguageProvider enableDomTranslation={false}>{children}</LanguageProvider> })
const request = vi.hoisted(() => vi.fn())
const getHoldingsWorkspace = vi.hoisted(() => vi.fn())
const permissions = vi.hoisted(() => ({ can_write_team_research: true, can_read: true }))
vi.mock('./PortfolioAccessProvider', () => ({ usePortfolioAccess: () => permissions }))
vi.mock('./PortfolioSessionProvider', () => ({ usePortfolioSession: () => permissions }))
vi.mock('../lib/api', () => ({ requestInstrumentRisk: request, getHoldingsWorkspace }))
vi.mock('../../../../../packages/ui/src/RiskOfficerPanel', () => ({ default: ({ scopeQuery, canRun }: { scopeQuery: string; canRun: boolean }) => <span data-testid="officer-scope" data-can-run={canRun}>{scopeQuery}</span> }))
beforeEach(() => { request.mockReset(); getHoldingsWorkspace.mockReset(); Object.assign(permissions, { can_write_team_research: true, can_read: true }) })

it('retains independently assessed risk after research follow-up stops and separates pending leads', async () => {
  const holding = holdingFixture()
  const id = holding.instrument_core!.instrument_id
  const base = { instrument_id: id, signal: 'sector:funding', severity: 'attention', status: 'open',
    created_at: '2026-09-25', updated_at: '2026-09-25', history_json: [] }
  request.mockResolvedValue({ instruments: [{ instrument_id: id, name: '当前持仓' }], cases: [
    { ...base, case_id: 'active', title: '原风险仍未解除', body: '跟进停止不改变风险评估', trigger_active: true,
      evidence_json: { direction: 'opportunity', follow_up: 'none', risk_assessment: { status: 'active' } } },
    { ...base, case_id: 'active-pending', title: '既有风险有新进展待评估', body: '原风险继续有效', trigger_active: true,
      evidence_json: { direction: 'risk', follow_up: 'none', risk_assessment: { status: 'pending' } } },
    { ...base, case_id: 'pending', title: '新线索等待核查', body: '不能冒充已确认风险', trigger_active: false,
      evidence_json: { direction: 'uncertain', follow_up: 'watch', risk_assessment: { status: 'pending' } } },
    { ...base, case_id: 'assessed-pending', title: '风控已核查的线索', body: '独立评估后仍需补充证据', trigger_active: false,
      evidence_json: { event_version_id: 'assessed-pending:2', risk_assessment: { status: 'pending',
        event_version_id: 'assessed-pending:2', run_id: 'risk-run', submitted_at: '2026-09-25T09:00:00Z',
        reviewed_at: '2026-09-25T10:00:00Z', reason: '需等待公司披露确认项目影响。' } } },
  ] })
  render(<MemoryRouter><PortfolioInstrumentRisk portfolioId="3" workspace={holdingsWorkspaceFixture({ rows: [holding] })} onAskAssistant={vi.fn()} /></MemoryRouter>)
  await screen.findByText('原风险仍未解除')
  expect(screen.getByText('既有风险有新进展待评估')).toBeInTheDocument()
  expect(screen.getByText('风险仍有效 · 新进展待复核')).toBeInTheDocument()
  expect(screen.queryByText('新线索等待核查')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /待风控复核/ }))
  expect(screen.getByText('新线索等待核查')).toBeInTheDocument()
  expect(screen.getByText('已提交／待复核')).toBeInTheDocument()
  expect(screen.queryByText('风控已核查的线索')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '待核实 1' }))
  expect(screen.getByText('风控已核查的线索')).toBeInTheDocument()
  expect(screen.getByText('已评估／待核实')).toBeInTheDocument()
  expect(screen.getByText('需等待公司披露确认项目影响。')).toBeInTheDocument()
})

it('keeps team-reader risk records readable without shared write controls and allows portfolio analysis', async () => {
  permissions.can_write_team_research = false
  const holding = holdingFixture()
  request.mockResolvedValue({ instruments: [{ instrument_id: holding.instrument_core!.instrument_id, name: '当前持仓' }], cases: [{
    case_id: 'reader-case', instrument_id: holding.instrument_core!.instrument_id, title: '已记录的风险', body: '已核查的资料',
    signal: 'manual', severity: 'attention', trigger_active: true, status: 'open', created_at: '2026-09-05', updated_at: '2026-09-05', evidence_json: {}, history_json: [],
  }] })
  render(<MemoryRouter><PortfolioInstrumentRisk portfolioId="3" workspace={holdingsWorkspaceFixture({ rows: [holding] })} onAskAssistant={vi.fn()} /></MemoryRouter>)
  await screen.findByText('已记录的风险')
  fireEvent.click(screen.getByText('跟进与证据'))
  expect(screen.queryByRole('textbox', { name: '处理记录' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '保存跟进' })).not.toBeInTheDocument()
  expect(screen.getByTestId('officer-scope')).toHaveAttribute('data-can-run', 'true')
  expect(screen.getByRole('button', { name: '问助手' })).toBeEnabled()
})

it('limits risk attention to actual holdings and writes follow-up to the shared case', async () => {
  const holding = holdingFixture({ risk_eligible: false })
  const id = holding.instrument_core!.instrument_id
  const record = {
    case_id: 'shared-case',
    instrument_id: id,
    title: '需要复核管理人变更',
    body: '待核查策略连续性',
    signal: 'manual',
    severity: 'attention',
    trigger_active: true,
    status: 'open',
    created_at: '2026-09-05',
    updated_at: '2026-09-05',
    evidence_json: {},
    history_json: [],
  }
  request.mockResolvedValue({
    instruments: [{ instrument_id: id, name: '当前持仓' }],
    cases: [record],
  })
  const ask = vi.fn()
  render(
    <MemoryRouter initialEntries={['/portfolios/3/risk?tab=Risk&currency=USD&benchmark=spy&start=2026-01-01&end=2026-09-06&unrelated=value']}><PortfolioInstrumentRisk
      portfolioId="3"
      onAskAssistant={ask}
      workspace={holdingsWorkspaceFixture({
        rows: [
          holding,
          holdingFixture({ instrument_core: null, quantity: 0 }),
          holdingFixture({
            instrument_core: instrumentFixture({ instrument_id: 'cash-cny', instrument_type: 'cash' }),
            quantity: 100,
          }),
        ],
      })}
    /></MemoryRouter>,
  )
  await screen.findByText(record.title)
  expect(screen.getByRole('region', { name: 'Holding risk watch' })).toBeInTheDocument()
  expect(screen.getByText('Current portfolio holdings')).toBeInTheDocument()
  expect(screen.queryByText('当前组合持仓')).not.toBeInTheDocument()
  expect(request).toHaveBeenCalledWith(`/risk?instrument_ids=${id}&summary=true`, undefined)
  expect(screen.getByTestId('officer-scope').textContent).toBe('portfolio_id=3')
  fireEvent.click(screen.getByText('跟进与证据'))
  fireEvent.change(screen.getByRole('textbox', { name: '处理记录' }), {
    target: { value: '已联系管理人' },
  })
  fireEvent.click(screen.getByRole('button', { name: '保存跟进' }))
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/risk/cases/shared-case',
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('已联系管理人'),
      }),
    ),
  )
  expect(screen.queryByRole('link', { name: '问助手' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '问助手' }))
  expect(ask).toHaveBeenCalledWith(id, expect.stringContaining(record.title), { instrument_id: id, risk_case_id: record.case_id, risk_case_updated_at: record.updated_at })
})

it('renders the same research risk and sources for a held instrument and removes it after exit', async () => {
  const holding = holdingFixture({
    instrument_core: instrumentFixture({ instrument_id: 'baba', instrument_type: 'equity', instrument_name: '阿里巴巴' }),
    quantity: 2,
    risk_eligible: false,
  })
  const record = {
    case_id: 'watchlist-baba-policy',
    instrument_id: 'baba',
    title: '阿里政策影响仍待核实',
    body: '研究员已保存的判断：关注政策对阿里业务的具体影响，等待公司披露。',
    signal: 'sector:policy',
    severity: 'attention',
    trigger_active: true,
    status: 'open',
    created_at: '2026-09-06',
    updated_at: '2026-09-06',
    evidence_json: {
      direction: 'risk',
      confidence: 'reported',
      event_version_id: 'watchlist-baba-policy:2',
      sources: [{ title: '已留存的公司公告', url: 'https://example.com/baba-disclosure', published_at: '2026-09-06T09:00:00+08:00' }],
    },
    history_json: [],
  }
  request.mockImplementation(async (path: string) => path === '/risk?instrument_ids=&summary=true'
    ? { instruments: [], cases: [] }
    : {
      instruments: [{ instrument_id: 'baba', name: '阿里巴巴' }],
      cases: [
        record,
        { ...record, case_id: 'opportunity', title: '未列入风险的机会', evidence_json: { direction: 'opportunity' } },
        { ...record, case_id: 'resolved', title: '已经解除的风险', status: 'resolved', trigger_active: false },
        { ...record, case_id: 'handled', title: '已经处理的事项', status: 'handled' },
      ],
    })
  const soldHolding = holdingFixture({
    instrument_core: instrumentFixture({ instrument_id: 'sold-equity', instrument_type: 'equity' }),
    quantity: 0,
  })
  const ask = vi.fn()
  const view = (quantity: number) => <MemoryRouter><PortfolioInstrumentRisk
    portfolioId="3"
    onAskAssistant={ask}
    workspace={holdingsWorkspaceFixture({ rows: [{ ...holding, quantity }, soldHolding] })}
  /></MemoryRouter>
  const { rerender } = render(view(2))

  await screen.findByText(record.body)
  expect(screen.getByRole('link', { name: record.title })).toHaveAttribute('href', expect.stringContaining('tab=investment-research&risk=1'))
  fireEvent.click(screen.getByRole('button', { name: '问助手' }))
  expect(ask).toHaveBeenCalledWith('baba', expect.any(String), { instrument_id: 'baba', event_case_id: record.case_id, event_version_id: 'watchlist-baba-policy:2' })
  expect(request).toHaveBeenCalledWith('/risk?instrument_ids=baba&summary=true', undefined)
  for (const title of ['未列入风险的机会', '已经解除的风险', '已经处理的事项']) {
    expect(screen.queryByText(title)).not.toBeInTheDocument()
  }
  fireEvent.click(screen.getByText('跟进与证据'))
  expect(screen.getByRole('link', { name: record.evidence_json.sources[0].title })).toHaveAttribute('href', record.evidence_json.sources[0].url)
  expect(screen.getByText(`发布时间 ${record.evidence_json.sources[0].published_at}`)).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: '处理记录' }), { target: { value: '组合已核对该公告' } })
  fireEvent.click(screen.getByRole('button', { name: '保存跟进' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith(`/risk/cases/${record.case_id}`, expect.objectContaining({ method: 'PUT', body: expect.stringContaining('组合已核对该公告') })))

  rerender(view(0))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/risk?instrument_ids=&summary=true', undefined))
  await waitFor(() => expect(screen.queryByText(record.body)).not.toBeInTheDocument())
})

it('opens the assistant from the risk drawer without closing or resetting the risk panel', async () => {
  const holding = holdingFixture()
  const id = holding.instrument_core!.instrument_id
  getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({ rows: [holding] }))
  request.mockResolvedValue({
    instruments: [{ instrument_id: id, name: '当前持仓' }],
    cases: [{ case_id: 'case', instrument_id: id, title: '价格影响待核实', body: '等待披露', signal: 'manual', severity: 'attention', trigger_active: true, status: 'open', updated_at: '2026-09-12T08:00:00Z', evidence_json: {}, history_json: [] }],
  })
  const ask = vi.fn(), close = vi.fn()
  render(<LanguageProvider enableDomTranslation={false}><PortfolioRiskDrawer portfolioId="3" onClose={close} onAskAssistant={ask} /></LanguageProvider>)
  await screen.findByText('价格影响待核实')
  const drawer = screen.getByRole('dialog')
  fireEvent.click(screen.getByText('跟进与证据'))
  fireEvent.change(screen.getByRole('textbox', { name: '处理记录' }), { target: { value: '尚未保存的跟进' } })
  fireEvent.click(screen.getByRole('button', { name: '问助手' }))
  expect(ask).toHaveBeenCalledWith(id, expect.stringContaining('价格影响待核实'), { instrument_id: id, risk_case_id: 'case', risk_case_updated_at: '2026-09-12T08:00:00Z' })
  expect(close).not.toHaveBeenCalled()
  expect(screen.getByRole('dialog')).toBe(drawer)
  expect(screen.getByRole('textbox', { name: '处理记录' })).toHaveValue('尚未保存的跟进')
})

it('omits the assistant action when a risk panel has neither a callback nor a link', async () => {
  request.mockResolvedValue({
    instruments: [{ instrument_id: 'a', name: '标的 A' }],
    cases: [{ case_id: 'a', instrument_id: 'a', title: '待核查风险', body: '等待披露', signal: 'manual', severity: 'attention', trigger_active: true, status: 'open', evidence_json: {}, history_json: [] }],
  })
  render(<RiskPanel request={request} instrumentHref={(id) => `/instruments/${id}`} />)
  await screen.findByText('待核查风险')
  expect(screen.queryByText('问助手')).not.toBeInTheDocument()
})


it('separates a retained assessment from a newer pending event and binds each evidence list to its own version', async () => {
  const old = { event_version_id: 'event-v1', title: '已确认的融资风险', body: '旧公告确认再融资窗口收窄。', status: 'active', reason: '独立复核后继续关注。', reviewed_at: '2026-09-05T12:00:00Z', checked_at: '2026-09-05T08:00:00Z',
    sources: [{ source_id: 'source-v1', version_id: 'doc-v1', title: '旧融资公告', url: 'https://example.com/old', published_at: '2026-09-05' }] }
  const pending = { event_version_id: 'event-v2', title: '传闻称融资取得新进展', body: '新进展尚待核实。', status: 'pending', checked_at: '2026-10-12T08:00:00Z', submitted_at: '2026-10-12T08:00:00Z', urgent_review_required: false,
    sources: [{ source_id: 'source-v2', version_id: 'doc-v2', title: '新融资报道', url: 'https://example.com/new', published_at: '2026-10-12' }] }
  const record = { case_id: 'a', instrument_id: 'a', title: pending.title, body: pending.body, signal: 'sector:financing', severity: 'attention', trigger_active: true, status: 'open', updated_at: '2026-10-12', evidence_json: { direction: 'risk', event_version_id: 'event-v2', risk_assessment: { status: 'pending' }, last_assessed_snapshot: old, pending_revision: pending, sources: pending.sources }, history_json: [] }
  request.mockResolvedValue({ instruments: [{ instrument_id: 'a', name: '标的 A' }], cases: [record] })
  render(<RiskPanel request={request} instrumentHref={(id) => `/instruments/${id}`} />)
  await screen.findByRole('heading', { name: old.title })
  expect(screen.getByText(old.body)).toBeInTheDocument()
  const next = screen.getByRole('region', { name: '新进展待复核' })
  expect(within(next).getByText(pending.title)).toBeInTheDocument()
  expect(within(next).getByText(pending.body)).toBeInTheDocument()
  expect(screen.getByText(/^上次确认 /, { selector: 'p' }).querySelector('time')?.dateTime).toBe(old.reviewed_at)
  fireEvent.click(screen.getByText('跟进与证据'))
  const priorEvidence = screen.getByRole('region', { name: '上次确认的依据' })
  expect(within(priorEvidence).getByRole('link', { name: '旧融资公告' })).toHaveAttribute('href', 'https://example.com/old')
  expect(within(priorEvidence).queryByText('新融资报道')).not.toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: '新进展的依据' })).getByRole('link', { name: '新融资报道' })).toHaveAttribute('href', 'https://example.com/new')
})

it('labels a withdrawn finding as invalidated history even if an old active snapshot exists', async () => {
  request.mockResolvedValue({ instruments: [{ instrument_id: 'a', name: '标的 A' }], cases: [{ case_id: 'a', instrument_id: 'a', title: '已撤回的融资线索', body: '原稿不能作为现状。', signal: 'sector:financing', severity: 'observation', trigger_active: false, status: 'open', updated_at: '2026-10-12', evidence_json: { withdrawn: true, withdrawal_reason: '引述未获原始资料支持。', last_assessed_snapshot: { title: '旧确认', status: 'active' } }, history_json: [] }] })
  render(<RiskPanel request={request} instrumentHref={(id) => `/instruments/${id}`} />)
  // General/history cases are a user-selected scope, never current attention.
  fireEvent.click(await screen.findByRole('button', { name: /全部记录/ }))
  expect(await screen.findByText('已撤回')).toBeInTheDocument()
  fireEvent.click(screen.getByText('撤回前记录与来源'))
  expect(screen.getByText('以下为已撤回的旧稿，不再作为当前风险判断。')).toBeInTheDocument()
  expect(screen.queryByText('风险仍有效 · 新进展待复核')).not.toBeInTheDocument()
  expect(screen.queryByText('旧确认')).not.toBeInTheDocument()
})

it('loads selected portfolio risk independently when holdings context is unavailable', async () => {
  getHoldingsWorkspace.mockRejectedValueOnce(new Error('持仓上下文读取失败'))
  render(<PortfolioRiskDrawer portfolioId="3" onClose={vi.fn()} onAskAssistant={vi.fn()} />)
  expect(screen.getByTestId('officer-scope')).toHaveTextContent('portfolio_id=3')
  expect(await screen.findByText('持仓上下文读取失败')).toBeInTheDocument()
  expect(screen.getByTestId('officer-scope')).toHaveTextContent('portfolio_id=3')
})
