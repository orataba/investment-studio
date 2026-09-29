import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { TransactionDeliveryReview } from './components/TransactionDeliveryReview'

it('keeps each physical delivery fee editable and lets the server sum exact amounts', () => {
  const onChange = vi.fn()
  render(<TransactionDeliveryReview accounts={[]} onChange={onChange} record={{ option_delivery: {
    stock_account_id: 'security', settlement_cash_account_id: 'cash', fees: 3,
    fee_components: [{ category: 'performance_fee', amount: '1.00000001' }, { category: 'transaction_cost', amount: '2' }],
  } }} />)
  expect(screen.getByLabelText('交付费用 1 分类')).toHaveValue('performance_fee')
  expect(screen.getByLabelText('交付费用 2 分类')).toHaveValue('transaction_cost')
  expect(screen.queryByLabelText('交付费用')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('交付费用 2 金额'), { target: { value: '2.12345678' } })
  expect(onChange).toHaveBeenCalledWith({ option_delivery: {
    stock_account_id: 'security', settlement_cash_account_id: 'cash', fees: undefined, fee_category: 'unknown',
    fee_components: [{ category: 'performance_fee', amount: '1.00000001' }, { category: 'transaction_cost', amount: '2.12345678' }],
  } })
})
