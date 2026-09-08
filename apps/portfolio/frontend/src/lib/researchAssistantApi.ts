const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export function researchAssistantUrl(path: string) {
  return `${API_BASE_URL}/api/research-assistant${path.replace(/^\/api\/research|^\/research/, '')}`
}

// Conversations are live, account-bound data; polling must not reuse the portfolio snapshot cache.
export async function requestResearchAssistant<T>(path: string, init?: RequestInit): Promise<T> {
  const multipart = init?.body instanceof FormData
  const response = await fetch(researchAssistantUrl(path), {
    ...init,
    credentials: 'include',
    headers: {
      ...(!multipart ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('studio-auth-changed'))
    const body = await response.text()
    let message = body || '研究助手暂时无法连接，请稍后重试。'
    try {
      const detail = (JSON.parse(body) as { detail?: unknown }).detail
      if (typeof detail === 'string') message = detail
    } catch { /* Keep a non-JSON service error readable. */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const readResearchAssistant = <T,>(path: string) => requestResearchAssistant<T>(path)
export const writeResearchAssistant = <T,>(path: string, value: unknown, method = 'POST') =>
  requestResearchAssistant<T>(path, { method, body: JSON.stringify(value) })

export function uploadResearchAssistantFile<T>(topicId: string, file: File): Promise<T> {
  const body = new FormData()
  body.append('file', file)
  return requestResearchAssistant(`/research/topics/${encodeURIComponent(topicId)}/files`, { method: 'POST', body })
}
