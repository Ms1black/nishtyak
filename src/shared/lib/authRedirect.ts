export function safeAuthRedirect(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u0020]/.test(value)) return '/upload'
  try {
    const parsed = new URL(value, 'https://workspace.invalid')
    if (parsed.origin !== 'https://workspace.invalid' || ['/login', '/register'].includes(parsed.pathname)) return '/upload'
    return parsed.pathname + parsed.search + parsed.hash
  } catch {
    return '/upload'
  }
}
