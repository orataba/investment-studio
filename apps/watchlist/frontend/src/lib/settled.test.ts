import { describe, expect, it } from 'vitest'

import { rejectedLabels, settledValue } from './settled'
import { clampColumnWidth, nextSortAction } from './tableControls'

describe('partial detail loading', () => {
  it('uses successful sections and only falls back failed sections', async () => {
    const [summary, chart] = await Promise.allSettled([
      Promise.resolve('fresh summary'),
      Promise.reject(new Error('chart unavailable')),
    ] as const)

    expect(settledValue(summary, 'old summary')).toBe('fresh summary')
    expect(settledValue(chart, 'old chart')).toBe('old chart')
    expect(rejectedLabels([summary, chart], ['summary', 'chart'])).toEqual(['chart'])
  })
})

describe('accessible table controls', () => {
  it('describes the actual next action in the three-state sort cycle', () => {
    expect(nextSortAction(false, 'asc')).toBe('ascending')
    expect(nextSortAction(true, 'asc')).toBe('descending')
    expect(nextSortAction(true, 'desc')).toBe('clear')
  })

  it('keeps keyboard column resizing within its announced range', () => {
    expect(clampColumnWidth(80, 90, 420)).toBe(90)
    expect(clampColumnWidth(250, 90, 420)).toBe(250)
    expect(clampColumnWidth(500, 90, 420)).toBe(420)
  })
})
