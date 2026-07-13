import { describe, expect, it } from 'vitest'

import {
  TRANSACTION_ACTOR_IDENTITY_STORAGE_KEY,
  buildTransactionActorIdentity,
  clearTransactionActorIdentity,
  loadTransactionActorIdentity,
  saveTransactionActorIdentity,
} from './lib/actorIdentity'

function memoryStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    values,
  }
}

describe('transaction actor identity', () => {
  it('persists only a normalized client-asserted user identity', () => {
    const storage = memoryStorage()
    const saved = saveTransactionActorIdentity(
      { actor_id: ' local-user:abc ', display_name: ' Fund Manager ' },
      storage,
    )

    expect(saved).toEqual({
      actor_id: 'local-user:abc',
      display_name: 'Fund Manager',
      actor_type: 'user',
      actor_source: 'client_asserted',
    })
    expect(loadTransactionActorIdentity(storage)).toEqual(saved)

    clearTransactionActorIdentity(storage)
    expect(storage.values.has(TRANSACTION_ACTOR_IDENTITY_STORAGE_KEY)).toBe(false)
    expect(loadTransactionActorIdentity(storage)).toBeNull()
  })

  it('rejects blank identities and ignores a stored actor with false provenance', () => {
    expect(() =>
      buildTransactionActorIdentity({ actor_id: 'local-user:abc', display_name: '  ' }),
    ).toThrow('Display name is required')

    const storage = memoryStorage()
    storage.setItem(
      TRANSACTION_ACTOR_IDENTITY_STORAGE_KEY,
      JSON.stringify({
        actor_id: 'service',
        display_name: 'Spoofed Service',
        actor_type: 'service',
        actor_source: 'trusted_service',
      }),
    )
    expect(loadTransactionActorIdentity(storage)).toBeNull()
  })
})
