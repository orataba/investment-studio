import { describe, expect, it } from 'vitest'

import {
  TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE,
  canDragTaxonomyEntity,
  canDropTaxonomyEntity,
  targetDimensionEnabledForDraft,
} from './lib/taxonomyInteractionPolicy'
import taxonomiesPageSource from './pages/TaxonomiesPage.tsx?raw'

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

  it('wires the policy to both drag sources, drop targets, and target editors', () => {
    expect(taxonomiesPageSource).toContain('draggable={assignmentDragEnabled}')
    expect(taxonomiesPageSource).toContain(
      'onDragOver={assignmentDropEnabled ? (event) => handleNodeDragOver(event, node) : undefined}',
    )
    expect(taxonomiesPageSource).toContain('onDrop={assignmentDropEnabled ? (event) => void handleNodeDrop(event, node) : undefined}')
    expect(taxonomiesPageSource).toContain('id="taxonomy-target-edit-lock-message"')
    expect(taxonomiesPageSource).toContain('Edit Targets')
    expect(taxonomiesPageSource).toContain('onDragStart={preventTargetEditorDrag}')
    expect(taxonomiesPageSource).toContain('placeholder="Required"')
    expect(taxonomiesPageSource).toContain('aria-invalid={targetWeightMissing}')
    expect(taxonomiesPageSource).toContain('aria-invalid={targetRiskMissing}')
  })

  it('preserves explicitly disabled dimensions when editing an existing target set', () => {
    expect(targetDimensionEnabledForDraft(false, true)).toBe(false)
    expect(targetDimensionEnabledForDraft(true, false)).toBe(true)
    expect(targetDimensionEnabledForDraft(undefined, true)).toBe(true)
    expect(targetDimensionEnabledForDraft(null, false)).toBe(false)
  })
})
