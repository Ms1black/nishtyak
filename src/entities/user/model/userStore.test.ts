import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { resetIntegrationSession } from '@/shared/api'
import { useUserStore } from './userStore'

const { getSession, me, login, register, logout, update } = vi.hoisted(() => ({ getSession: vi.fn(), me: vi.fn(), login: vi.fn(), register: vi.fn(), logout: vi.fn(), update: vi.fn() }))

vi.mock('@/shared/api', async (original) => ({
  ...await original<typeof import('@/shared/api')>(),
  getIntegrationSession: getSession,
}))
vi.mock('../api', () => ({ userApi: { me, login, register, logout, update } }))

describe('shared workspace user session', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    resetIntegrationSession()
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
  it('registers an account and clears its identity on logout', async () => {
    const account = { id: 'new', name: 'Researcher', email: 'new@example.org', avatarUrl: null }
    register.mockResolvedValue(account)
    getSession.mockResolvedValue({ mode: 'accounts', user: account, team: { id: 'personal', name: 'Personal' } })
    logout.mockResolvedValue(undefined)
    const store = useUserStore()
    const credentials = { name: account.name, email: account.email, password: 'long password phrase' }
    await store.register(credentials)
    expect(register).toHaveBeenCalledExactlyOnceWith(credentials)
    expect(store.isAuthenticated).toBe(true)
    expect(store.currentUser.id).toBe('new')
    await store.logout()
    expect(store.isAuthenticated).toBe(false)
    expect(store.currentUser).toEqual({ id: '', name: '', email: '', avatarUrl: null })
  })

  it('uses the account session directly without an extra profile request', async () => {
    getSession.mockResolvedValue({ mode: 'accounts', user: { id: 'account', name: 'Account', email: 'a@example.org' }, team: { id: 'personal', name: 'Personal' } })
    const store = useUserStore()
    await store.ensureSession()
    expect(store.currentUser.id).toBe('account')
    expect(store.isAuthenticated).toBe(true)
    expect(me).not.toHaveBeenCalled()
  })

  it('does not restore a previous profile when an update finishes after logout', async () => {
    getSession.mockResolvedValue({ mode: 'accounts', user: { id: 'alice', name: 'Alice', email: 'alice@example.org' }, team: { id: 'personal', name: 'Personal' } })
    let finish!: (value: unknown) => void
    update.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const store = useUserStore()
    await store.ensureSession()
    const updating = store.updateName('Private name')
    store.markAnonymous()
    finish({ id: 'alice', name: 'Private name', email: 'alice@example.org', avatarUrl: null })
    await updating
    expect(store.currentUser.name).toBe('')
    expect(store.status).toBe('anonymous')
  })

})
