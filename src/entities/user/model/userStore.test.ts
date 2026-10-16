import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useUserStore } from './userStore'

const { getSession, me } = vi.hoisted(() => ({ getSession: vi.fn(), me: vi.fn() }))

vi.mock('@/shared/api', async (original) => ({
  ...await original<typeof import('@/shared/api')>(),
  getIntegrationSession: getSession,
}))
vi.mock('../api', () => ({ userApi: { me } }))

describe('shared workspace user session', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setActivePinia(createPinia())
  })

  it.each(['local', 'preview'])('shows the server identity in %s mode without a mock account', async (mode) => {
    getSession.mockResolvedValue({
      mode,
      user: { id: 'reviewer', name: 'Review workspace', email: '' },
      team: { id: 'workspace', name: 'Shared workspace' },
    })
    const store = useUserStore()
    await store.ensureSession()
    expect(store.status).toBe(mode)
    expect(store.currentUser).toEqual({ id: 'reviewer', name: 'Review workspace', email: '', avatarUrl: null })
    expect(store.isAuthenticated).toBe(false)
    expect(me).not.toHaveBeenCalled()
  })

  it('continues to use the account backend in proxy mode', async () => {
    getSession.mockResolvedValue({ mode: 'proxy' })
    me.mockResolvedValue({ id: 'account', name: 'User', email: 'user@example.org', avatarUrl: null })
    const store = useUserStore()
    await store.ensureSession()
    expect(store.currentUser.id).toBe('account')
    expect(store.isAuthenticated).toBe(true)
  })
})
