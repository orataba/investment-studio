// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import MonitoringPage from './MonitoringPage'
import type { MonitoringDashboardResponse, MonitoringRecalcJobRecord } from '../lib/api'
const api = vi.hoisted(() => ({ getMonitoringDashboard: vi.fn() }))
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), ...api }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const empty: MonitoringDashboardResponse = {
  generated_at: '2026-10-15T06:15:00Z', watchlists: [], instruments: [], needs_attention_instruments: [], missing_required_metadata_instruments: [], research_queue: [], open_recalc_jobs: [],
  overview: { watchlist_count: 0, unique_instrument_count: 0, needs_refresh_count: 0, missing_quote_count: 0, missing_required_metadata_count: 0, research_review_due_count: 0, research_follow_up_due_count: 0, missing_investment_view_count: 0, open_recalc_job_count: 0, failed_recalc_job_count: 0 },
}
function show(data = empty) {
  api.getMonitoringDashboard.mockResolvedValue(data)
  return render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><MonitoringPage /></MemoryRouter></LanguageProvider>)
}
it('uses category-specific neutral labels for known zero counts', async () => {
  show()
  for (const text of ['No missing quotes', 'No reviews due', 'No follow-ups due', 'No open jobs']) {
    const label = await screen.findByText(text)
    expect(label.className).not.toMatch(/status-warning|status-error|status-stale/)
  }
  expect(screen.getByText(/Snapshot generated at/).textContent).toContain('UTC')
})
it('keeps refresh requirements separate from queued, running and failed jobs', async () => {
  const jobs = ['queued', 'running', 'failed'].map((status, index): MonitoringRecalcJobRecord => ({ recalc_job_id: `${index}`, instrument_id: `instrument-${index}`, instrument_name: `Instrument ${index}`, job_type: 'refresh', job_status: status, trigger_type: 'manual', enqueued_at: null, started_at: null, finished_at: null, error_message: null, primary_watchlist_id: null, primary_watchlist_name: null, watchlists: [] }))
  show({ ...empty, overview: { ...empty.overview, needs_refresh_count: 3, open_recalc_job_count: 3, failed_recalc_job_count: 1 }, open_recalc_jobs: jobs })
  expect(await screen.findByText('1 failed · 1 running · 1 queued')).toBeTruthy()
  expect(screen.getByText('Refresh needed')).toBeTruthy()
  expect(screen.queryByText('No open jobs')).toBeNull()
})
it('does not turn an unknown count into a healthy zero', async () => {
  show({ ...empty, overview: { ...empty.overview, missing_quote_count: null as unknown as number } })
  expect(await screen.findByText('Unknown')).toBeTruthy()
  expect(screen.queryByText('No missing quotes')).toBeNull()
})
