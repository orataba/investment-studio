import type { PortfolioEntryRecord } from './api'
import { exactDecimalSum, exactDecimalToDisplayNumber } from './exactDecimal'

export type PortfolioCurrencyTotal = {
  baseCurrency: string
  portfolioCount: number
  nav: number | null
  dayChangeValue: number | null
}

/**
 * Roll up money only inside a single declared base currency. Null source facts
 * make the corresponding aggregate unavailable instead of silently becoming 0.
 */
export function groupPortfolioTotalsByBaseCurrency(
  portfolios: readonly PortfolioEntryRecord[],
): PortfolioCurrencyTotal[] {
  const groups = new Map<
    string,
    {
      baseCurrency: string
      portfolioCount: number
      nav: string[]
      navComplete: boolean
      dayChangeValue: string[]
      dayChangeComplete: boolean
    }
  >()

  portfolios.forEach((portfolio) => {
    const baseCurrency = String(portfolio.base_currency || '').trim().toUpperCase()
    const group = groups.get(baseCurrency) ?? {
      baseCurrency,
      portfolioCount: 0,
      nav: [],
      navComplete: true,
      dayChangeValue: [],
      dayChangeComplete: true,
    }
    group.portfolioCount += 1
    if (portfolio.nav_exact != null) {
      group.nav.push(portfolio.nav_exact)
    } else {
      group.navComplete = false
    }
    if (portfolio.day_change_value_exact != null) {
      group.dayChangeValue.push(portfolio.day_change_value_exact)
    } else {
      group.dayChangeComplete = false
    }
    groups.set(baseCurrency, group)
  })

  return [...groups.values()]
    .map((group) => ({
      baseCurrency: group.baseCurrency,
      portfolioCount: group.portfolioCount,
      nav: group.navComplete ? exactDecimalToDisplayNumber(exactDecimalSum(group.nav)) : null,
      dayChangeValue: group.dayChangeComplete
        ? exactDecimalToDisplayNumber(exactDecimalSum(group.dayChangeValue))
        : null,
    }))
    .sort((left, right) => left.baseCurrency.localeCompare(right.baseCurrency))
}
