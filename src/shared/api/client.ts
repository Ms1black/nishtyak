const BASE_URL = import.meta.env.VITE_API_URL ?? '/api'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isFormData = init.body instanceof FormData
  const response = await fetch(`${BASE_URL}${path}`, {
    credentials: 'include',
    signal: AbortSignal.timeout(90_000),
    ...init,
    headers: isFormData ? init.headers : { 'Content-Type': 'application/json', ...init.headers },
  })

  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText)
    let message = text || response.statusText
    let code: string | undefined
    try {
      const error = JSON.parse(text) as { message?: unknown; code?: unknown }
      if (typeof error.message === 'string') message = error.message
      if (typeof error.code === 'string') code = error.code
    } catch {
      // A reverse proxy can return a plain-text error.
    }
    if (response.status === 401 && code === 'PREVIEW_AUTH_REQUIRED') {
      const next = `${window.location.pathname}${window.location.search}`
      window.location.assign(`/preview/access?next=${encodeURIComponent(next)}`)
    } else if (response.status === 401 && !path.startsWith('/me') && !path.startsWith('/session') && !path.startsWith('/auth/')) {
      onUnauthorized?.()
    }
    throw new ApiError(response.status, message, code)
  }

  if (response.status === 204) return undefined as T

  const contentType = response.headers.get('content-type') ?? ''
  return (contentType.includes('application/json') ? response.json() : response.blob()) as Promise<T>
}

function encodeBody(body: unknown): BodyInit | undefined {
  if (body === undefined) return undefined
  if (body instanceof FormData) return body
  return JSON.stringify(body)
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: encodeBody(body) }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body: encodeBody(body) }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body: encodeBody(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
