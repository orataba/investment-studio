import { describe, expect, it } from 'vitest'

import {
  buildPerformanceWindowFilters,
  normalizePerformanceWindowSelection,
  performancePresetStartDate,
  resolvePerformanceWindow,
  shiftIsoDate,
  validIsoDate,
} from './lib/performanceWindow'

describe('performance window extraction contract', () => {
  it('uses prior-close anchors for MTD, QTD, YTD and a leap-day-clamped 1Y anchor', () => {
    expect(performancePresetStartDate('mtd', '2026-07-15')).toBe('2026-06-30')
    expect(performancePresetStartDate('qtd', '2026-07-15')).toBe('2026-06-30')
    expect(performancePresetStartDate('ytd', '2026-07-15')).toBe('2025-12-31')
    expect(performancePresetStartDate('1y', '2024-02-29')).toBe('2023-02-28')
    expect(shiftIsoDate('2026-03-01', -1)).toBe('2026-02-28')
  })

  it('normalizes persisted values without accepting non-ISO inputs', () => {
    expect(validIsoDate('2026-07-15')).toBe('2026-07-15')
    expect(validIsoDate('07/15/2026')).toBe('')
    expect(
      normalizePerformanceWindowSelection({
        startDate: '2026-01-01',
        endDate: 'bad',
        mode: 'dates',
      }),
    ).toEqual({ startDate: '2026-01-01', endDate: '', mode: 'dates' })
    expect(
      normalizePerformanceWindowSelection({ mode: 'since_inception' }),
    ).toEqual({ startDate: '', endDate: '', mode: 'since_inception' })
    expect(normalizePerformanceWindowSelection({})).toBeNull()
  })

  it('resolves query, stored, summary and default boundaries with the old precedence', () => {
    expect(
      resolvePerformanceWindow({
        queryStartDate: '2026-04-01',
        queryEndDate: '2026-06-30',
        querySinceInception: false,
        storedSelection: {
          startDate: '2025-01-01',
          endDate: '2025-12-31',
          mode: 'dates',
        },
        portfolioAsOfDate: '2026-07-15',
        todayDate: '2026-07-16',
        portfolioSummarySettled: true,
        defaultLookbackDays: 30,
      }),
    ).toEqual({
      appliedSinceInception: false,
      appliedStartDate: '2026-04-01',
      appliedEndDate: '2026-06-30',
      waitingForDefaultEndDate: false,
      effectiveStartDate: '2026-04-01',
      effectiveEndDate: '2026-06-30',
    })

    expect(
      resolvePerformanceWindow({
        querySinceInception: true,
        portfolioAsOfDate: '2026-07-15',
        todayDate: '2026-07-16',
        portfolioSummarySettled: true,
        defaultLookbackDays: 30,
      }),
    ).toMatchObject({
      appliedSinceInception: true,
      effectiveStartDate: '',
      effectiveEndDate: '2026-07-15',
    })
  })

  it('waits for the summary before deriving a default end and shares one request filter', () => {
    expect(
      resolvePerformanceWindow({
        querySinceInception: false,
        todayDate: '2026-07-15',
        portfolioSummarySettled: false,
        defaultLookbackDays: 30,
      }),
    ).toMatchObject({
      waitingForDefaultEndDate: true,
      effectiveStartDate: '2026-06-15',
      effectiveEndDate: '2026-07-15',
    })
    expect(buildPerformanceWindowFilters('', '2026-07-15')).toEqual({
      end_date: '2026-07-15',
    })
    expect(buildPerformanceWindowFilters('2026-07-01', '2026-07-15')).toEqual({
      start_date: '2026-07-01',
      end_date: '2026-07-15',
    })
  })
})
