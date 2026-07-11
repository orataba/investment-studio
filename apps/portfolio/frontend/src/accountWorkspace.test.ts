import { describe, expect, it } from 'vitest'

import {
  beginRequest,
  isRequestCurrent,
} from '../../../../packages/ui/src/requestIdentity'
import {
  accountWorkspaceResourceId,
  resolvedAccountWorkspaceResourceId,
  resolveSettlementCashAccountId,
  resolveWorkspaceAccountSelection,
  shouldLoadAccountWorkspace,
} from './lib/accountWorkspace'

describe('account workspace loading', () => {
  it('treats the server default as loaded after synchronizing an empty URL selection', () => {
    const initialResource = accountWorkspaceResourceId('portfolio-3', '')
    expect(shouldLoadAccountWorkspace(initialResource, null)).toBe(true)
    expect(shouldLoadAccountWorkspace(initialResource, null, initialResource)).toBe(false)

    const selectedAccountId = resolveWorkspaceAccountSelection({
      currentAccountId: '',
      workspaceRequestedAccountId: '',
      workspaceSelectedAccountId: 'cash-3',
    })
    const loadedResource = resolvedAccountWorkspaceResourceId(
      'portfolio-3',
      '',
      selectedAccountId,
    )

    expect(selectedAccountId).toBe('cash-3')
    expect(
      shouldLoadAccountWorkspace(
        accountWorkspaceResourceId('portfolio-3', selectedAccountId),
        loadedResource,
      ),
    ).toBe(false)
  })

  it('does not let an older account response overwrite a newer selection', () => {
    const sequence = { current: 0 }
    const cashRequest = beginRequest(sequence, accountWorkspaceResourceId('portfolio-3', 'cash-3'))
    const brokerRequest = beginRequest(sequence, accountWorkspaceResourceId('portfolio-3', 'broker-3'))

    expect(
      isRequestCurrent(
        sequence,
        cashRequest,
        accountWorkspaceResourceId('portfolio-3', 'broker-3'),
      ),
    ).toBe(false)
    expect(
      isRequestCurrent(
        sequence,
        brokerRequest,
        accountWorkspaceResourceId('portfolio-3', 'broker-3'),
      ),
    ).toBe(true)
    expect(
      resolveWorkspaceAccountSelection({
        currentAccountId: 'broker-3',
        workspaceRequestedAccountId: 'cash-3',
        workspaceSelectedAccountId: 'cash-3',
      }),
    ).toBeUndefined()
  })
})

describe('settlement cash account reconciliation', () => {
  it('keeps a compatible selection and falls back only when the id changes', () => {
    expect(resolveSettlementCashAccountId('cash-cny', ['cash-cny', 'cash-cny-2'])).toBe('cash-cny')
    expect(resolveSettlementCashAccountId('cash-usd', ['cash-cny', 'cash-cny-2'])).toBe('cash-cny')
  })

  it('stabilizes at an empty id when no compatible cash account exists', () => {
    expect(resolveSettlementCashAccountId('cash-usd', [])).toBe('')
    expect(resolveSettlementCashAccountId('', [])).toBe('')
  })
})
