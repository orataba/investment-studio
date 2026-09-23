import { describe, expect, it } from 'vitest'

import {
  TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE,
  canDragTaxonomyEntity,
  canDropTaxonomyEntity,
} from './lib/taxonomyInteractionPolicy'

describe('taxonomy target edit interaction policy', () => {
  it('pauses both assignment drag sources and drop targets while target values are edited', () => {
    expect(
      canDragTaxonomyEntity({
        targetEditMode: true,
        lockedEntity: false,
        ambiguousEntity: false,
        actionPending: false,
      }),
    ).toBe(false)
    expect(
      canDropTaxonomyEntity({
        targetEditMode: true,
        terminalNode: true,
        actionPending: false,
      }),
    ).toBe(false)
    expect(TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE).toContain('Save or Cancel')
    expect(TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE).toContain('structure')
  })

  it('restores assignment dragging and terminal-node drops outside target edit mode', () => {
    expect(
      canDragTaxonomyEntity({
        targetEditMode: false,
        lockedEntity: false,
        ambiguousEntity: false,
        actionPending: false,
      }),
    ).toBe(true)
    expect(
      canDropTaxonomyEntity({
        targetEditMode: false,
        terminalNode: true,
        actionPending: false,
      }),
    ).toBe(true)
  })

  it('keeps system, ambiguous, busy, and branch destinations non-draggable', () => {
    expect(
      canDragTaxonomyEntity({
        targetEditMode: false,
        lockedEntity: true,
        ambiguousEntity: false,
        actionPending: false,
      }),
    ).toBe(false)
    expect(
      canDragTaxonomyEntity({
        targetEditMode: false,
        lockedEntity: false,
        ambiguousEntity: true,
        actionPending: false,
      }),
    ).toBe(false)
    expect(
      canDropTaxonomyEntity({
        targetEditMode: false,
        terminalNode: false,
        actionPending: false,
      }),
    ).toBe(false)
    expect(
      canDropTaxonomyEntity({
        targetEditMode: false,
        terminalNode: true,
        actionPending: true,
      }),
    ).toBe(false)
  })

})
