import type {
  InstrumentType,
  MetricFamily,
} from '../../../../packages/instrument-core/ts/src'
import { canonicalPriceContract } from '../../../../packages/instrument-core/ts/src'
import { formatPriceUnit } from './marketDataContract'

export function PriceContractFields({
  instrumentType,
  metricFamily,
}: {
  instrumentType: InstrumentType
  metricFamily: MetricFamily
}) {
  const contract = canonicalPriceContract(instrumentType, metricFamily)

  return (
    <>
      <label>
        <span>Unit</span>
        <input
          value={formatPriceUnit(contract.price_unit)}
          readOnly
          aria-label="Price unit"
        />
      </label>
      <label>
        <span>Scale</span>
        <input value={contract.price_scale} readOnly aria-label="Price scale" />
      </label>
    </>
  )
}
