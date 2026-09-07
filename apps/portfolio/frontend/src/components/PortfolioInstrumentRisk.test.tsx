import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import PortfolioInstrumentRisk from './PortfolioInstrumentRisk'
import {
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
} from '../test/portfolioFixtures'
const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ requestInstrumentRisk: request }))
vi.mock('../../../../../packages/ui/src/RiskOfficerPanel', () => ({ default: ({ scopeQuery }: { scopeQuery: string }) => <span data-testid="officer-scope">{scopeQuery}</span> }))
beforeEach(() => request.mockReset())

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
  render(
    <MemoryRouter initialEntries={['/portfolios/3/risk?tab=Risk&currency=USD&benchmark=spy&start=2026-01-01&end=2026-09-06&unrelated=value']}><PortfolioInstrumentRisk
      portfolioId="3"
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
  const link = screen.getByRole('link', { name: '问助手' }) as HTMLAnchorElement
  const params = new URL(link.href).searchParams
  expect(Object.fromEntries(params)).toEqual({
    portfolio: '3', instruments: id, question: expect.any(String), tab: 'Risk', currency: 'USD', benchmark: 'spy', start: '2026-01-01', end: '2026-09-06',
  })
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
  const view = (quantity: number) => <MemoryRouter><PortfolioInstrumentRisk
    portfolioId="3"
    workspace={holdingsWorkspaceFixture({ rows: [{ ...holding, quantity }, soldHolding] })}
  /></MemoryRouter>
  const { rerender } = render(view(2))

  await screen.findByText(record.body)
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
