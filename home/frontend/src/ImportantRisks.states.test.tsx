// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../packages/ui/src/i18n'
import ImportantRisks from './ImportantRisks'
import { homeMessages } from './messages'
import type { StudioApp } from './StudioHome'
import { readRiskAlerts } from './riskAlerts'
vi.mock('./riskAlerts', async original => ({ ...await original<object>(), readRiskAlerts: vi.fn() }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })
it('distinguishes unknown from empty risk and recovers only after a successful read', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  vi.mocked(readRiskAlerts).mockRejectedValueOnce(new Error('HTTP 503 · request-123'))
    .mockResolvedValueOnce({ alerts: [], total: 0, has_more: false, selection_note: '' })
  const app = { app_id: 'watchlist', url: 'http://localhost:5173' } as StudioApp
  render(<LanguageProvider messages={homeMessages}><ImportantRisks apps={[app]} /></LanguageProvider>)
  expect(await screen.findByText('Important risks cannot be read right now.')).toBeTruthy()
  expect(screen.getByText('The current risk status is unknown; this does not mean there are no important risks.')).toBeTruthy()
  expect(screen.queryByText('No items currently meet the important-risk criteria.')).toBeNull()
  expect(screen.getByText('HTTP 503 · request-123').closest('details')?.open).toBe(false)
  expect(screen.getByRole('link', { name: 'Open Watchlist' }).getAttribute('href')).toContain('/watchlists/all-instruments')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('No items currently meet the important-risk criteria.')).toBeTruthy()
  expect(screen.queryByText('Important risks cannot be read right now.')).toBeNull()
})
it('shows more than three selected risks including severe facts without pagination', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  const alerts = Array.from({ length: 5 }, (_, index) => ({ case_id: `c-${index}`, notice_id: `n-${index}`, title: `Risk ${index + 1}`, body: `Fact ${index + 1}`, level: 'high' as const, instrument_id: 'a', instrument_name: 'Asset A', impact_level: null, urgency: null, updated_at: '2026-10-14', reviewed_at: null, confidence: null, as_of: '2026-10-14', target: { workspace: 'watchlist' as const, path: '/instruments/a?risk=1' }, current_rule_facts: index === 4 ? [{ title: 'New severe fact', body: 'A new severe threshold was crossed.', as_of: '2026-10-14' }] : [] }))
  vi.mocked(readRiskAlerts).mockResolvedValueOnce({ alerts, total: 5, has_more: false, selection_note: '', notice_status: 'assessed' })
  render(<LanguageProvider messages={homeMessages}><ImportantRisks apps={[{ app_id: 'watchlist', url: 'http://localhost:5173' } as StudioApp]} /></LanguageProvider>)
  await screen.findByText('Risk 5')
  expect(screen.getAllByRole('listitem')).toHaveLength(5)
  expect(screen.getByText('A new severe threshold was crossed.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Next risks' })).toBeNull()
})
