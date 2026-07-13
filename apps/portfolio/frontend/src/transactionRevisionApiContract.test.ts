import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  clearPortfolioApiCache,
  deletePortfolioTransaction,
  getPortfolioTransactionRevisionHistory,
  updatePortfolioTransaction,
  type PortfolioTransactionActorInput,
  type PortfolioTransactionUpdatePayload,
} from './lib/api'

const actor: PortfolioTransactionActorInput = {
  actor_id: 'local-user:test',
  display_name: 'Test Manager',
  actor_type: 'user',
  actor_source: 'client_asserted',
}

const updatePayload: PortfolioTransactionUpdatePayload = {
  transaction_type: 'deposit',
  trade_date: '2026-07-13',
  trade_time: '12:00',
  settlement_date: '2026-07-13',
  account_id: 'cash-usd-main',
  gross_amount: '1000.10',
  fees: '0.00',
  taxes: '0.00',
  currency: 'USD',
  actor,
  expected_revision_id: 'revision-1',
  expected_revision_number: 1,
  change_reason: 'Corrected source statement',
}

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('transaction revision API contract', () => {
  it('sends precise decimal strings and optimistic concurrency metadata on update', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await updatePortfolioTransaction('portfolio-1', 'txn-1', updatePayload)

    const [, request] = fetchMock.mock.calls[0]
    if (!request) {
      throw new Error('Expected update request options.')
    }
    expect(request.method).toBe('PUT')
    expect(JSON.parse(String(request.body))).toMatchObject({
      gross_amount: '1000.10',
      expected_revision_id: 'revision-1',
      expected_revision_number: 1,
      change_reason: 'Corrected source statement',
      actor,
    })
  })

  it('sends the required audit body on DELETE', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await deletePortfolioTransaction('portfolio-1', 'txn-1', {
      expected_revision_id: 'revision-2',
      expected_revision_number: 2,
      change_reason: 'Duplicate transaction',
      actor,
    })

    const [url, request] = fetchMock.mock.calls[0]
    if (!request) {
      throw new Error('Expected delete request options.')
    }
    expect(url).toBe('/api/portfolios/portfolio-1/transactions/txn-1')
    expect(request.method).toBe('DELETE')
    expect(JSON.parse(String(request.body))).toEqual({
      expected_revision_id: 'revision-2',
      expected_revision_number: 2,
      change_reason: 'Duplicate transaction',
      actor,
    })
  })

  it('preserves structured revision-conflict details in ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              detail: {
                code: 'transaction_revision_conflict',
                transaction_id: 'txn-1',
                expected_revision_number: 1,
                actual_revision_number: 2,
                current_lifecycle_status: 'active',
              },
            }),
            { status: 409 },
          ),
        ),
      ),
    )

    const request = updatePortfolioTransaction('portfolio-1', 'txn-1', updatePayload)
    await expect(request).rejects.toBeInstanceOf(ApiError)
    await expect(request).rejects.toMatchObject({
      status: 409,
      code: 'transaction_revision_conflict',
      detail: {
        transaction_id: 'txn-1',
        expected_revision_number: 1,
        actual_revision_number: 2,
      },
    })
  })

  it('loads the immutable revision timeline endpoint', async () => {
    const payload = {
      portfolio_id: 'portfolio-1',
      transaction_id: 'txn-1',
      current_revision_id: 'revision-1',
      current_revision_number: 1,
      lifecycle_status: 'active',
      revisions: [],
    }
    const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })))
    vi.stubGlobal('fetch', fetchMock)

    await expect(getPortfolioTransactionRevisionHistory('portfolio-1', 'txn-1')).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio-1/transactions/txn-1/revisions',
      expect.any(Object),
    )
  })
})
