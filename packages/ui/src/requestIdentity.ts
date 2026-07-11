export type RequestSequenceRef = { current: number }

export type RequestIdentity = {
  sequence: number
  resourceId: string
}

export function beginRequest(sequenceRef: RequestSequenceRef, resourceId: string): RequestIdentity {
  sequenceRef.current += 1
  return { sequence: sequenceRef.current, resourceId }
}

export function invalidateRequests(sequenceRef: RequestSequenceRef) {
  sequenceRef.current += 1
}

export function isRequestCurrent(
  sequenceRef: RequestSequenceRef,
  request: RequestIdentity,
  currentResourceId: string,
  responseResourceId: string = request.resourceId,
) {
  return (
    request.sequence === sequenceRef.current &&
    request.resourceId === currentResourceId &&
    responseResourceId === request.resourceId
  )
}
