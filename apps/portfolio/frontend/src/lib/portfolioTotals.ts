import type { PortfolioEntryRecord } from './api'

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
      nav: number
      navComplete: boolean
      dayChangeValue: number
      dayChangeComplete: boolean
    }
  >()

  portfolios.forEach((portfolio) => {
    const baseCurrency = String(portfolio.base_currency || '').trim().toUpperCase()
    const group = groups.get(baseCurrency) ?? {
      baseCurrency,
      portfolioCount: 0,
      nav: 0,
      navComplete: true,
      dayChangeValue: 0,
      dayChangeComplete: true,
    }
    group.portfolioCount += 1
    if (typeof portfolio.nav === 'number' && Number.isFinite(portfolio.nav)) {
      group.nav += portfolio.nav
    } else {
      group.navComplete = false
    }
    if (typeof portfolio.day_change_value === 'number' && Number.isFinite(portfolio.day_change_value)) {
      group.dayChangeValue += portfolio.day_change_value
    } else {
      group.dayChangeComplete = false
    }
    groups.set(baseCurrency, group)
  })

  return [...groups.values()]
    .map((group) => ({
      baseCurrency: group.baseCurrency,
      portfolioCount: group.portfolioCount,
      nav: group.navComplete ? group.nav : null,
      dayChangeValue: group.dayChangeComplete ? group.dayChangeValue : null,
    }))
    .sort((left, right) => left.baseCurrency.localeCompare(right.baseCurrency))
}
