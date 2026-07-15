import { useEffect, useRef, useState } from 'react'

export default function usePerformanceResource<T>({
  enabled,
  resourceKey,
  load,
  fallbackError,
}: {
  enabled: boolean
  resourceKey: string
  load: () => Promise<T>
  fallbackError: string
}) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const activeResourceKey = useRef(resourceKey)

  useEffect(() => {
    const resourceChanged = activeResourceKey.current !== resourceKey
    activeResourceKey.current = resourceKey

    if (!enabled) {
      setData(null)
      setLoading(false)
      setError(null)
      return
    }

    let cancelled = false
    if (resourceChanged) {
      setData(null)
    }
    setLoading(true)
    setError(null)

    load()
      .then((response) => {
        if (!cancelled) {
          setData(response)
        }
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setData(null)
          setError(requestError instanceof Error ? requestError.message : fallbackError)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [enabled, fallbackError, load, resourceKey])

  const resourceChanged = activeResourceKey.current !== resourceKey
  return {
    data: enabled && !resourceChanged ? data : null,
    loading: enabled && resourceChanged ? true : loading,
    error: resourceChanged ? null : error,
  }
}
