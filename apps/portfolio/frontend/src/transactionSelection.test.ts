import { describe, expect, it, vi } from 'vitest'

import {
  resolveWorkspaceTransactionSelection,
  stopTransactionRowSelection,
  transactionWorkspaceQueryKey,
} from './lib/transactionSelection'

describe('transaction workspace selection synchronization', () => {
  it('separates portfolio, transaction, and every financial filter when identifying a loaded workspace', () => {
    const initial = transactionWorkspaceQueryKey('p1', {}, 'txn-1')
    expect(transactionWorkspaceQueryKey('p2', {}, 'txn-1')).not.toBe(initial)
    expect(transactionWorkspaceQueryKey('p1', {}, 'txn-2')).not.toBe(initial)
    for (const filters of [
      { account_id: 'cash-1' }, { asset_domain: 'cash' as const },
      { asset_subtype: 'option' as const }, { transaction_type: 'buy' },
      { position_reference_id: 'instrument-1' }, { start_date: '2026-09-01' }, { end_date: '2026-09-20' },
    ]) expect(transactionWorkspaceQueryKey('p1', filters, 'txn-1')).not.toBe(initial)
  })

  it('preserves a newly clicked row while the previous workspace is still rendered', () => {
    expect(
      resolveWorkspaceTransactionSelection({
        currentTransactionId: 'txn-0048',
        workspaceRequestedTransactionId: 'txn-0049',
        workspaceSelectedTransactionId: 'txn-0049',
      }),
    ).toBeUndefined()
  })

  it('adopts the server default only for the request that loaded the workspace', () => {
    expect(
      resolveWorkspaceTransactionSelection({
        currentTransactionId: '',
        workspaceRequestedTransactionId: '',
        workspaceSelectedTransactionId: 'txn-0049',
      }),
    ).toBe('txn-0049')
  })

  it('replaces an invalid requested id with the server-selected fallback', () => {
    expect(
      resolveWorkspaceTransactionSelection({
        currentTransactionId: 'missing-transaction',
        workspaceRequestedTransactionId: 'missing-transaction',
        workspaceSelectedTransactionId: 'txn-0049',
      }),
    ).toBe('txn-0049')
  })

  it('keeps an account link click from selecting the enclosing transaction row', () => {
    const stopPropagation = vi.fn()

    stopTransactionRowSelection({ stopPropagation })

    expect(stopPropagation).toHaveBeenCalledOnce()
  })
})
