import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PortfolioInstrumentEventTaskRecord } from '../lib/api'
import FundDistributionTasksPanel from './FundDistributionTasksPanel'


const apiMocks = vi.hoisted(() => ({
  getPortfolioInstrumentEventTasks: vi.fn(),
  reconcilePortfolioInstrumentEventTasks: vi.fn(),
  reviewPortfolioInstrumentEventTask: vi.fn(),
}))

vi.mock('../lib/api', () => apiMocks)

const pendingTask: PortfolioInstrumentEventTaskRecord = {
  instrument_event_task_id: 'task-1',
  portfolio_id: 'portfolio-1',
  account_id: 'account-1',
  instrument_id: 'fund-1',
  instrument_name: 'Example Private Fund',
  event_source: 'instrument_registry.fund_nav_action',
  event_action_id: 'distribution-1',
  current_event_revision_id: 'event-1',
  event_type: 'cash_distribution',
  source_revision_kind: 'original',
  source_event_state: 'active',
  announcement_date: '2026-04-08',
  record_date: '2026-04-10',
  effective_date: '2026-04-10',
  payable_date: '2026-04-15',
  cash_per_unit: 0.1,
  unit_ratio: null,
  reinvestment_nav: 1.05,
  entitled_quantity: 304.236,
  expected_gross_amount: 30.4236,
  resolution_status: 'pending',
  reviewed_event_revision_id: null,
  resolution_note: null,
  resolved_by: null,
  resolved_at: null,
  status: 'pending',
  attention_required: true,
  attention_reason: 'Record and link the cash distribution or dividend reinvestment.',
  linked_transactions: [],
  row_version: 1,
  created_at: '2026-04-15T08:00:00Z',
  updated_at: '2026-04-15T08:00:00Z',
}

describe('FundDistributionTasksPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getPortfolioInstrumentEventTasks.mockResolvedValue({
      portfolio_id: 'portfolio-1',
      accounting_policy: 'official_unit_nav_assume_no_unrecorded_distribution',
      attention_count: 1,
      tasks: [pendingTask],
    })
    apiMocks.reconcilePortfolioInstrumentEventTasks.mockResolvedValue({
      portfolio_id: 'portfolio-1',
      accounting_policy: 'official_unit_nav_assume_no_unrecorded_distribution',
      attention_count: 1,
      tasks: [pendingTask],
    })
  })

  it('shows confirmed distribution facts and delegates manual transaction entry', async () => {
    const user = userEvent.setup()
    const onRecord = vi.fn()
    render(
      <FundDistributionTasksPanel
        portfolioId="portfolio-1"
        accountNames={{ 'account-1': 'Private Funds Account' }}
        refreshKey={0}
        onRecord={onRecord}
      />,
    )

    expect(await screen.findByText('Example Private Fund')).toBeInTheDocument()
    expect(screen.getByText(/Private Funds Account · entitlement 2026-04-10/)).toBeInTheDocument()
    expect(screen.getByText('Expected gross 30.42')).toBeInTheDocument()
    expect(
      screen.getByText('Confirmed distribution events never post cash or units automatically.'),
    ).toBeInTheDocument()

    const recordButton = screen.getByRole('button', { name: 'Record Cash Dividend' })
    expect(recordButton).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Operator identity' }), 'alice.ops')
    expect(recordButton).toBeEnabled()
    await user.click(recordButton)
    expect(onRecord).toHaveBeenCalledWith(pendingTask, 'dividend', 'alice.ops')
  })

  it('only reconciles Registry events after an explicit operator action', async () => {
    const user = userEvent.setup()
    apiMocks.getPortfolioInstrumentEventTasks.mockResolvedValueOnce({
      portfolio_id: 'portfolio-1',
      accounting_policy: 'official_unit_nav_assume_no_unrecorded_distribution',
      attention_count: 0,
      tasks: [],
    })

    render(
      <FundDistributionTasksPanel
        portfolioId="portfolio-1"
        accountNames={{}}
        refreshKey={0}
        onRecord={vi.fn()}
      />,
    )

    expect(
      await screen.findByText('No distribution events currently need attention.'),
    ).toBeInTheDocument()
    expect(apiMocks.reconcilePortfolioInstrumentEventTasks).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Refresh Distribution Events' }))

    expect(apiMocks.reconcilePortfolioInstrumentEventTasks).toHaveBeenCalledWith(
      'portfolio-1',
      true,
    )
    expect(await screen.findByText('Example Private Fund')).toBeInTheDocument()
  })
})
