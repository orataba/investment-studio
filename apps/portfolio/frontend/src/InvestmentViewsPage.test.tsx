import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Link, MemoryRouter, Route, Routes } from 'react-router'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import InvestmentViewsPage from './pages/InvestmentViewsPage'
import type { InvestmentView, InvestmentViewsResponse } from './lib/investmentViewsApi'

const mocks = vi.hoisted(() => ({
  getInvestmentViews: vi.fn(), getInvestmentViewInstruments: vi.fn(), createInvestmentView: vi.fn(),
  correctInvestmentView: vi.fn(), getInvestmentViewRevisions: vi.fn(), selectInvestmentStance: vi.fn(),
  access: vi.fn(),
}))
vi.mock('./lib/investmentViewsApi', () => mocks)
vi.mock('./components/PortfolioAccessProvider', () => ({ usePortfolioAccess: mocks.access }))
vi.mock('./components/PortfolioWorkspaceLayout', () => ({ default: ({ children }: { children: React.ReactNode }) => children }))

function note(changes: Partial<InvestmentView> = {}): InvestmentView {
  return { portfolio_id: 'p1', note_id: 'note-1', revision_number: 1, view_date: '2026-10-08', title: 'Liquidity',
    body: 'Hold cash pending better compensation.', context: 'Risk analysis as of 2026-10-07.', source: '', instrument_ids: [], instruments: [],
    author_user_id: 'pm', author_name: 'Portfolio Manager', created_at: '2026-10-08T01:00:00Z',
    revised_by_user_id: 'pm', revised_by_name: 'Portfolio Manager', updated_at: '2026-10-08T01:00:00Z', can_edit: true, ...changes }
}
function timeline(notes: InvestmentView[] = [], changes: Partial<InvestmentViewsResponse> = {}): InvestmentViewsResponse {
  return { portfolio_id: 'p1', notes, current_stance: null, stance_version: 0, ...changes }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
function show(path = '/portfolios/p1/investment-views') {
  return render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={[path]}>
    <Link to="/portfolios/p2/investment-views">Other portfolio</Link>
    <Link to="/portfolios/p1/investment-views?instrument_id=asset-b">Other asset</Link>
    <Routes><Route path="/portfolios/:portfolioId/investment-views" element={<InvestmentViewsPage />} /></Routes>
  </MemoryRouter></LanguageProvider>)
}

