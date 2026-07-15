import type { ExpectedFrequency } from '../../../../packages/instrument-core/ts/src'


const FREQUENCY_OPTIONS: Array<{ value: ExpectedFrequency; label: string }> = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'event_driven', label: 'Event driven' },
]


export function SourceScheduleFields({
  expectedFrequency,
  marketCalendar,
  releaseLagDays,
  onExpectedFrequencyChange,
  onMarketCalendarChange,
  onReleaseLagDaysChange,
}: {
  expectedFrequency: ExpectedFrequency
  marketCalendar: string
  releaseLagDays: number
  onExpectedFrequencyChange: (value: ExpectedFrequency) => void
  onMarketCalendarChange: (value: string) => void
  onReleaseLagDaysChange: (value: number) => void
}) {
  return (
    <>
      <label>
        <span>Expected Frequency</span>
        <select
          aria-label="Expected frequency"
          value={expectedFrequency}
          onChange={(event) => onExpectedFrequencyChange(event.target.value as ExpectedFrequency)}
        >
          {FREQUENCY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Market Calendar</span>
        <input
          aria-label="Market calendar"
          value={marketCalendar}
          onChange={(event) => onMarketCalendarChange(event.target.value)}
          placeholder="XSHG / XSHE / XHKG (optional)"
        />
      </label>
      <label>
        <span>Release Lag (days)</span>
        <input
          aria-label="Release lag days"
          type="number"
          min="0"
          step="1"
          value={releaseLagDays}
          onChange={(event) => {
            const nextValue = Number.parseInt(event.target.value, 10)
            onReleaseLagDaysChange(Number.isFinite(nextValue) ? Math.max(0, nextValue) : 0)
          }}
        />
      </label>
    </>
  )
}
