import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import HoldingPeriodPanel from './HoldingPeriodPanel'
import HoldingEventsPanel from './HoldingEventsPanel'
import OptionPayoffChart from './OptionPayoffChart'
import InstrumentPriceChart from './InstrumentPriceChart'
import DerivativeHoldingOverview from './DerivativeHoldingOverview'
import { holdingFixture, instrumentFixture, optionContractFixture, fcnContractFixture } from '../test/portfolioFixtures'
import { renderPortfolioPage } from '../test/renderPortfolioPage'
import type { PortfolioInstrumentPriceChartResponse, PortfolioTransactionRecord } from '../lib/api'
import { LANGUAGE_STORAGE_KEY } from '../../../../../packages/ui/src/i18n'

const api = vi.hoisted(() => ({ getPortfolioPerformanceCalculationGroups: vi.fn(), getPortfolioInstrumentEventTasks: vi.fn(), getPortfolioInstrumentPriceChart: vi.fn() }))
vi.mock('../lib/api', () => api)

function render(element: React.ReactElement) { return renderPortfolioPage(element, '/portfolios/3/holdings/asset-1', '/portfolios/:portfolioId/holdings/:holdingId') }
const chart: PortfolioInstrumentPriceChartResponse = {
  portfolio_id: '3', instrument_core: instrumentFixture(), as_of_date: '2026-07-15', range_key: '1y', chart_basis: 'adjusted_close', metric_family: 'price', currency: 'USD', series_role: 'performance',
  points: [{ date: '2026-07-01', value: 110 }, { date: '2026-07-02', value: 100 }, { date: '2026-07-15', value: 110 }],
  summary: { point_count: 3, high: 110, low: 100, change_pct: 0, change_value: 0 },
}
beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
  api.getPortfolioInstrumentEventTasks.mockResolvedValue({ tasks: [] })
  api.getPortfolioInstrumentPriceChart.mockResolvedValue(chart)
})

