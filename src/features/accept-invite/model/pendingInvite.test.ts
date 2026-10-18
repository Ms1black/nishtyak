import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { clearPendingInvite, rememberInvite } from './pendingInvite'

const token = 'a'.repeat(43)

describe('invitation continuation across authentication', () => {
  let data: Map<string, string>
  beforeEach(() => {
    data = new Map()
    vi.stubGlobal('sessionStorage', { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => data.set(key, value), removeItem: (key: string) => data.delete(key) })
    clearPendingInvite()
  })
  afterEach(() => vi.unstubAllGlobals())
  it('keeps a fragment token in this tab when login removes it from the URL', () => {
    expect(rememberInvite(`#token=${token}`)).toBe(token)
    expect(rememberInvite('')).toBe(token)
    expect(data.get('nishtyak:pending-invite')).toBe(token)
    clearPendingInvite()
    expect(rememberInvite('')).toBeNull()
  })
  it('does not retain malformed invitation tokens', () => {
    expect(rememberInvite('#token=bad')).toBeNull()
    expect(rememberInvite('#token=' + 'b'.repeat(42))).toBeNull()
    expect(rememberInvite('#token=' + 'b'.repeat(44))).toBeNull()
    expect(data.size).toBe(0)
  })
})