describe('Portfolio investment judgments', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mocks.access.mockReturnValue({ portfolio_id: 'p1', can_edit: true })
    mocks.getInvestmentViews.mockResolvedValue(timeline())
    mocks.getInvestmentViewInstruments.mockImplementation((id: string) => Promise.resolve({ portfolio_id: id, instruments: [
      { instrument_id: 'asset-a', instrument_name: 'Asset Alpha', instrument_type: 'etf' },
      { instrument_id: 'asset-b', instrument_name: 'Asset Beta', instrument_type: 'public_fund' },
    ] }))
  })

  it('uses the explicitly selected revision even after a newer note or correction', async () => {
    const selected = note()
    mocks.getInvestmentViews.mockResolvedValue(timeline([note({ revision_number: 2, body: 'Corrected wording.' }), note({ note_id: 'newer', body: 'A different view.' })], {
      current_stance: { note: selected, selected_at: '2026-10-08T01:01:00Z', selected_by_name: 'PM', selected_by_user_id: 'pm' }, stance_version: 1,
    }))
    show()
    const panel = await screen.findByRole('region', { name: 'Current overall view' })
    expect(await within(panel).findByText(selected.body)).toBeInTheDocument()
    expect(within(panel).queryByText('Corrected wording.')).not.toBeInTheDocument()
    expect(within(panel).getByText('v1')).toBeInTheDocument()
    expect(mocks.selectInvestmentStance).not.toHaveBeenCalled()
  })

  it('viewer can read and inspect revisions but cannot write', async () => {
    mocks.access.mockReturnValue({ portfolio_id: 'p1', can_edit: false })
    mocks.getInvestmentViews.mockResolvedValue(timeline([note({ can_edit: false })]))
    show()
    expect(await screen.findByText(note().body)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New judgment' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Correct' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Select as current/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Versions (1)' })).toBeInTheDocument()
  })

  it('records multiple shared assets without sending a client-supplied author', async () => {
    const user = userEvent.setup()
    const saving = deferred<InvestmentView>()
    mocks.createInvestmentView.mockReturnValue(saving.promise)
    show()
    await screen.findByText('No judgments recorded yet.')
    await user.click(screen.getByRole('button', { name: 'New judgment' }))
    await user.type(screen.getByLabelText('Judgment'), 'Why this portfolio holds both assets.')
    await user.type(screen.getByLabelText('Context at the time'), 'Reviewed risks and liquidity.')
    await user.type(screen.getByLabelText('Search registered assets'), 'Asset')
    await user.click(screen.getByRole('checkbox', { name: /Asset Alpha/ }))
    await user.click(screen.getByRole('checkbox', { name: /Asset Beta/ }))
    await user.click(screen.getByRole('button', { name: 'Save judgment' }))
    expect(mocks.createInvestmentView).toHaveBeenCalledTimes(1)
    expect(mocks.createInvestmentView).toHaveBeenCalledWith('p1', expect.objectContaining({ instrument_ids: ['asset-a', 'asset-b'], body: 'Why this portfolio holds both assets.' }))
    expect(mocks.createInvestmentView.mock.calls[0][1]).not.toHaveProperty('author_user_id')
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    await act(async () => saving.resolve(note()))
    expect(await screen.findByText('Judgment saved.')).toBeInTheDocument()
  })

  it('retains the draft on revision conflict without silently adopting a newer revision', async () => {
    const user = userEvent.setup()
    mocks.getInvestmentViews.mockResolvedValue(timeline([note()]))
    mocks.correctInvestmentView.mockRejectedValue(new Error('409: another member saved v2'))
    show()
    await user.click(await screen.findByRole('button', { name: 'Correct' }))
    const body = screen.getByLabelText('Judgment')
    await user.clear(body)
    await user.type(body, 'My unsaved correction.')
    await user.click(screen.getByRole('button', { name: 'Save judgment' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('409: another member saved v2')
    expect(body).toHaveValue('My unsaved correction.')
    mocks.getInvestmentViews.mockResolvedValue(timeline([note({ revision_number: 2, body: 'Other correction.' })]))
    await user.click(screen.getByRole('button', { name: 'Reload timeline; keep draft' }))
    expect(await screen.findByText('Other correction.')).toBeInTheDocument()
    expect(body).toHaveValue('My unsaved correction.')
    expect(screen.getByRole('heading', { name: 'Correct judgment · based on v1' })).toBeInTheDocument()
    expect(mocks.correctInvestmentView).toHaveBeenCalledWith('p1', 'note-1', expect.objectContaining({ expected_revision: 1 }))
  })

  it('blocks correction resubmission during a reload and retains the original draft revision', async () => {
    const user = userEvent.setup()
    mocks.getInvestmentViews.mockResolvedValue(timeline([note()]))
    mocks.correctInvestmentView.mockRejectedValue(new Error('409: another member saved v2'))
    const view = show()
    await user.click(await screen.findByRole('button', { name: 'Correct' }))
    await user.click(screen.getByRole('button', { name: 'Save judgment' }))
    const reloading = deferred<InvestmentViewsResponse>()
    mocks.getInvestmentViews.mockReturnValue(reloading.promise)
    await user.click(await screen.findByRole('button', { name: 'Reload timeline; keep draft' }))
    expect(view.container.querySelector('.investment-views-page')).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('button', { name: 'Save judgment' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save judgment' }))
    expect(mocks.correctInvestmentView).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Judgment')).toHaveValue(note().body)
    await act(async () => reloading.resolve(timeline([note({ revision_number: 2, body: 'Newer saved revision.' })])))
    expect(await screen.findByText('Newer saved revision.')).toBeInTheDocument()
    expect(view.container.querySelector('.investment-views-page')).toHaveAttribute('aria-busy', 'false')
    expect(screen.getByLabelText('Judgment')).toHaveValue(note().body)
    expect(screen.getByRole('heading', { name: 'Correct judgment · based on v1' })).toBeInTheDocument()
  })

  it('does not claim that no overall view is selected when its read failed', async () => {
    mocks.getInvestmentViews.mockRejectedValue(new Error('Connection lost'))
    show()
    await screen.findByRole('alert')
    expect(screen.getByText('The current overall view is unavailable until the timeline is loaded.')).toBeInTheDocument()
    expect(screen.queryByText('No overall view selected. Choose a specific version of a portfolio-wide judgment.')).not.toBeInTheDocument()
  })

  it('filters the same timeline by asset and starts a new view with that asset', async () => {
    const user = userEvent.setup()
    show('/portfolios/p1/investment-views?instrument_id=asset-a')
    await screen.findByText('No judgment for this asset in this portfolio yet.')
    expect(mocks.getInvestmentViews).toHaveBeenCalledWith('p1', 'asset-a', expect.any(AbortSignal))
    expect(await screen.findByRole('option', { name: 'Asset Alpha · asset-a' })).toHaveValue('asset-a')
    await user.click(screen.getByRole('button', { name: 'New judgment' }))
    expect(screen.getByRole('button', { name: /Asset Alpha.*Remove asset/ })).toBeInTheDocument()
  })

  it('ignores a late portfolio read after navigation', async () => {
    const user = userEvent.setup()
    const old = deferred<InvestmentViewsResponse>()
    mocks.getInvestmentViews.mockImplementation((id: string) => id === 'p1' ? old.promise : Promise.resolve(timeline([note({ portfolio_id: 'p2', body: 'Second portfolio only.' })], { portfolio_id: 'p2' })))
    show()
    await user.click(screen.getByRole('link', { name: 'Other portfolio' }))
    expect(await screen.findByText('Second portfolio only.')).toBeInTheDocument()
    await act(async () => old.resolve(timeline([note()])))
    expect(screen.queryByText(note().body)).not.toBeInTheDocument()
    expect(screen.getByText('Second portfolio only.')).toBeInTheDocument()
  })

  it('ignores a late save response after leaving the portfolio', async () => {
    const user = userEvent.setup()
    const pending = deferred<InvestmentView>()
    mocks.getInvestmentViews.mockImplementation((id: string) => Promise.resolve(timeline([], { portfolio_id: id })))
    mocks.createInvestmentView.mockReturnValue(pending.promise)
    show()
    await screen.findByText('No judgments recorded yet.')
    await user.click(screen.getByRole('button', { name: 'New judgment' }))
    await user.type(screen.getByLabelText('Judgment'), 'Private pending judgment')
    await user.type(screen.getByLabelText('Context at the time'), 'Private context')
    await user.click(screen.getByRole('button', { name: 'Save judgment' }))
    await user.click(screen.getByRole('link', { name: 'Other portfolio' }))
    await screen.findByText('No judgments recorded yet.')
    await act(async () => pending.resolve(note({ body: 'Private pending judgment' })))
    expect(screen.queryByText('Judgment saved.')).not.toBeInTheDocument()
    expect(screen.queryByText('Private pending judgment')).not.toBeInTheDocument()
    expect(mocks.getInvestmentViews.mock.calls.filter(([id]) => id === 'p1')).toHaveLength(1)
  })

  it('ignores late history and asset-filter responses after scope changes', async () => {
    const user = userEvent.setup()
    const previousHistory = deferred<{ portfolio_id: string; note_id: string; revisions: InvestmentView[] }>()
    mocks.getInvestmentViews.mockImplementation((_id: string, asset?: string) => Promise.resolve(timeline(asset ? [] : [note()])))
    mocks.getInvestmentViewRevisions.mockReturnValue(previousHistory.promise)
    show()
    await user.click(await screen.findByRole('button', { name: 'Versions (1)' }))
    await user.click(screen.getByRole('link', { name: 'Other asset' }))
    await screen.findByText('No judgment for this asset in this portfolio yet.')
    await act(async () => previousHistory.resolve({ portfolio_id: 'p1', note_id: 'note-1', revisions: [note()] }))
    expect(screen.queryByRole('region', { name: 'Revision history' })).not.toBeInTheDocument()
    expect(screen.queryByText(note().body)).not.toBeInTheDocument()
  })

  it('selects an explicit older revision with the current stance version', async () => {
    const user = userEvent.setup()
    mocks.getInvestmentViews.mockResolvedValue(timeline([note({ revision_number: 2 })], { stance_version: 3 }))
    mocks.getInvestmentViewRevisions.mockResolvedValue({ portfolio_id: 'p1', note_id: 'note-1', revisions: [note({ revision_number: 2 }), note()] })
    mocks.selectInvestmentStance.mockResolvedValue({ current_stance: { note: note(), selected_at: '2026-10-08T01:00:00Z', selected_by_user_id: 'pm', selected_by_name: 'PM' }, stance_version: 4 })
    show()
    await user.click(await screen.findByRole('button', { name: 'Versions (2)' }))
    await user.click(await screen.findByRole('button', { name: 'Select as current overall view · v1' }))
    await waitFor(() => expect(mocks.selectInvestmentStance).toHaveBeenCalledWith('p1', { note_id: 'note-1', revision_number: 1, expected_version: 3 }))
    expect(await screen.findByText('Current overall view selected.')).toBeInTheDocument()
  })
})
