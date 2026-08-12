import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import DataOperationsDashboard from './DataOperationsDashboard'
import {
  EmailNavInventoryPanel,
  type EmailNavInventoryPayload,
} from './EmailNavInventoryPanel'


const inventory: EmailNavInventoryPayload = {
  limit: 100,
  counts: {
    candidate_routes: 12,
    matched_unimported: 2,
    unresolved_identity_count: 1,
    unresolved_occurrence_count: 3,
    folder_count: 2,
    total_uid_lag: 4,
    folders_with_lag: 1,
    folders_with_error: 1,
    message_retryable: 1,
    message_dead_letter: 0,
    parse_retryable: 0,
    parse_dead_letter: 1,
    parse_unsupported: 0,
  },
  routing_statuses: { matched: 9, unmatched: 2, ambiguous: 1 },
  validation_statuses: { valid: 9, pending: 2, rejected: 1 },
  unresolved_identities: [
    {
      identity_id: 'identity-1',
      instrument_code: 'YG-UNKNOWN',
      instrument_name: '云谷待确认基金',
      first_nav_date: '2026-07-01',
      latest_nav_date: '2026-07-15',
      occurrence_count: 3,
      routing_statuses: { unmatched: 2, ambiguous: 0, rejected: 1 },
      example: {
        folder_name: '云谷3号',
        attachment_name: '净值.xlsx',
        reason: 'No exact Registry identity.',
      },
    },
  ],
  folders: [
    {
      folder_id: 'folder-1',
      folder_name: 'INBOX',
      last_committed_uid: 100,
      last_observed_uid_next: 105,
      uid_lag: 4,
      last_scan_started_at: '2026-07-16T01:00:00+00:00',
      last_scan_succeeded_at: null,
      error: {
        at: '2026-07-16T01:01:00+00:00',
        code: 'FolderScanError',
        reason: 'Temporary scan failure.',
      },
    },
  ],
  message_failures: [
    {
      failure_id: 'message-1',
      status: 'retryable',
      folder_name: 'INBOX',
      attempt_count: 2,
      next_retry_at: '2026-07-16T02:00:00+00:00',
      error_code: 'FetchError',
      reason: 'Temporary fetch failure.',
    },
  ],
  parse_failures: [
    {
      failure_id: 'parse-1',
      status: 'dead_letter',
      folder_name: '云谷3号',
      attachment_name: 'broken.xlsx',
      source_format: 'xlsx',
      attempt_count: 5,
      next_retry_at: null,
      error_code: 'ParseError',
      reason: 'Workbook cannot be parsed.',
    },
  ],
  automatic_fund_creation: false,
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('DataOperationsDashboard', () => {
  it('renders only the three workspace entrances on Home', () => {
    vi.stubGlobal('window', {
      location: {
        hostname: '127.0.0.1',
        protocol: 'http:',
        search: '',
      },
    })

    const markup = renderToStaticMarkup(
      <LanguageProvider>
        <DataOperationsDashboard />
      </LanguageProvider>,
    )

    expect(markup).toContain('href="/watchlist"')
    expect(markup).toContain('href="/portfolio"')
    expect(markup).toContain('href="/instruments"')
    expect(markup).toContain('Watchlist')
    expect(markup).toContain('Portfolio')
    expect(markup).toContain('Instrument Registry')
    expect(markup).not.toContain('Email NAV')
    expect(markup).not.toContain('operational status')
    expect(markup).not.toContain('/api/dashboard')
  })
})

describe('EmailNavInventoryPanel', () => {
  it('renders durable identity, folder, and failure review without an auto-create action', () => {
    const markup = renderToStaticMarkup(<EmailNavInventoryPanel inventory={inventory} />)

    expect(markup).toContain('Trackable funds needing identity review')
    expect(markup).toContain('automatic fund creation is disabled')
    expect(markup).toContain('云谷待确认基金')
    expect(markup).toContain('2026-07-01 – 2026-07-15')
    expect(markup).toContain('unmatched 2 · rejected 1')
    expect(markup).toContain('Folder cursors')
    expect(markup).toContain('Temporary scan failure.')
    expect(markup).toContain('Workbook cannot be parsed.')
    expect(markup).not.toContain('Create fund')
  })
})
