import { screen, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import FcnLifecyclePanel from './FcnLifecyclePanel'
import type { FcnLifecyclesResponse } from '../lib/fcnLifecycleApi'
import { renderPortfolioPage } from '../test/renderPortfolioPage'

const api = vi.hoisted(() => ({ getPortfolioFcnLifecycles: vi.fn() }))
vi.mock('../lib/api', () => api)

function result(status: 'pending' | 'unknown' = 'pending'): FcnLifecyclesResponse {
  return {
    portfolio_id: 'p', position_reference_id: 'hk-1', as_of_date: '2026-04-03',
    lifecycles: [{
      derivative_contract_id: 'fcn-1', contract_name: 'USD / HK shares', currency: 'USD', contract_status: 'closed',
      contract_disposal_pnl: -62500, coupon_income: 10000, contract_charges: 10, contract_pnl: -52510,
      stock_realized_pnl: 300, stock_unrealized_pnl: 700, stock_income: 10, stock_pnl: 1010, total_pnl: -51500,
      deliveries: [{ transaction_id: 'txn-close', instrument_id: 'hk-1', instrument_name: 'HK stock', account_id: 'a', account_name: 'Broker HK',
        currency: 'HKD', quantity: 4881, fair_value: 3416700, capitalized_charges: 3416.7, effective_date: '2026-04-02',
        delivery_date: status === 'pending' ? '2026-04-07' : null, status }],
      stocks: [{ instrument_id: 'hk-1', instrument_name: 'HK stock', account_id: 'a', account_name: 'Broker HK', currency: 'HKD',
        remaining_quantity: 100, realized_quantity: 50, remaining_cost_basis: 70000, current_market_value: 9500, realized_pnl: 300, unrealized_pnl: 700 }],
      warnings: [],
    }],
  }
}

beforeEach(() => { vi.clearAllMocks(); localStorage.setItem('investment_studio.language', 'en') })

it('keeps a closed FCN discoverable from its delivered stock and exposes pending settlement', async () => {
  api.getPortfolioFcnLifecycles.mockResolvedValue(result())
  renderPortfolioPage(<FcnLifecyclePanel portfolioId="p" positionReferenceId="hk-1" asOfDate="2026-04-03" />, '/', '/')
  expect(await screen.findByRole('heading', { name: 'FCN lifecycle results' })).toBeInTheDocument()
  expect(api.getPortfolioFcnLifecycles).toHaveBeenCalledWith('p', 'hk-1', '2026-04-03')
  expect(screen.getByRole('link', { name: 'USD / HK shares' })).toHaveAttribute('href', '/portfolios/p/holdings/fcn-1?as_of_date=2026-04-03')
  expect(screen.getByRole('link', { name: 'Pending delivery' })).toHaveAttribute('href', '/portfolios/p/transactions?transaction_id=txn-close')
  expect(screen.getByText('Whole investment P/L')).toBeInTheDocument()
  expect(screen.getByText('Broker HK')).toBeInTheDocument()
  expect(screen.getByText('Broker HK')).toHaveStyle({ display: 'block' })
  expect(within(screen.getByText('Contract charges').parentElement!).getByText('$10.00')).toBeInTheDocument()
  expect(screen.getByText('Pending shares already carry market risk; they are not yet available for sale.')).toBeInTheDocument()
})

it('does not invent a delivered date for historical records', async () => {
  api.getPortfolioFcnLifecycles.mockResolvedValue(result('unknown'))
  renderPortfolioPage(<FcnLifecyclePanel portfolioId="p" positionReferenceId="hk-1" asOfDate="2026-04-03" />, '/', '/')
  expect(await screen.findByRole('link', { name: 'Delivery date not recorded' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Delivered' })).not.toBeInTheDocument()
  expect(screen.queryByText('2026-04-07')).not.toBeInTheDocument()
})

it('translates unavailable FX and source-attribution explanations on Chinese pages', async () => {
  localStorage.setItem('investment_studio.language', 'zh-Hans')
  const unavailable = result()
  unavailable.lifecycles[0].total_pnl = null
  unavailable.lifecycles[0].warnings = ['Missing HKD/USD FX on 2026-04-03', 'Source share quantities are unavailable for a pooled lot']
  api.getPortfolioFcnLifecycles.mockResolvedValue(unavailable)
  renderPortfolioPage(<FcnLifecyclePanel portfolioId="p" positionReferenceId="hk-1" asOfDate="2026-04-03" />, '/', '/')
  expect(await screen.findByText(/缺少2026-04-03的HKD\/USD汇率/)).toBeInTheDocument()
  expect(screen.getByText(/合并批次缺少可追溯的来源股数/)).toBeInTheDocument()
  expect(screen.queryByText(/Missing HKD\/USD FX/)).not.toBeInTheDocument()
})
