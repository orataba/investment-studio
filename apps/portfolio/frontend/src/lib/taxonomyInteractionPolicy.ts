export const TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE =
  'Allocation bases, targets, and concentration limits can be edited. Taxonomy structure and assignment moves are locked until Save or Cancel.'

type TaxonomyEntityDragPolicy = {
  targetEditMode: boolean
  lockedEntity: boolean
  ambiguousEntity: boolean
  actionPending: boolean
}

type TaxonomyNodeDropPolicy = {
  targetEditMode: boolean
  terminalNode: boolean
  actionPending: boolean
}

export function canDragTaxonomyEntity({
  targetEditMode,
  lockedEntity,
  ambiguousEntity,
  actionPending,
}: TaxonomyEntityDragPolicy) {
  return !targetEditMode && !lockedEntity && !ambiguousEntity && !actionPending
}

export function canDropTaxonomyEntity({
  targetEditMode,
  terminalNode,
  actionPending,
}: TaxonomyNodeDropPolicy) {
  return !targetEditMode && terminalNode && !actionPending
}
