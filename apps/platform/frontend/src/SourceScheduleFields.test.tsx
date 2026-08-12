import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { SourceScheduleFields } from './SourceScheduleFields'


describe('SourceScheduleFields', () => {
  it('renders all schedule semantics as editable controlled fields', () => {
    const markup = renderToStaticMarkup(
      <SourceScheduleFields
        expectedFrequency="daily"
        marketCalendar="CN_FUND_WEEKLY"
        releaseLagDays={2}
        onExpectedFrequencyChange={() => undefined}
        onMarketCalendarChange={() => undefined}
        onReleaseLagDaysChange={() => undefined}
      />,
    )

    expect(markup).toContain('aria-label="Expected frequency"')
    expect(markup).toContain('<option value="daily" selected="">Daily</option>')
    expect(markup).toContain('<option value="event_driven">Event driven</option>')
    expect(markup).toMatch(
      /<input(?=[^>]*aria-label="Market calendar")(?=[^>]*value="CN_FUND_WEEKLY")[^>]*>/,
    )
    expect(markup).toMatch(
      /<input(?=[^>]*aria-label="Release lag days")(?=[^>]*type="number")(?=[^>]*min="0")(?=[^>]*value="2")[^>]*>/,
    )
  })
})
