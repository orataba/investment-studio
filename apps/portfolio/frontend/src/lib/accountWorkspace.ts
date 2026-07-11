export type AccountWorkspaceSelectionSyncInput = {
  currentAccountId: string
  workspaceRequestedAccountId: string | null
  workspaceSelectedAccountId: string | null | undefined
}

export function accountWorkspaceResourceId(portfolioId: string, accountId: string | null | undefined) {
  return `${portfolioId}:${accountId ?? ''}`
}

export function resolvedAccountWorkspaceResourceId(
  portfolioId: string,
  requestedAccountId: string,
  selectedAccountId: string | null | undefined,
) {
  return accountWorkspaceResourceId(portfolioId, selectedAccountId ?? requestedAccountId)
}

export function shouldLoadAccountWorkspace(
  resourceId: string,
  loadedResourceId: string | null,
  inFlightResourceId: string | null = null,
) {
  return resourceId !== loadedResourceId && resourceId !== inFlightResourceId
}

/**
 * Resolve a URL selection update from the workspace that answered the active request.
 * Returning undefined means the response belongs to an older selection and must not
 * write over the user's newer URL state.
 */
export function resolveWorkspaceAccountSelection({
  currentAccountId,
  workspaceRequestedAccountId,
  workspaceSelectedAccountId,
}: AccountWorkspaceSelectionSyncInput): string | null | undefined {
  if (
    workspaceRequestedAccountId == null ||
    workspaceRequestedAccountId !== currentAccountId
  ) {
    return undefined
  }

  const nextAccountId = workspaceSelectedAccountId ?? ''
  if (nextAccountId === currentAccountId) {
    return undefined
  }
  return nextAccountId || null
}

export function resolveSettlementCashAccountId(
  currentAccountId: string,
  compatibleAccountIds: readonly string[],
) {
  if (currentAccountId && compatibleAccountIds.includes(currentAccountId)) {
    return currentAccountId
  }
  return compatibleAccountIds[0] ?? ''
}
