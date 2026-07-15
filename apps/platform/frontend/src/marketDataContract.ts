import type {
  InstrumentType,
  MetricFamily,
  PriceUnit,
  QuoteBasis,
} from '../../../../packages/instrument-core/ts/src'


export type QuoteBasisOption = { value: QuoteBasis; label: string }

const PRICE_UNIT_OPTIONS: Array<{ value: PriceUnit; label: string }> = [
  { value: 'per_unit', label: 'Per unit' },
  { value: 'percent_of_par', label: 'Percent of par' },
  { value: 'rate', label: 'Rate' },
]

export const QUOTE_BASIS_OPTIONS: Record<MetricFamily, QuoteBasisOption[]> = {
  price: [
    { value: 'last', label: 'Last Trade' },
    { value: 'close', label: 'Close' },
    { value: 'adjusted_close', label: 'Adjusted Close' },
    { value: 'clean_price', label: 'Clean Price' },
    { value: 'dirty_price', label: 'Dirty Price' },
    { value: 'accrued_interest', label: 'Accrued Interest' },
    { value: 'par', label: 'Par' },
  ],
  nav: [
    { value: 'official_nav', label: 'Official NAV' },
    { value: 'total_return_nav', label: 'Total Return NAV' },
  ],
  fx: [{ value: 'spot', label: 'Spot' }],
}

export function defaultMarketDataSelection(instrumentType: InstrumentType): {
  metric_family: MetricFamily
  quote_basis: QuoteBasis
} {
  let metricFamily: MetricFamily = 'price'
  let quoteBasis: QuoteBasis = 'close'
  if (instrumentType === 'fx') {
    metricFamily = 'fx'
    quoteBasis = 'spot'
  } else if (instrumentType === 'cash') {
    quoteBasis = 'par'
  } else if (instrumentType === 'bond') {
    quoteBasis = 'dirty_price'
  } else if (instrumentType === 'fund') {
    metricFamily = 'nav'
    quoteBasis = 'official_nav'
  }
  return {
    metric_family: metricFamily,
    quote_basis: quoteBasis,
  }
}

export function quoteBasisOptionsForInstrument(
  instrumentType: InstrumentType,
  metricFamily: MetricFamily,
): QuoteBasisOption[] {
  const options = QUOTE_BASIS_OPTIONS[metricFamily]
  if (instrumentType === 'bond' && metricFamily === 'price') {
    return options
  }
  return options.filter((option) => option.value !== 'accrued_interest')
}

export function formatPriceUnit(priceUnit: PriceUnit): string {
  return PRICE_UNIT_OPTIONS.find((option) => option.value === priceUnit)?.label ?? priceUnit
}

export function formatPriceContract(
  priceUnit: PriceUnit,
  priceScale: string,
): string {
  return `${formatPriceUnit(priceUnit)} × ${priceScale}`
}
