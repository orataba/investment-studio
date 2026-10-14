export const DEFAULT_TRADE_TIMEZONE = import.meta.env.VITE_PORTFOLIO_DEFAULT_TRADE_TIMEZONE || 'Asia/Shanghai'

/** Calendar dates entered in the transaction timezone must not follow the browser timezone. */
export function transactionTodayIso(now = new Date(), timezone = DEFAULT_TRADE_TIMEZONE): string {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now)
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)!.value
  return `${value('year')}-${value('month')}-${value('day')}`
}
