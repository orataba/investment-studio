/** Independently rebased economic-return segments must never be joined. */
export function sameReturnSegment(points: ReadonlyArray<{ segment_id?: string | null }>): boolean {
  if (!points.length) return false
  const first = points[0].segment_id ?? null
  return points.every(point => (point.segment_id ?? null) === first)
}
