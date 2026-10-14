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
