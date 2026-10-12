/** A UI read must eventually offer recovery. This does not retry a request or
 * declare a persisted background task failed. Keep the deadline through body read. */
export async function readWithDeadline<T>(read: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal | null, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController()
  let timedOut = false
  const cancel = () => controller.abort(signal?.reason)
  if (signal?.aborted) cancel()
  else signal?.addEventListener('abort', cancel, { once: true })
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, timeoutMs)
  try {
    return await read(controller.signal)
  } catch (error) {
    if (timedOut) throw new Error('读取超时，请重试。后台任务不会因此停止。')
    throw error
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
  }
}
