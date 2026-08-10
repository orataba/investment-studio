import type {
  InstrumentType,
  MetricFamily,
  PriceUnit,
  QuoteBasis,
} from '../../../../packages/instrument-core/ts/src'


export type QuoteBasisOption = { value: QuoteBasis; label: string }

const PRICE_UNIT_OPTIONS: Array<{ value: PriceUnit; label: string }> = [
  { value: 'per_unit', label: 'Per unit' },
  { value: 'rate', label: 'Rate' },
]

export const QUOTE_BASIS_OPTIONS: Record<MetricFamily, QuoteBasisOption[]> = {
  price: [
    { value: 'last', label: 'Last Trade' },
    { value: 'close', label: 'Close' },
    { value: 'adjusted_close', label: 'Adjusted Close' },
    { value: 'par', label: 'Par' },
  ],
  nav: [
    { value: 'official_nav', label: 'Unit NAV' },
    {
      value: 'total_return_nav',
      label: 'Dividend-Reinvested Total Return NAV',
    },
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
  }
  return {
    metric_family: metricFamily,
    quote_basis: quoteBasis,
  }
}

export function quoteBasisOptions(metricFamily: MetricFamily): QuoteBasisOption[] {
  return QUOTE_BASIS_OPTIONS[metricFamily]
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
