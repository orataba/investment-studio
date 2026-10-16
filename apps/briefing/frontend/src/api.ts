const api = (import.meta.env.VITE_API_BASE_URL || '/api/briefing').replace(/\/$/, '')
export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${api}${path}`, { credentials: 'include', ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`
    const requestId = response.headers?.get('X-Request-ID')
    throw new Error(requestId ? `${message} [request_id=${requestId}]` : message)
  }
  return response.json()
}
