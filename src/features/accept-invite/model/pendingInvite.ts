const KEY = 'nishtyak:pending-invite'
let memoryToken: string | null = null

export function rememberInvite(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, '')).get('token')
  if (token !== null && !/^[A-Za-z0-9_-]{43}$/.test(token)) {
    clearPendingInvite()
    return null
  }
  if (token) {
    memoryToken = token
    try { sessionStorage.setItem(KEY, token) } catch { /* Keep it for this page when storage is unavailable. */ }
  }
  try { return memoryToken ?? sessionStorage.getItem(KEY) } catch { return memoryToken }
}

export function clearPendingInvite() {
  memoryToken = null
  try { sessionStorage.removeItem(KEY) } catch { /* Storage may be disabled. */ }
}
