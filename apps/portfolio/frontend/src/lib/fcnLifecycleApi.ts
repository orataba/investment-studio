export type FcnLifecycleDelivery = {
  transaction_id: string
  instrument_id: string
  instrument_name: string
  account_id: string
  account_name: string
  currency: string
  quantity: number
  fair_value: number
  capitalized_charges: number
  effective_date: string
  delivery_date: string | null
  status: 'pending' | 'delivered' | 'unknown'
}

export type FcnLifecycleStock = {
  instrument_id: string
  instrument_name: string
  account_id: string
  account_name: string
  currency: string
  remaining_quantity: number | null
  realized_quantity: number | null
  remaining_cost_basis: number
  current_market_value: number | null
  realized_pnl: number | null
  unrealized_pnl: number | null
}

export type FcnLifecycle = {
  derivative_contract_id: string
  contract_name: string
  currency: string
  contract_status: 'open' | 'closed'
  contract_disposal_pnl: number
  coupon_income: number | null
  contract_charges: number | null
  contract_pnl: number | null
  stock_realized_pnl: number | null
  stock_unrealized_pnl: number | null
  stock_income: number | null
  stock_pnl: number | null
  total_pnl: number | null
  deliveries: FcnLifecycleDelivery[]
  stocks: FcnLifecycleStock[]
  warnings: string[]
}

export type FcnLifecyclesResponse = {
  portfolio_id: string
  position_reference_id: string
  as_of_date: string
  lifecycles: FcnLifecycle[]
}
