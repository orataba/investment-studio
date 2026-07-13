import type { PortfolioTransactionActorInput } from './api'

export const TRANSACTION_ACTOR_IDENTITY_STORAGE_KEY =
  'portfolio-operations-workbench.transaction-actor-identity.v1'

const ACTOR_TEXT_MAX_LENGTH = 128

type ActorIdentityStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function browserStorage(storage?: ActorIdentityStorage): ActorIdentityStorage | null {
  if (storage) {
    return storage
  }
  if (typeof window === 'undefined') {
    return null
  }
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function normalizeRequiredActorText(value: unknown, label: string): string {
  if (typeof value !== 'string') {
    throw new TypeError(`${label} must be a string.`)
  }
  const normalized = value.trim()
  if (!normalized) {
    throw new TypeError(`${label} is required.`)
  }
  if (normalized.length > ACTOR_TEXT_MAX_LENGTH) {
    throw new TypeError(`${label} must not exceed ${ACTOR_TEXT_MAX_LENGTH} characters.`)
  }
  return normalized
}

export function buildTransactionActorIdentity(input: {
  actor_id: string
  display_name: string
}): PortfolioTransactionActorInput {
  return {
    actor_id: normalizeRequiredActorText(input.actor_id, 'Actor ID'),
    display_name: normalizeRequiredActorText(input.display_name, 'Display name'),
    actor_type: 'user',
    actor_source: 'client_asserted',
  }
}

export function createTransactionActorIdentity(displayName: string): PortfolioTransactionActorInput {
  if (!globalThis.crypto?.randomUUID) {
    throw new Error('This browser cannot create a stable local actor identifier.')
  }
  return buildTransactionActorIdentity({
    actor_id: `local-user:${globalThis.crypto.randomUUID()}`,
    display_name: displayName,
  })
}

export function loadTransactionActorIdentity(
  storage?: ActorIdentityStorage,
): PortfolioTransactionActorInput | null {
  const resolvedStorage = browserStorage(storage)
  if (!resolvedStorage) {
    return null
  }

  let storedValue: string | null
  try {
    storedValue = resolvedStorage.getItem(TRANSACTION_ACTOR_IDENTITY_STORAGE_KEY)
  } catch {
    return null
  }
  if (!storedValue) {
    return null
  }

  try {
    const parsed = JSON.parse(storedValue) as Record<string, unknown>
    if (parsed.actor_type !== 'user' || parsed.actor_source !== 'client_asserted') {
      return null
    }
    return buildTransactionActorIdentity({
      actor_id: normalizeRequiredActorText(parsed.actor_id, 'Actor ID'),
      display_name: normalizeRequiredActorText(parsed.display_name, 'Display name'),
    })
  } catch {
    return null
  }
}

export function saveTransactionActorIdentity(
  input: { actor_id: string; display_name: string },
  storage?: ActorIdentityStorage,
): PortfolioTransactionActorInput {
  const resolvedStorage = browserStorage(storage)
  if (!resolvedStorage) {
    throw new Error('Browser storage is unavailable; the actor identity cannot be saved.')
  }
  const identity = buildTransactionActorIdentity(input)
  resolvedStorage.setItem(TRANSACTION_ACTOR_IDENTITY_STORAGE_KEY, JSON.stringify(identity))
  return identity
}

export function clearTransactionActorIdentity(storage?: ActorIdentityStorage): void {
  const resolvedStorage = browserStorage(storage)
  if (!resolvedStorage) {
    return
  }
  resolvedStorage.removeItem(TRANSACTION_ACTOR_IDENTITY_STORAGE_KEY)
}
