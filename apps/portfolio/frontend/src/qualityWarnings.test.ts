import { describe, expect, it } from 'vitest'

import { normalizeQualityWarnings } from './components/QualityWarningsNotice'

describe('quality warnings', () => {
  it('trims, removes empty values, and deduplicates warnings without changing their order', () => {
    expect(
      normalizeQualityWarnings([
        'Corporate actions are not modeled.',
        '  Corporate actions are not modeled.  ',
        '',
        'Review adjusted-price coverage.',
      ]),
    ).toEqual([
      'Corporate actions are not modeled.',
      'Review adjusted-price coverage.',
    ])
  })
})
