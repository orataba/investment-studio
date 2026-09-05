import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import PortfolioInstrumentRisk from './PortfolioInstrumentRisk'
import {
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
} from '../test/portfolioFixtures'
const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ requestInstrumentRisk: request }))
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
    <PortfolioInstrumentRisk
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
    />,
  )
  await screen.findByText(record.title)
  expect(request).toHaveBeenCalledWith(`/risk?instrument_ids=${id}`, undefined)
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
  expect(
    screen.getByRole('link', { name: '问助手' }).getAttribute('href'),
  ).toContain('portfolio=3')
})
