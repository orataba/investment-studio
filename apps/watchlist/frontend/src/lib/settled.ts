export function settledValue<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === 'fulfilled' ? result.value : fallback
}

export function rejectedLabels(
  results: readonly PromiseSettledResult<unknown>[],
  labels: readonly string[],
) {
  return results.flatMap((result, index) =>
    result.status === 'rejected' && labels[index] ? [labels[index]] : [],
  )
}
