import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import LedgerHoldings from './LedgerHoldings'

const api = vi.hoisted(() => ({ getPortfolioPositions: vi.fn(), getPortfolioAccountsWorkspace: vi.fn() }))
vi.mock('../lib/api', () => api)
const positions = { positions: [{ position_id: 'aapl', instrument_ref: { instrument_name: 'Apple' }, quantity: 60, cost_basis: 603, currency: 'USD', account_ids: ['broker'] }] }
const accounts = { accounts: [{ account: { account_id: 'broker', account_name: 'Primary broker', account_type: 'securities_account', currency: 'USD' }, derived_cash_balance: 14224, pending_settlement: -200 }] }
const page = (date = '2026-08-31') => <LanguageProvider enableDomTranslation={false}><MemoryRouter><LedgerHoldings portfolioId="p1" asOfDate={date} /></MemoryRouter></LanguageProvider>

describe('Unvalued ledger holdings', () => {
  beforeEach(() => { vi.resetAllMocks(); api.getPortfolioPositions.mockResolvedValue(positions); api.getPortfolioAccountsWorkspace.mockResolvedValue(accounts) })
  it('preserves requested date, quantity, local cost and settled/pending cash without presenting NAV', async () => {
    render(page())
    expect(await screen.findByText('Apple')).toBeInTheDocument()
    expect(api.getPortfolioPositions).toHaveBeenCalledWith('p1', '2026-08-31', expect.any(AbortSignal), false)
    expect(api.getPortfolioAccountsWorkspace).toHaveBeenCalledWith('p1', undefined, expect.any(AbortSignal), '2026-08-31', false)
    const position = screen.getByText('Apple').closest('tr')!
    expect(within(position).getByText('60.000000')).toBeInTheDocument()
    expect(within(position).getByText('$603.00')).toBeInTheDocument()
    expect(within(position).getByText('Primary broker')).toBeInTheDocument()
    expect(screen.getByText('$14,224.00')).toBeInTheDocument()
    expect(screen.getByText('-$200.00')).toBeInTheDocument()
    expect(screen.getByText(/This table is not current NAV/)).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /NAV|Market value/ })).not.toBeInTheDocument()
  })
  it('keeps available cash visible when the position ledger fails without claiming an empty position book', async () => {
    api.getPortfolioPositions.mockRejectedValue(new Error('Position ledger unavailable'))
    render(page())
    expect(await screen.findByText('Position ledger unavailable')).toBeInTheDocument()
    expect(screen.getByText('$14,224.00')).toBeInTheDocument()
    expect(screen.queryByText(/No open security/)).not.toBeInTheDocument()
  })
  it('keeps security, contract and account names unchanged when translating ledger headings', async () => {
    api.getPortfolioPositions.mockResolvedValue({ positions: [
      { ...positions.positions[0], instrument_ref: { instrument_name: 'Return' } },
      { ...positions.positions[0], position_id: 'option', instrument_ref: null, derivative_contract: { contract_name: 'Interest' } },
    ] })
    api.getPortfolioAccountsWorkspace.mockResolvedValue({ accounts: [{ ...accounts.accounts[0], account: { ...accounts.accounts[0].account, account_name: 'Income' } }] })
    render(<LanguageProvider><LanguageSelector /><MemoryRouter><LedgerHoldings portfolioId="p1" asOfDate="2026-08-31" /></MemoryRouter></LanguageProvider>)
    await screen.findByText('Return')
    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
    await waitFor(() => expect(screen.getByRole('columnheader', { name: '现金账户' })).toBeInTheDocument())
    expect(screen.getByText('Return')).toBeInTheDocument()
    expect(screen.getByText('Interest')).toBeInTheDocument()
    expect(screen.getAllByText('Income')).toHaveLength(3)
    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => expect(screen.getByRole('columnheader', { name: 'Cash account' })).toBeInTheDocument())
    expect(screen.getByText('Return')).toBeInTheDocument()
  })
  it('clears the previous date immediately and ignores its late response', async () => {
    let finishOld!: (value: unknown) => void
    api.getPortfolioPositions.mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve }))
    const view = render(page())
    view.rerender(page('2026-09-01'))
    expect(await screen.findByText('Apple')).toBeInTheDocument()
    await act(async () => { finishOld({ positions: [{ ...positions.positions[0], instrument_ref: { instrument_name: 'Old date security' } }] }) })
    expect(screen.queryByText('Old date security')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /2026-09-01/ })).toBeInTheDocument()
  })
})
