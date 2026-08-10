import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { MetricFamily } from '../../../../packages/instrument-core/ts/src'
import { PriceContractFields } from './PriceContractFields'

function renderContract(metricFamily: MetricFamily) {
  return renderToStaticMarkup(
    <PriceContractFields instrumentType="equity" metricFamily={metricFamily} />,
  )
}

function expectReadonlyField(markup: string, label: string, value: string) {
  expect(markup).toMatch(
    new RegExp(
      `<input(?=[^>]*aria-label="${label}")(?=[^>]*readonly="")(?=[^>]*value="${value}")[^>]*>`,
      'i',
    ),
  )
}

describe('PriceContractFields', () => {
  it('renders the deterministic readonly contract', () => {
    const priceMarkup = renderContract('price')
    expectReadonlyField(priceMarkup, 'Price unit', 'Per unit')
    expectReadonlyField(priceMarkup, 'Price scale', '1')
    expect(priceMarkup).not.toContain('<select')

    const navMarkup = renderContract('nav')
    expectReadonlyField(navMarkup, 'Price unit', 'Per unit')
    expectReadonlyField(navMarkup, 'Price scale', '1')
    expect(navMarkup).not.toContain('<select')
  })
})
