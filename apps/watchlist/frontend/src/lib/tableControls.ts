export function nextSortAction(
  active: boolean,
  direction: string,
): 'ascending' | 'descending' | 'clear' {
  if (!active) {
    return 'ascending'
  }
  return direction.toLowerCase() === 'asc' ? 'descending' : 'clear'
}

export function clampColumnWidth(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value))
}
