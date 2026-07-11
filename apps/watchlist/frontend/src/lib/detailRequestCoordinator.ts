export type DetailRequestToken = Readonly<{
  generation: string
  key: string
  requestId: number
}>

export type DetailRequestCoordinator = {
  generation: string
  nextRequestId: number
  inFlightByKey: Map<string, number>
  loadedKeys: Set<string>
}

export function createDetailRequestCoordinator(generation: string): DetailRequestCoordinator {
  return {
    generation,
    nextRequestId: 0,
    inFlightByKey: new Map(),
    loadedKeys: new Set(),
  }
}

export function beginDetailRequest(
  coordinator: DetailRequestCoordinator,
  key: string,
): DetailRequestToken | null {
  if (coordinator.loadedKeys.has(key) || coordinator.inFlightByKey.has(key)) {
    return null
  }

  coordinator.nextRequestId += 1
  const requestId = coordinator.nextRequestId
  coordinator.inFlightByKey.set(key, requestId)
  return { generation: coordinator.generation, key, requestId }
}

/**
 * Completes a request only when it is still the latest request for its key.
 * Failed requests deliberately remain unloaded so a later tab activation or
 * explicit retry can start a fresh request.
 */
export function completeDetailRequest(
  coordinator: DetailRequestCoordinator,
  request: DetailRequestToken,
  succeeded: boolean,
): boolean {
  if (
    request.generation !== coordinator.generation ||
    coordinator.inFlightByKey.get(request.key) !== request.requestId
  ) {
    return false
  }

  coordinator.inFlightByKey.delete(request.key)
  if (succeeded) {
    coordinator.loadedKeys.add(request.key)
  } else {
    coordinator.loadedKeys.delete(request.key)
  }
  return true
}

export function isDetailRequestLoaded(
  coordinator: DetailRequestCoordinator,
  key: string,
): boolean {
  return coordinator.loadedKeys.has(key)
}
