import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { MetricFamily } from '../../../../packages/instrument-core/ts/src'
import { PriceContractFields } from './PriceContractFields'

function renderBondContract(metricFamily: MetricFamily) {
  return renderToStaticMarkup(
    <PriceContractFields instrumentType="bond" metricFamily={metricFamily} />,
  )
}

function expectReadonlyField(markup: string, label: string, value: string) {
  expect(markup).toMatch(
    new RegExp(
      `<input(?=[^>]*aria-label="${label}")(?=[^>]*readonly="")(?=[^>]*value="${value}")[^>]*>`,
    ),
  )
}

describe('PriceContractFields', () => {
  it('renders the deterministic readonly contract as a bond changes metric family', () => {
    const priceMarkup = renderBondContract('price')
    expectReadonlyField(priceMarkup, 'Price unit', 'Percent of par')
    expectReadonlyField(priceMarkup, 'Price scale', '0.01')
    expect(priceMarkup).not.toContain('<select')

    const navMarkup = renderBondContract('nav')
    expectReadonlyField(navMarkup, 'Price unit', 'Per unit')
    expectReadonlyField(navMarkup, 'Price scale', '1')
    expect(navMarkup).not.toContain('<select')
  })
})
