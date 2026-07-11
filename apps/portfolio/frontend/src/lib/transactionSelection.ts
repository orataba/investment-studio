export interface TransactionSelectionSyncInput {
  currentTransactionId: string
  workspaceRequestedTransactionId: string | null
  workspaceSelectedTransactionId: string | null | undefined
}

export function stopTransactionRowSelection(event: { stopPropagation(): void }): void {
  event.stopPropagation()
}

/**
 * Resolve a URL selection update from a loaded transaction workspace.
 *
 * A workspace can remain rendered while a click has already changed the URL and
 * started a new request.  In that interval its selected transaction is stale and
 * must not overwrite the user's newer selection.
 */
export function resolveWorkspaceTransactionSelection({
  currentTransactionId,
  workspaceRequestedTransactionId,
  workspaceSelectedTransactionId,
}: TransactionSelectionSyncInput): string | null | undefined {
  if (
    workspaceRequestedTransactionId == null ||
    workspaceRequestedTransactionId !== currentTransactionId
  ) {
    return undefined
  }

  const nextTransactionId = workspaceSelectedTransactionId ?? ''
  if (nextTransactionId === currentTransactionId) {
    return undefined
  }
  return nextTransactionId || null
}
