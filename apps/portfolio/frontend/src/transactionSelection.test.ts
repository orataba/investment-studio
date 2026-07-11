import { describe, expect, it, vi } from 'vitest'

import {
  resolveWorkspaceTransactionSelection,
  stopTransactionRowSelection,
} from './lib/transactionSelection'

describe('transaction workspace selection synchronization', () => {
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
