import type { PortfolioOptionAction } from './api'

const OPTION_ACTION_LABELS: Readonly<Record<PortfolioOptionAction, string>> = {
  buy_to_open: 'Option Buy to Open',
  sell_to_close: 'Option Sell to Close',
  sell_to_open: 'Covered Call Sell to Open',
  buy_to_close: 'Covered Call Buy to Close',
}

export function resolveOptionAction(
  transactionType: string,
  instrumentType?: string | null,
): PortfolioOptionAction | null {
  if (transactionType === 'option_write') return 'sell_to_open'
  if (transactionType === 'option_buy_to_close') return 'buy_to_close'
  if (instrumentType === 'option' && transactionType === 'buy') return 'buy_to_open'
  if (instrumentType === 'option' && transactionType === 'sell') return 'sell_to_close'
  return null
}

export function optionActionLabel(action: PortfolioOptionAction) {
  return OPTION_ACTION_LABELS[action]
}
