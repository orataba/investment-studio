import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  ] })
  render(<MemoryRouter><PortfolioInstrumentRisk portfolioId="3" workspace={holdingsWorkspaceFixture({ rows: [holding] })} onAskAssistant={vi.fn()} /></MemoryRouter>)
  await screen.findByText('原风险仍未解除')
  expect(screen.getByText('既有风险有新进展待评估')).toBeInTheDocument()
  expect(screen.getByText('风险仍有效 · 新进展待复核')).toBeInTheDocument()
  expect(screen.queryByText('新线索等待核查')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /待风控复核/ }))
  expect(screen.getByText('新线索等待核查')).toBeInTheDocument()
  expect(screen.getByText('已提交／待复核')).toBeInTheDocument()
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
  expect(request).toHaveBeenCalledWith(`/risk?instrument_ids=${id}`, undefined)
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
  request.mockImplementation(async (path: string) => path === '/risk?instrument_ids='
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
  expect(screen.getByRole('link', { name: record.title })).toHaveAttribute('href', expect.stringContaining('tab=events'))
  fireEvent.click(screen.getByRole('button', { name: '问助手' }))
  expect(ask).toHaveBeenCalledWith('baba', expect.any(String), { instrument_id: 'baba', event_case_id: record.case_id, event_version_id: 'watchlist-baba-policy:2' })
  expect(request).toHaveBeenCalledWith('/risk?instrument_ids=baba', undefined)
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
  await waitFor(() => expect(request).toHaveBeenCalledWith('/risk?instrument_ids=', undefined))
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
