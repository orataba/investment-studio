import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  importPortfolioTransactionFile,
  portfolioTransactionDownloadUrl,
  portfolioTransactionTemplateUrl,
  previewPortfolioTransactionFile,
} from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('transaction file API contract', () => {
  it('maps CSV and Excel to importable export and template endpoints', () => {
    expect(portfolioTransactionDownloadUrl('portfolio/ops', 'csv')).toBe(
      '/api/portfolios/portfolio%2Fops/transactions.csv',
    )
    expect(portfolioTransactionDownloadUrl('portfolio/ops', 'xlsx')).toBe(
      '/api/portfolios/portfolio%2Fops/transactions.xlsx',
    )
    expect(portfolioTransactionTemplateUrl('portfolio/ops', 'csv')).toBe(
      '/api/portfolios/portfolio%2Fops/transactions/csv-template',
    )
    expect(portfolioTransactionTemplateUrl('portfolio/ops', 'xlsx')).toBe(
      '/api/portfolios/portfolio%2Fops/transactions/xlsx-template',
    )
  })

  it('uploads the original file for preview without forcing a JSON content type', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({ preview_digest: 'a'.repeat(64) }), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['asset_type,transaction_action'], 'transactions.xlsx')

    await previewPortfolioTransactionFile('portfolio/ops', file)

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit
    const form = request.body as FormData
    expect(request.method).toBe('POST')
    expect(request.headers).not.toHaveProperty('Content-Type')
    expect(form).toBeInstanceOf(FormData)
    expect(form.get('file')).toBe(file)
    expect(form.get('default_source_system')).toBe('portfolio_file_upload')
  })

  it('reuploads the same file with the preview digest and idempotency key', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({ created_count: 1 }), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['asset_type,transaction_action'], 'transactions.csv', { type: 'text/csv' })

    await importPortfolioTransactionFile(
      'portfolio/ops',
      file,
      'b'.repeat(64),
      'file-import-1',
    )

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit
    const form = request.body as FormData
    expect(request.headers).toEqual({ 'Idempotency-Key': 'file-import-1' })
    expect(form.get('file')).toBe(file)
    expect(form.get('preview_digest')).toBe('b'.repeat(64))
  })
})
