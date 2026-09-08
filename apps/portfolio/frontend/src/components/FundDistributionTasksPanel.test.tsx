import { render as renderComponent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReactNode } from 'react'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'

import type { PortfolioInstrumentEventTaskRecord } from '../lib/api'
import FundDistributionTasksPanel from './FundDistributionTasksPanel'


function render(ui: ReactNode) {
  return renderComponent(<LanguageProvider enableDomTranslation={false}>{ui}</LanguageProvider>)
}

const accessState = vi.hoisted(() => ({ can_edit: true }))
vi.mock('./PortfolioAccessProvider', () => ({ usePortfolioAccess: () => accessState }))
vi.mock('./PortfolioSessionProvider', () => ({ usePortfolioSession: () => ({ display_name: 'alice.ops' }) }))

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
    accessState.can_edit = true
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

  it('shows distribution facts to readers without allowing review or reconciliation', async () => {
    accessState.can_edit = false
    const onRecord = vi.fn()
    render(<FundDistributionTasksPanel portfolioId="portfolio-1" accountNames={{ 'account-1': 'Private Funds Account' }} refreshKey={0} onRecord={onRecord} />)
    await userEvent.click(await screen.findByRole('button', { name: /Fund distribution reviews: 1 need attention/ }))
    expect(await screen.findByText('Example Private Fund')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh Distribution Events' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Record Cash Dividend' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Mark Not Applicable' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Operator identity' })).toBeDisabled()
    expect(apiMocks.reconcilePortfolioInstrumentEventTasks).not.toHaveBeenCalled()
    expect(apiMocks.reviewPortfolioInstrumentEventTask).not.toHaveBeenCalled()
    expect(onRecord).not.toHaveBeenCalled()
  })

  it.each([['Record Cash Dividend', 'dividend'], ['Record Reinvestment', 'dividend_reinvestment']] as const)('shows confirmed distribution facts and delegates %s', async (buttonLabel, transactionType) => {
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

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('Example Private Fund')).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /Fund distribution reviews: 1 need attention/ }))
    expect(await screen.findByText('Example Private Fund')).toBeInTheDocument()
    expect(screen.getByText(/Private Funds Account · entitlement 2026-04-10/)).toBeInTheDocument()
    expect(screen.getByText('Expected gross 30.42')).toBeInTheDocument()
    expect(
      screen.getByText('Confirmed distribution events never post cash or units automatically.'),
    ).toBeInTheDocument()

    const recordButton = screen.getByRole('button', { name: buttonLabel })
    expect(screen.getByRole('textbox', { name: 'Operator identity' })).toHaveValue('alice.ops')
    expect(screen.getByRole('textbox', { name: 'Operator identity' })).toHaveAttribute('readonly')
    expect(recordButton).toBeEnabled()
    await user.click(recordButton)
    expect(onRecord).toHaveBeenCalledWith(pendingTask, transactionType, 'alice.ops')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
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

    const trigger = await screen.findByRole('button', { name: /Fund distribution reviews: No distribution events/ })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('No distribution events currently need attention.')).not.toBeInTheDocument()
    expect(trigger.querySelector('.fund-distribution-tasks-badge')).toBeNull()
    await user.click(trigger)
    expect(screen.getByText('No distribution events currently need attention.')).toBeInTheDocument()
    expect(apiMocks.reconcilePortfolioInstrumentEventTasks).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Refresh Distribution Events' }))

    expect(apiMocks.reconcilePortfolioInstrumentEventTasks).toHaveBeenCalledWith(
      'portfolio-1',
      true,
    )
    expect(await screen.findByText('Example Private Fund')).toBeInTheDocument()
  })


  it('keeps the loading state in the compact entry until the review is opened', async () => {
    apiMocks.getPortfolioInstrumentEventTasks.mockReturnValue(new Promise(() => {}))
    const user = userEvent.setup()
    render(<FundDistributionTasksPanel portfolioId="portfolio-1" accountNames={{}} refreshKey={0} onRecord={vi.fn()} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Fund distribution reviews: Loading' }))
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
    expect(screen.queryByText('No distribution events currently need attention.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh Distribution Events' })).toBeDisabled()
  })

  it('marks failed loading with an error badge and lets readers retry the read', async () => {
    accessState.can_edit = false
    apiMocks.getPortfolioInstrumentEventTasks.mockRejectedValueOnce(new Error('Distribution service unavailable.'))
    const user = userEvent.setup()
    render(<FundDistributionTasksPanel portfolioId="portfolio-1" accountNames={{}} refreshKey={0} onRecord={vi.fn()} />)
    const trigger = await screen.findByRole('button', { name: /Fund distribution reviews: Loading or review failed/ })
    expect(trigger).toHaveClass('has-error')
    expect(trigger.querySelector('.fund-distribution-tasks-badge')).toHaveTextContent('!')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.click(trigger)
    expect(screen.getByRole('alert')).toHaveTextContent('Distribution service unavailable.')
    expect(screen.queryByText('No distribution events currently need attention.')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Example Private Fund')).toBeInTheDocument()
    expect(apiMocks.getPortfolioInstrumentEventTasks).toHaveBeenCalledTimes(2)
    expect(apiMocks.reconcilePortfolioInstrumentEventTasks).not.toHaveBeenCalled()
  })

  it('closes with Escape or the backdrop and returns focus to the title entry', async () => {
    const user = userEvent.setup()
    render(<FundDistributionTasksPanel portfolioId="portfolio-1" accountNames={{}} refreshKey={0} onRecord={vi.fn()} />)
    const trigger = await screen.findByRole('button', { name: /Fund distribution reviews: 1 need attention/ })
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Fund distribution reviews' })
    expect(dialog).toHaveClass('portfolio-settings-modal')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
    await user.click(trigger)
    await user.click(screen.getByRole('dialog').parentElement!)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('retains operator identity and version when marking a task not applicable', async () => {
    const user = userEvent.setup()
    render(<FundDistributionTasksPanel portfolioId="portfolio-1" accountNames={{}} refreshKey={0} onRecord={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /Fund distribution reviews: 1 need attention/ }))
    await user.click(screen.getByRole('button', { name: 'Mark Not Applicable' }))
    expect(screen.getByRole('button', { name: 'Confirm Not Applicable' })).toBeDisabled()
    await user.type(screen.getByPlaceholderText('Required reason'), 'No distribution entitlement')
    await user.click(screen.getByRole('button', { name: 'Confirm Not Applicable' }))
    expect(apiMocks.reviewPortfolioInstrumentEventTask).toHaveBeenCalledWith('portfolio-1', 'task-1', {
      decision: 'not_applicable', transaction_ids: [], note: 'No distribution entitlement', reviewed_by: 'alice.ops', expected_row_version: 1,
    })
  })

  it.each([['Reconfirm Linked Facts', 'processed', ['txn-1']], ['Detach and Reopen', 'reopened', []]] as const)('preserves the linked review action %s', async (buttonLabel, decision, transactionIds) => {
    apiMocks.getPortfolioInstrumentEventTasks.mockResolvedValue({ tasks: [{
      ...pendingTask, status: 'needs_review', row_version: 4,
      linked_transactions: [{ transaction_id: 'txn-1' }],
    }] })
    const user = userEvent.setup()
    render(<FundDistributionTasksPanel portfolioId="portfolio-1" accountNames={{}} refreshKey={0} onRecord={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /Fund distribution reviews: 1 need attention/ }))
    expect(screen.getByRole('button', { name: 'Mark Not Applicable' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: buttonLabel }))
    expect(apiMocks.reviewPortfolioInstrumentEventTask).toHaveBeenCalledWith('portfolio-1', 'task-1', expect.objectContaining({
      decision, transaction_ids: transactionIds, reviewed_by: 'alice.ops', expected_row_version: 4,
    }))
  })
})
