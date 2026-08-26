import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  commitPortfolioTransactionImport,
  createPortfolioTransactionCaptureAnalysisRevision,
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

  it('stores a human screenshot review as a new analysis revision', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)
    const payload = {
      source: 'human' as const,
      finish_reason: 'human_review_confirmed',
      schema_version: 'portfolio.transaction-capture-analysis.v2' as const,
      analysis: {
        summary: 'Reviewed proposal.',
        documents: [{ capture_id: 'capture-1', document_kind: 'trade_activity' }],
        candidates: [],
        questions: [],
      },
      transaction_import: {
        source_system: 'portfolio_screenshot_assistant',
        records: [{
          external_reference: 'batch/1#1',
          asset_type: 'security' as const,
          transaction_action: 'buy' as const,
          trade_date: '2026-08-25',
          account_id: 'broker-usd',
          instrument_id: 'asset-1',
          quantity: '1',
          price: '10',
          gross_amount: '10',
          currency: 'USD',
        }],
      },
    }

    await createPortfolioTransactionCaptureAnalysisRevision(
      'portfolio/ops',
      'batch/1',
      payload,
    )

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/portfolios/portfolio%2Fops/transaction-capture-batches/batch%2F1/analysis-revisions',
    )
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(request.method).toBe('POST')
    expect(JSON.parse(String(request.body))).toEqual(payload)
  })

  it('commits the unchanged reviewed JSON proposal with an idempotency key', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({ created_count: 1 }), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)
    const transactionImport = {
      source_system: 'portfolio_screenshot_assistant',
      records: [{
        external_reference: 'batch-1#1',
        asset_type: 'cash' as const,
        transaction_action: 'deposit' as const,
        trade_date: '2026-08-25',
        account_id: 'cash-usd',
        gross_amount: '100',
        currency: 'USD',
      }],
    }

    await commitPortfolioTransactionImport(
      'portfolio/ops',
      transactionImport,
      'd'.repeat(64),
      'capture-import-1',
    )

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/portfolios/portfolio%2Fops/transaction-imports/commit',
    )
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(request.headers).toEqual({
      'Content-Type': 'application/json',
      'Idempotency-Key': 'capture-import-1',
    })
    expect(JSON.parse(String(request.body))).toEqual({
      ...transactionImport,
      preview_digest: 'd'.repeat(64),
    })
  })
})
