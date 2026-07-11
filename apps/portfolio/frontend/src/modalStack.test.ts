import { describe, expect, it } from 'vitest'

import { ModalStack } from '../../../../packages/ui/src/modalStack'

describe('modal stack', () => {
  it('only treats the most recently opened modal as topmost', () => {
    const stack = new ModalStack()
    const drawer = Symbol('drawer')
    const confirmation = Symbol('confirmation')

    stack.register(drawer)
    stack.register(confirmation)

    expect(stack.isTopmost(drawer)).toBe(false)
    expect(stack.isTopmost(confirmation)).toBe(true)
    expect(stack.unregister(confirmation)).toBe(true)
    expect(stack.isTopmost(drawer)).toBe(true)
  })

  it('does not restore an underlying modal when it closes below the topmost modal', () => {
    const stack = new ModalStack()
    const drawer = Symbol('drawer')
    const confirmation = Symbol('confirmation')

    stack.register(drawer)
    stack.register(confirmation)

    expect(stack.unregister(drawer)).toBe(false)
    expect(stack.isTopmost(confirmation)).toBe(true)
    expect(stack.size).toBe(1)
  })

  it('keeps registration idempotent and ignores unknown removals', () => {
    const stack = new ModalStack()
    const modal = Symbol('modal')

    stack.register(modal)
    stack.register(modal)

    expect(stack.size).toBe(1)
    expect(stack.unregister(Symbol('unknown'))).toBe(false)
    expect(stack.isTopmost(modal)).toBe(true)
  })
})