describe('Holding detail investment semantics', () => {
  it('uses the canonical instrument calculation, separates FX and expenses, and accepts a custom close-to-close period', async () => {
    const user = userEvent.setup()
    api.getPortfolioPerformanceCalculationGroups.mockResolvedValue({
      portfolio_id: '3', base_currency: 'HKD',
      summary: { start_date: '2026-06-30', end_date: '2026-07-15', include_start_date_return: false },
      groups: [
        { group_key: 'linked-option', total_pnl: 9999 },
        { group_key: 'asset-1', total_pnl: 123, realized_capital_gains: 92, unrealized_pnl_change: -20, earnings: 50, instrument_currency_gains: 3, cash_currency_gains: 0, pending_settlement_currency_gains: 0, expense_cash_amount: 2, fees: 5, taxes: 3, initial_value: 1000, final_value: 1123, period_contribution: 0.0123 },
      ],
    })
    render(<HoldingPeriodPanel portfolioId="3" holdingId="asset-1" asOfDate="2026-07-15" eventValued={false} />)
    expect(await screen.findByText('+HK$123.00')).toBeInTheDocument()
    expect(screen.queryByText(/9,999/)).not.toBeInTheDocument()
    expect(screen.getByText('Change in unrealized price P/L').parentElement).toHaveTextContent('-HK$20.00')
    expect(screen.getByText('Separate cash expenses').parentElement).toHaveTextContent('-HK$2.00')
    expect(screen.getByText(/Included fees and taxes/)).toHaveTextContent('-HK$8.00')
    expect(screen.getByText(/Net period P\/L = realized/)).toBeInTheDocument()
    expect(screen.getByText('1.230 percentage points')).toBeInTheDocument()
    expect(api.getPortfolioPerformanceCalculationGroups).toHaveBeenCalledWith('3', { axis: 'instrument', start_date: '2026-06-30', end_date: '2026-07-15' })
    await user.clear(screen.getByLabelText('P/L opening boundary'))
    await user.type(screen.getByLabelText('P/L opening boundary'), '2026-07-02')
    await user.click(screen.getByRole('button', { name: 'Apply period' }))
    await waitFor(() => expect(api.getPortfolioPerformanceCalculationGroups).toHaveBeenLastCalledWith('3', { axis: 'instrument', start_date: '2026-07-02', end_date: '2026-07-15' }))
  })

  it('shows the acceptance ledger charges once and reconciles the additive components', async () => {
    api.getPortfolioPerformanceCalculationGroups.mockResolvedValue({ portfolio_id: '3', base_currency: 'CNY', summary: {}, groups: [
      { group_key: 'asset-1', total_pnl: 99859.60, realized_capital_gains: 70, unrealized_pnl_change: 99789.60, earnings: 0, instrument_currency_gains: 0, cash_currency_gains: 0, pending_settlement_currency_gains: 0, expense_cash_amount: 0, fees: 10, taxes: 0 },
    ] })
    render(<HoldingPeriodPanel portfolioId="3" holdingId="asset-1" asOfDate="2026-10-01" eventValued={false} />)
    expect(await screen.findByText(/Included fees and taxes/)).toHaveTextContent('-CN¥10.00')
    expect(screen.getByText('Realized price P/L').parentElement).toHaveTextContent('+CN¥70.00')
    expect(screen.getByText('Change in unrealized price P/L').parentElement).toHaveTextContent('+CN¥99,789.60')
    expect(screen.getByText('+CN¥99,859.60')).toBeInTheDocument()
  })

  it('keeps missing derivative valuation unavailable instead of reporting a zero market return', async () => {
    api.getPortfolioPerformanceCalculationGroups.mockResolvedValue({ portfolio_id: '3', base_currency: 'USD', summary: {}, groups: [{ group_key: 'asset-1', total_pnl: null, period_contribution: null }] })
    render(<HoldingPeriodPanel portfolioId="3" holdingId="asset-1" asOfDate="2026-07-15" eventValued />)
    expect(await screen.findByText(/Contract-wide accounting result/)).toBeInTheDocument()
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument()
    expect(screen.queryByText('Contribution to portfolio return')).not.toBeInTheDocument()
  })

  it('normalizes decimal-string strikes before computing break-even and drawing the payoff', () => {
    const contract = optionContractFixture({ terms: { underlying_instrument_id: 'asset-1', option_type: 'call', strike: '110' as unknown as number, contract_multiplier: '100' as unknown as number, expiry_date: '2026-12-18' } })
    const holding = holdingFixture({ quantity: 1, cost_basis: 500, option_risk: { underlying_instrument_id: 'asset-1', underlying_name: 'Alpha', underlying_quote_currency: 'USD', underlying_spot: 120, underlying_quote_as_of_date: '2026-07-15', underlying_quote_status: 'complete', intrinsic_value_per_share: 10, moneyness_pct: 10 / 110, max_loss_local: 500, days_to_expiry: 156, risk_state: 'in_the_money', backing: null } })
    render(<OptionPayoffChart contract={contract} holding={holding} />)
    expect(screen.getByText('Break-even').parentElement).toHaveTextContent('$115.0000')
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'Standalone option expiry payoff based on remaining premium basis')
    expect(document.querySelector('.option-payoff-line')?.getAttribute('d')).not.toMatch(/NaN|Infinity/)
  })

  it.each([
    ['call', 'USD', 2, 1000, '$205.0000', '$5.0000', 'en'],
    ['put', 'USD', 2, 1000, '$195.0000', '$5.0000', 'en'],
    ['call', 'HKD', 3, 1700, 'HK$205.6667', 'HK$5.6667', 'en'],
    ['put', 'HKD', 2, 1200, 'HK$194.0000', 'HK$6.0000', 'en'],
    ['call', 'USD', 2, 1000, '$205.0000', '$5.0000', 'zh-Hans'],
  ] as const)('discloses remaining premium-only %s payoff in %s after opening or partial close', (optionType, currency, quantity, basis, breakEven, premium, language) => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
    const contract = optionContractFixture({ currency, terms: { underlying_instrument_id: 'asset-1', option_type: optionType, strike: 200, contract_multiplier: 100, expiry_date: '2026-12-18' } })
    const holding = holdingFixture({ quantity, cost_basis: basis, option_risk: { underlying_instrument_id: 'asset-1', underlying_name: 'Alpha', underlying_quote_currency: currency, underlying_spot: 200, underlying_quote_as_of_date: '2026-07-15', underlying_quote_status: 'complete', intrinsic_value_per_share: 0, moneyness_pct: 0, max_loss_local: basis, days_to_expiry: 156, risk_state: 'at_the_money', backing: null } })
    const { unmount } = render(<OptionPayoffChart contract={contract} holding={holding} />)
    expect(screen.getByText('Break-even').parentElement).toHaveTextContent(breakEven)
    expect(screen.getByText('Premium basis / share').parentElement).toHaveTextContent(premium)
    expect(screen.getByText('Maximum loss / share').parentElement).toHaveTextContent(premium)
    expect(screen.getByText(language === 'en' ? /Long payoff uses remaining premium cost only/ : /买方收益仅按剩余权利金成本/)).toHaveTextContent(language === 'en' ? 'future closing charges are excluded' : '未来平仓费用不包含在内')
    const path = document.querySelector('.option-payoff-line')!.getAttribute('d')
    // A later snapshot with the same remaining basis cannot re-add opening fees.
    unmount()
    render(<OptionPayoffChart contract={contract} holding={{ ...holding, option_risk: { ...holding.option_risk!, underlying_quote_as_of_date: '2026-07-16' } }} />)
    expect(document.querySelector('.option-payoff-line')!.getAttribute('d')).toBe(path)
  })

  it.each(['missing-cost', 'missing-currency', 'different-currency'] as const)('keeps option payoff unavailable with %s', (reason) => {
    const contract = optionContractFixture()
    const holding = holdingFixture({ quantity: 2, cost_basis: reason === 'missing-cost' ? null : 1000, option_risk: { underlying_instrument_id: 'asset-1', underlying_name: 'Alpha', underlying_quote_currency: reason === 'different-currency' ? 'HKD' : reason === 'missing-currency' ? '' : 'USD', underlying_spot: 200, underlying_quote_as_of_date: '2026-07-15', underlying_quote_status: 'complete', intrinsic_value_per_share: null, moneyness_pct: null, max_loss_local: null, days_to_expiry: 156, risk_state: 'unavailable', backing: null } })
    render(<OptionPayoffChart contract={contract} holding={holding} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('Break-even').parentElement).toHaveTextContent('—')
    expect(screen.getByText(reason === 'missing-cost' ? 'Remaining premium basis is unavailable.' : 'Payoff needs a confirmed common currency for premium and strike.')).toBeInTheDocument()
  })

  it('draws decimal-string reference lines and uses calendar spacing for irregular fund observations', () => {
    render(<InstrumentPriceChart chart={chart} loading={false} error={null} rangeKey="1y" onRangeChange={vi.fn()} variant="instrument" referenceLines={[{ label: 'Strike', value: '105' as unknown as number }]} />)
    expect(screen.getByText('Strike $105.0000')).toBeInTheDocument()
    expect(screen.getByText('Observed max drawdown').parentElement).toHaveTextContent('-9.09%')
    const path = document.querySelector('.price-chart-line')!.getAttribute('d')!
    const coordinates = [...path.matchAll(/[ML] ([\d.]+) ([\d.]+)/g)].map((match) => Number(match[1]))
    expect((coordinates[1] - coordinates[0]) / (coordinates[2] - coordinates[0])).toBeCloseTo(1 / 14, 3)
    expect(screen.getByRole('button', { name: '1Y' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('dates booked income by entitlement and distinguishes pending source notices from cash receipts', async () => {
    api.getPortfolioInstrumentEventTasks.mockResolvedValue({ tasks: [{ instrument_event_task_id: 'task-1', instrument_id: 'asset-1', effective_date: '2026-07-05', event_type: 'cash_dividend', expected_gross_amount: 900, account_id: 'account-1', status: 'pending', attention_required: true }] })
    const income = { transaction_id: 'div-1', transaction_type: 'dividend', instrument_id: 'asset-1', entitlement_date: '2026-07-01', economic_date: '2026-07-01', trade_date: '2026-07-10', settlement_date: '2026-07-20', account: { account_name: 'Broker' }, currency: 'USD', net_cash_effect: 100 } as PortfolioTransactionRecord
    render(<HoldingEventsPanel portfolioId="3" holdingId="asset-1" asOfDate="2026-07-15" currency="USD" security transactions={[income]} lots={[]} loading={false} error={null} />)
    expect(await screen.findByText('Expected gross amount')).toBeInTheDocument()
    expect(screen.getByText('2026-07-01')).toBeInTheDocument()
    expect(screen.getByText('View transaction')).toHaveAttribute('href', '/portfolios/3/transactions?transaction_id=div-1')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Current review status: Source notices/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('Source notices and expected entitlements are not booked income.')
  })

  it('does not reserve a distribution review section when there are no notices', async () => {
    render(<HoldingEventsPanel portfolioId="3" holdingId="asset-1" asOfDate="2026-07-15" currency="USD" security transactions={[]} lots={[]} loading={false} error={null} />)
    await waitFor(() => expect(api.getPortfolioInstrumentEventTasks).toHaveBeenCalled())
    expect(screen.queryByRole('heading', { name: 'Distribution review' })).not.toBeInTheDocument()
    expect(screen.getByText('No booked events in this date range.')).toBeInTheDocument()
  })

  it('counts only settled coupon cash and keeps missing FCN terms blank', async () => {
    const contract = fcnContractFixture()
    const coupon = { transaction_type: 'coupon', derivative_contract_id: contract.derivative_contract_id, currency: contract.currency, net_cash_effect: 100, settlement_date: '2026-07-10' } as PortfolioTransactionRecord
    render(<DerivativeHoldingOverview portfolioId="3" asOfDate="2026-07-15" baseCurrency="USD" holding={null} contract={contract} rangeKey="1y" onRangeChange={vi.fn()} transactions={[coupon, { ...coupon, net_cash_effect: 200, settlement_date: '2026-07-20' }]} transactionsLoading={false} transactionsError={null} />)
    expect(await screen.findByRole('heading', { name: 'Contract terms' })).toBeInTheDocument()
    expect(screen.getByText('Coupons received').parentElement).toHaveTextContent('+$100.00')
    expect(screen.getByText('Coupon payments').parentElement).toHaveTextContent('1')
    expect(screen.queryByText('+$300.00')).not.toBeInTheDocument()
  })
})
