import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../packages/ui/src/i18n'
import { ImportantRiskEntries } from './ImportantRisks'
import type { RiskAlert } from './riskAlerts'
import { homeMessages } from './messages'

afterEach(() => vi.unstubAllGlobals())

it('keeps the assessed title and dates distinct from an urgent unverified lead and routes to the authorized workspace', () => {
  vi.stubGlobal('window', { location: { href: 'http://127.0.0.1:5172/?lang=en', search: '?lang=en', hostname: '127.0.0.1', protocol: 'http:', port: '5172' } })
  const assessed: RiskAlert = { case_id: 'old', title: 'Retained financing risk', instrument_id: 'a', instrument_name: 'Fund A', impact_level: 'major', urgency: 'soon', updated_at: '2026-10-12', reviewed_at: '2026-09-05T12:00:00Z', confidence: 'confirmed', as_of: '2026-09-05T08:00:00Z', alert_state: 'assessment_pending', target: { workspace: 'watchlist', path: '/instruments/a?tab=investment-research&risk=1' } }
  const pending: RiskAlert = { ...assessed, case_id: 'new', title: 'Time-sensitive disclosure to verify', reviewed_at: null, confidence: 'reported', as_of: '2026-10-12T08:00:00Z', alert_state: 'unverified_urgent' }
  const html = renderToStaticMarkup(<LanguageProvider messages={homeMessages}><ImportantRiskEntries apps={[]} alerts={[assessed, pending]} /></LanguageProvider>)
  expect(html).toContain('Retained financing risk')
  expect(html).toContain('New information awaiting review')
  expect(html).toContain('Major lead — unverified')
  expect(html).toContain('Time-sensitive disclosure to verify')
  expect(html).toContain('Reviewed: 2026-09-05 12:00:00 UTC')
  expect(html).toContain('Reviewed: —')
  expect(html).toContain('/instruments/a?tab=investment-research&amp;risk=1')
})
