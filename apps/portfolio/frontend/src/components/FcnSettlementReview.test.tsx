import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider, LANGUAGE_STORAGE_KEY } from '../../../../../packages/ui/src/i18n'
import { materializePortfolioSecurity, searchPortfolioSecurities } from '../lib/api'
import { TransactionDeliveryReview } from './TransactionDeliveryReview'
import type { PortfolioAccountRecord, PortfolioAssetDelivery, PortfolioFcnContractTerms } from '../lib/api'
import { instrumentFixture } from '../test/portfolioFixtures'
import { FcnSettlementReview } from './FcnSettlementReview'

vi.mock('../lib/api', async original => ({ ...(await original<typeof import('../lib/api')>()),
  searchPortfolioSecurities: vi.fn(), materializePortfolioSecurity: vi.fn(),
}))

const accounts = [
  { account_id: 'stock-hkd', account_name: 'HK securities', account_category: 'security', currency: 'HKD' },
  { account_id: 'cash-usd', account_name: 'USD cash', account_category: 'cash', currency: 'USD' },
  { account_id: 'cash-hkd', account_name: 'HKD cash', account_category: 'cash', currency: 'HKD' },
] as PortfolioAccountRecord[]
const stock = { ...instrumentFixture({ instrument_id: 'smic', instrument_name: 'SMIC', instrument_type: 'equity', currency: 'HKD' }), latest_market_data: [], coverage_state: 'complete' as const, quote_selection_policy: { trading: [], valuation: [], total_return: [], chart: [], reference: [] } }
const terms = { notional: 500000, underlyings: [{ instrument_id: 'smic', initial_reference_price: 1000, strike_level_pct: 80, deliverable: true }] } as PortfolioFcnContractTerms
const delivery: PortfolioAssetDelivery = {
  account_id: 'stock-hkd', instrument_id: 'smic', quantity: '4875', fair_value: '2925000', currency: 'HKD', fx_rate_to_contract: '0.1282',
  quantity_fx_rate: '7.8', fractional_quantity: '0.25', fractional_reference_price: '600',
  delivery_date: '2026-09-03', settlement_cash_account_id: 'cash-hkd', taxes: '3000', fee_settlement_date: '2026-09-04',
}

afterEach(() => { window.localStorage.removeItem(LANGUAGE_STORAGE_KEY) })

it('shows contractual strike separately from stock value and restricts acquisition charges to the stock currency', () => {
  const onChange = vi.fn()
  render(<LanguageProvider enableDomTranslation={false}><FcnSettlementReview
    record={{ asset_deliveries: [delivery], settlement_cashflows: [{ kind: 'coupon', cash_account_id: 'cash-usd', currency: 'USD', amount: '3333.33' }] }}
    accounts={accounts} instruments={[stock]} currency="USD" terms={terms} economicDate="2026-09-01"
    settlementDate="2026-09-02" residualCash="19.23" residualAccountId="cash-usd" onChange={onChange}
  /></LanguageProvider>)
  expect(screen.getByText(/Contract strike/)).toHaveTextContent('800')
  expect(screen.getByText(/Confirmed value per share/)).toHaveTextContent('600')
  expect(screen.getByText(/original principal is not debited again/)).toBeVisible()
  const chargeAccount = screen.getByLabelText('Stock-charge cash account')
  expect(within(chargeAccount).getByRole('option', { name: 'HKD cash · HKD' })).toBeInTheDocument()
  expect(within(chargeAccount).queryByRole('option', { name: 'USD cash · USD' })).not.toBeInTheDocument()
  const couponAccount = screen.getByLabelText('Cashflow 1 account')
  expect(within(couponAccount).queryByRole('option', { name: 'HKD cash · HKD' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Stock acquisition taxes (HKD)'), { target: { value: '3200' } })
  expect(onChange).toHaveBeenLastCalledWith({ asset_deliveries: [{ ...delivery, taxes: '3200' }] })
  fireEvent.change(screen.getByLabelText('Quantity conversion FX (HKD/USD)'), { target: { value: '7.81' } })
  expect(onChange).toHaveBeenLastCalledWith({ asset_deliveries: [{ ...delivery, quantity_fx_rate: '7.81' }] })
})

it('renders the FCN settlement explanation in Chinese with named securities and actual receipt dates', () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  render(<LanguageProvider enableDomTranslation={false}><FcnSettlementReview record={{ asset_deliveries: [delivery] }}
    accounts={accounts} instruments={[stock]} currency="USD" terms={terms} economicDate="2026-09-01" settlementDate="2026-09-02"
  /></LanguageProvider>)
  expect(screen.getByText('FCN 结算单')).toBeVisible()
  expect(screen.getByText(/接票时不会再次扣除原本金/)).toBeVisible()
  expect(screen.getByText(/HK securities.*SMIC/)).toBeVisible()
  expect(screen.getByLabelText('证券实际到账日')).toHaveValue('2026-09-03')
  expect(screen.getByLabelText('股票费用扣款日')).toHaveValue('2026-09-04')
  expect(screen.getByText(/价值汇率只将确认价值折算到合约币种，不产生现金换汇/)).toBeVisible()
  expect(screen.queryByText('Settlement cashflows')).not.toBeInTheDocument()
})


it.each(['manual', 'capture'])('registers an unregistered FCN delivery security in the %s review', async mode => {
  const onChange = vi.fn()
  const onInstrumentRegistered = vi.fn()
  const candidate = { instrument_type: 'equity' as const, symbol: '0700.HK', name: 'Tencent',
    catalog_provider: 'fmp' as const, catalog_symbol: '0700.HK', exchange_code: 'XHKG',
    exchange_label: 'Hong Kong Exchange', market: 'HK', currency: 'HKD', currency_verified: true, existing_instrument_id: null }
  const registered = { ...stock, instrument_id: 'tencent', instrument_name: 'Tencent' }
  vi.mocked(searchPortfolioSecurities).mockResolvedValue({ results: [candidate], catalog_errors: {} })
  vi.mocked(materializePortfolioSecurity).mockResolvedValue(registered)
  const props = { record: { asset_deliveries: [{ ...delivery, instrument_id: '' }] }, accounts, instruments: [stock],
    portfolioId: 'portfolio-1', onChange, onInstrumentRegistered }
  render(<LanguageProvider enableDomTranslation={false}>{mode === 'manual'
    ? <FcnSettlementReview {...props} />
    : <TransactionDeliveryReview {...props} canAddAssetDelivery />}</LanguageProvider>)
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '0700' } })
  fireEvent.click(await screen.findByRole('button', { name: /0700.HK.*Hong Kong Exchange/ }))
  await waitFor(() => expect(onChange).toHaveBeenCalledWith({ asset_deliveries: [{ ...delivery, instrument_id: 'tencent' }] }))
  expect(onInstrumentRegistered).toHaveBeenCalledWith(registered)
})
